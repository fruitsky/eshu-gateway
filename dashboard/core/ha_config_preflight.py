"""F11: the dashboard-side preflight for `ha_config_write`.

H4's decision is a dependency-free **structural scan** (`core/ha_config_yaml.py`
in the design): comments are stripped, a top-level key is the text before the
first unquoted `:` at indentation 0, values are never interpreted, and HA's
tags are collected wherever they appear. It is not a general YAML parser;
`ha core check` after the write remains the validity check, with the
automatic restore.

The scan is strict where the design is:
* `!include`/`!include_dir_*` targets are decoded, then limited to the read
  tool's own scope (AT1) and must be relative (BC1);
* `!env_var`, a `%TAG` directive and a verbatim tag are refused;
* `secrets.yaml` is never written or included;
* the risky top-level keys are named for the approval card (H4).
"""
import difflib
import hashlib
import posixpath
import re

# The add-on's view (W4: /config is a symlink to it).
ROOT = '/homeassistant'
MAX_CONTENT = 256 * 1024
WRITE_SUFFIXES = ('.yaml', '.yml')
SECRETS = 'secrets.yaml'

DIR_TAGS = ('include_dir_merge_named', 'include_dir_merge_list',
            'include_dir_named', 'include_dir_list')
OTHER_TAGS = ('env_var', 'secret', 'input')
_TAG_RE = re.compile(r'!(%s|include|%s)\b' % (
    '|'.join(DIR_TAGS), '|'.join(OTHER_TAGS)))
_BAD_TAG = re.compile(r'(^%TAG\b)|!<', re.MULTILINE)
_TAB_INDENT = re.compile(r'^[ ]*\t', re.MULTILINE)

# H4: top-level keys that turn a config file into code or an auth surface.
RISKY_KEYS = ('shell_command', 'command_line', 'python_script',
              'rest_command', 'http', 'packages')
RISKY_HOMEASSISTANT = 'auth_providers'

_DOUBLE_ESCAPES = {'n': '\n', 't': '\t', 'r': '\r', '0': '\0',
                   '\\': '\\', '"': '"', '/': '/', "'": "'"}


class ConfigError(ValueError):
    """A preflight refusal with a stable code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# ── paths ──────────────────────────────────────────────────────────────

def to_root_path(path):
    """Normalise `/config/...` or `/homeassistant/...` to the add-on's view."""
    text = str(path or '').strip()
    if text == '/config':
        text = ROOT
    elif text.startswith('/config/'):
        text = ROOT + '/' + text[len('/config/'):]
    return text


def check_write_path(path):
    """The target file a write may touch; raises ConfigError."""
    text = to_root_path(path)
    if not text.startswith(ROOT + '/'):
        raise ConfigError('config_path_refused',
                          'the path must be under /config')
    rel = text[len(ROOT) + 1:]
    parts = rel.split('/')
    if not rel or any(part in ('', '.', '..') or part.startswith('.')
                      for part in parts):
        raise ConfigError('config_path_refused',
                          'the path has an empty or dot segment')
    if not rel.endswith(WRITE_SUFFIXES):
        raise ConfigError('config_path_refused', 'only .yaml/.yml may be written')
    if '/' in rel and not rel.startswith('packages/'):
        raise ConfigError('config_path_refused',
                          'only top-level files or packages/ may be written')
    if parts[-1] == SECRETS:
        raise ConfigError('config_secret_refused',
                          'secrets.yaml is never written by Eshu')
    return text


# ── the structural scan ────────────────────────────────────────────────

def _decode_double(body):
    out = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch != '\\':
            out.append(ch)
            i += 1
            continue
        nxt = body[i + 1] if i + 1 < len(body) else ''
        if nxt == 'x' and re.fullmatch(r'[0-9A-Fa-f]{2}', body[i + 2:i + 4]):
            out.append(chr(int(body[i + 2:i + 4], 16)))
            i += 4
        elif nxt == 'u' and re.fullmatch(r'[0-9A-Fa-f]{4}', body[i + 2:i + 6]):
            out.append(chr(int(body[i + 2:i + 6], 16)))
            i += 6
        elif nxt in _DOUBLE_ESCAPES:
            out.append(_DOUBLE_ESCAPES[nxt])
            i += 2
        else:
            out.append(nxt)
            i += 2
    return ''.join(out)


def _decode_scalar(text):
    text = str(text or '').strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in '"\'':
        body = text[1:-1]
        return _decode_double(body) if text[0] == '"' else body.replace("''", "'")
    return text


def _strip_comment(line):
    out = []
    quote = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            out.append(ch)
            if ch == '\\' and quote == '"' and i + 1 < len(line):
                out.append(line[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in '"\'':
            quote = ch
            out.append(ch)
        elif ch == '#' and (i == 0 or line[i - 1] in ' \t'):
            break
        else:
            out.append(ch)
        i += 1
    return ''.join(out)


def _first_colon(line):
    """The index of the first unquoted `:` or -1."""
    quote = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == '\\' and quote == '"' and i + 1 < len(line):
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in '"\'':
            quote = ch
        elif ch == ':':
            return i
        i += 1
    return -1


def _tag_args(line):
    """(kind, raw argument) for every HA tag on a line, outside quotes."""
    found = []
    quote = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == '\\' and quote == '"' and i + 1 < len(line):
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in '"\'':
            quote = ch
            i += 1
            continue
        match = _TAG_RE.match(line, i)
        if match:
            kind = match.group(1)
            rest = line[match.end():]
            argument, consumed = _read_argument(rest)
            if argument is not None:
                found.append(('file' if kind == 'include'
                              else 'dir' if kind in DIR_TAGS
                              else kind, argument))
            i = match.end() + consumed
            continue
        i += 1
    return found


def _read_argument(rest):
    """The scalar after a tag, and the characters consumed."""
    lead = len(rest) - len(rest.lstrip())
    rest = rest[lead:]
    if not rest or rest.startswith('#'):
        return None, lead
    if rest[0] in '"\'':
        quote = rest[0]
        i = 1
        while i < len(rest):
            if rest[i] == '\\' and quote == '"' and i + 1 < len(rest):
                i += 2
                continue
            if rest[i] == quote:
                return _decode_scalar(rest[:i + 1]), lead + i + 1
            i += 1
        return _decode_scalar(rest), lead + len(rest)
    match = re.match(r'[^\s#]+', rest)
    if not match:
        return None, lead
    return _decode_scalar(match.group(0)), lead + match.end()


def scan(content):
    """(top-level keys, tags, has a tab indent, homeassistant.auth_providers).

    The scan tracks indentation only far enough to skip a block scalar and to
    see the `auth_providers` key inside a top-level `homeassistant:` block.
    """
    keys = []
    tags = []
    home_auth = False
    home_indent = None
    in_block = False
    block_indent = None
    for raw in str(content or '').splitlines():
        line = _strip_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(' '))
        if in_block:
            if indent > block_indent:
                continue
            in_block = False
        stripped = line.lstrip(' ')
        if stripped.startswith('---') or stripped.startswith('...'):
            continue
        colon = _first_colon(line)
        if indent == 0:
            if colon >= 0:
                key = _decode_scalar(line[:colon])
                if key:
                    keys.append(key)
                value = line[colon + 1:].strip()
                home_indent = indent if key == 'homeassistant' else None
                if value.startswith('|') or value.startswith('>'):
                    in_block = True
                    block_indent = indent
                    continue
        elif home_indent is not None and colon >= 0:
            if _decode_scalar(line[:colon]).strip() == RISKY_HOMEASSISTANT:
                home_auth = True
        tags.extend(_tag_args(line))
    return (keys, tags, bool(_TAB_INDENT.search(str(content or ''))),
            home_auth)


# ── includes ───────────────────────────────────────────────────────────

def _check_target(kind, target, including_path):
    target = str(target or '').strip()
    if not target:
        raise ConfigError('config_include_refused', 'an empty include target')
    if posixpath.isabs(target):
        # BC1: HA Core loads the same file with its config at /config, so an
        # absolute path names a different tree there. One view, relative only.
        raise ConfigError('config_include_refused',
                          'an include target must be relative')
    for part in target.split('/'):
        if part in ('', '.', '..') or part.startswith('.'):
            raise ConfigError('config_include_refused',
                              'an include target has a dot segment')
    base = posixpath.dirname(including_path)
    resolved = posixpath.normpath(posixpath.join(base, target))
    if not resolved.startswith(ROOT + '/'):
        raise ConfigError('config_include_refused',
                          'an include target leaves the config tree')
    if kind == 'file':
        if not target.endswith(WRITE_SUFFIXES):
            raise ConfigError('config_include_refused',
                              'a file include must be .yaml or .yml')
        if posixpath.basename(resolved) == SECRETS:
            raise ConfigError('config_secret_include_refused',
                              'secrets.yaml cannot be included')
        parent = posixpath.dirname(resolved)
        if not (parent == ROOT or parent == ROOT + '/packages'
                or parent.startswith(ROOT + '/packages/')):
            raise ConfigError('config_include_refused',
                              'an include may reach only top-level files or '
                              'packages/')
    else:
        if resolved == ROOT:
            raise ConfigError('config_include_refused',
                              'a directory include cannot be the config root')
        if not (resolved == ROOT + '/packages'
                or resolved.startswith(ROOT + '/packages/')):
            raise ConfigError('config_include_refused',
                              'a directory include must be packages/ or below')
    return resolved


def _risky_keys(keys):
    found = []
    for key in RISKY_KEYS:
        if key in keys:
            found.append(key)
    return found


def validate_write(path, content):
    """The preflight for one proposed write; raises ConfigError.

    Returns {'path', 'sha256', 'size', 'risky', 'includes'}.
    """
    target = check_write_path(path)
    text = content if isinstance(content, str) else str(content or '')
    size = len(text.encode('utf-8'))
    if size > MAX_CONTENT:
        raise ConfigError('config_too_large',
                          'the content is over %d bytes' % MAX_CONTENT)
    if _BAD_TAG.search(text):
        raise ConfigError('config_tag_refused',
                          'a %TAG directive or a verbatim tag is not allowed')
    keys, tags, tab_indent, home_auth = scan(text)
    if tab_indent:
        raise ConfigError('config_yaml_invalid',
                          'YAML indentation cannot be a tab')
    if any(kind == 'env_var' for kind, _arg in tags):
        raise ConfigError('config_env_var_refused',
                          '!env_var is not allowed in config files')
    includes = []
    for kind, argument in tags:
        if kind in ('secret', 'input', 'env_var'):
            continue
        resolved = _check_target(kind, argument, target)
        includes.append({'kind': kind, 'target': argument,
                         'resolved': resolved})
    risky = _risky_keys(keys)
    if home_auth:
        risky.append('homeassistant.' + RISKY_HOMEASSISTANT)
    return {'path': target,
            'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
            'size': size,
            'risky': risky,
            'includes': includes}


def unified_diff(path, old, new):
    """The dry_run/card diff; the content itself is not stored."""
    old_lines = (old or '').splitlines(keepends=True)
    new_lines = (new or '').splitlines(keepends=True)
    return ''.join(difflib.unified_diff(
        old_lines, new_lines, fromfile=str(path), tofile=str(path)))
