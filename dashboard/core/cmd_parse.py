"""Command breakdown engine (chunk F1).

`breakdown(command)` turns one command string into reviewable steps: a list of
`read` / `write` / `unknown` items, the section markers the agent inserted, a
one-line summary and the flags F2/F4/F7 build on.

The module is pure and side-effect free: it never executes anything, never
touches the network and never reads the filesystem. It is also total — any
string (including broken shell syntax) returns a dict; parsing failures set
``complete`` to ``false`` and the affected steps to ``unknown``.

Design notes for the reviewer:

* `shlex.shlex(posix=True, punctuation_chars=True)` does the tokenising, as
  chunk rule 1 requires. Newline is added to the punctuation set so a newline
  outside quotes is a separator while a newline inside quotes is not (rule 1).
* Wrappers (`sudo`, `timeout`, `pct exec`, `docker exec`, `sh -c`, …) are
  unwrapped before classification; each unwrap lands in the step's ``context``
  list (outermost first), which is what F2 renders as prefix chips (rule 3).
* Anything the engine cannot see through is ``unknown``. `read` is the only
  answer that can ever auto-run anything (F4), so the default is never `read`.
"""
import copy
import functools
import os
import re
import shlex

from core.cmd_codec import decode_preview, detect_encoded_exec
from core.cmd_risk import get_cmd_risk
from core import cmd_rules

VERSION = 1
MAX_WRAP_DEPTH = 3
MAX_SECTION = 40
PUNCT_CHARS = '();<>|&\n'

READ = cmd_rules.READ
WRITE = cmd_rules.WRITE
UNKNOWN = cmd_rules.UNKNOWN

_SEPS = {'&&', '||', ';', '|', '&', '\n'}
_REDIRS = {'>', '>>', '>|', '&>', '>&', '<', '<<', '<<<', '<&'}
_WRITE_REDIRS = {'>', '>>', '&>', '>|'}
_SHELLS = {'sh', 'bash', 'dash', 'zsh', 'ksh', 'ash'}
_SCRIPT_READERS = _SHELLS | {'python', 'python3', 'perl', 'ruby'}

# Shell keywords are structure, not commands. A `for`/`case` header runs
# nothing (only its `$( … )` substitutions matter); `if`/`while` keep their
# condition because the condition *is* an executed command.
_ASSIGN_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')

_KEYWORDS_DROP = {'for', 'select', 'case', 'function'}
_KEYWORDS_TRIM = {'if', 'elif', 'while', 'until', 'then', 'do', 'else',
                  'done', 'fi', 'esac', '{', '}', '!', 'time'}

# `^[=\-#*\s]*[A-Za-z0-9 _./:-]{1,40}[=\-#*\s]*$` from chunk rule 2, spelled
# out so the label can be extracted from it.
_SECTION_RE = re.compile(r'^[=\-#*\s]*[A-Za-z0-9 _./:\-]{1,40}[=\-#*\s]*$')
_SECTION_BODY_RE = re.compile(r'^[A-Za-z0-9 _./:\-]{1,40}$')
_SECTION_CHARS = '=#*-'


class _ParseState:
    """Mutable facts collected while walking one command."""

    __slots__ = ('complete', 'flags', 'inner_targets', 'sections', 'section')

    def __init__(self):
        self.complete = True
        self.flags = []
        self.inner_targets = []
        self.sections = []
        self.section = ''

    def flag(self, name):
        if name not in self.flags:
            self.flags.append(name)

    def add_inner(self, target):
        if target and target not in self.inner_targets:
            self.inner_targets.append(target)

    def set_section(self, name):
        self.section = name
        if name not in self.sections:
            self.sections.append(name)


class _Statement:
    """One simple command (or pipeline member) plus its redirections."""

    __slots__ = ('argv', 'op', 'redirs', 'text', 'heredoc')

    def __init__(self, argv, op, redirs, text, heredoc):
        self.argv = argv
        self.op = op
        self.redirs = redirs
        self.text = text
        self.heredoc = heredoc


# ── Tokenising ──────────────────────────────────────────────────────────────


def _base(token):
    return os.path.basename(token) if token else ''


def _quote(token):
    """Shell-quote a token for display; keep substitutions readable."""
    if '$(' in token or '`' in token:
        return token
    return shlex.quote(token)


def _display(argv):
    return ' '.join(_quote(tok) for tok in argv)


def _is_punct_token(token):
    return bool(token) and all(ch in PUNCT_CHARS for ch in token)


def _split_punct(run):
    """Split one shlex punctuation run into single operators."""
    out = []
    i = 0
    n = len(run)
    while i < n:
        ch = run[i]
        nxt = run[i + 1] if i + 1 < n else ''
        if ch == '&' and nxt == '&':
            out.append('&&')
            i += 2
        elif ch == '|' and nxt == '|':
            out.append('||')
            i += 2
        elif ch == '|' and nxt == '&':
            out.append('|')
            i += 2
        elif ch == '&' and nxt == '>':
            out.append('&>')
            i += 2
        elif ch == '>' and nxt == '>':
            out.append('>>')
            i += 2
        elif ch == '>' and nxt == '&':
            out.append('>&')
            i += 2
        elif ch == '>' and nxt == '|':
            out.append('>|')
            i += 2
        elif ch == '<' and nxt == '<' and run[i + 2:i + 3] == '<':
            out.append('<<<')
            i += 3
        elif ch == '<' and nxt == '<':
            out.append('<<')
            i += 2
        elif ch == '<' and nxt == '&':
            out.append('<&')
            i += 2
        elif ch in '><':
            out.append(ch)
            i += 1
        elif ch == '|':
            out.append('|')
            i += 1
        elif ch == '&':
            out.append('&')
            i += 1
        elif ch == ';':
            out.append(';')
            i += 1
        elif ch == '\n':
            out.append('\n')
            i += 1
        elif ch in '()':
            out.append(ch)
            i += 1
        else:
            i += 1
    return out


def _matching_paren(tokens, start):
    """Index of the ')' matching tokens[start] == '(', or None."""
    depth = 0
    i = start
    while i < len(tokens):
        tok = tokens[i]
        if tok == '(':
            depth += 1
        elif tok == ')':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _tokens_text(tokens):
    """Rebuild re-parseable text from raw shlex tokens (quote words only)."""
    parts = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if _is_punct_token(tok):
            parts.append(tok)
            i += 1
            continue
        if tok == '$' and i + 1 < len(tokens) and tokens[i + 1] == '(':
            end = _matching_paren(tokens, i + 1)
            if end is not None:
                parts.append('$(' + _tokens_text(tokens[i + 2:end]) + ')')
                i = end + 1
                continue
        if tok.endswith('$') and i + 1 < len(tokens) and tokens[i + 1] == '(':
            end = _matching_paren(tokens, i + 1)
            if end is not None:
                parts.append(tok[:-1] + '$(' + _tokens_text(tokens[i + 2:end]) + ')')
                i = end + 1
                continue
        parts.append(shlex.quote(tok))
        i += 1
    return ' '.join(parts)


def _normalise(tokens):
    """Split shlex's punctuation runs (`);`, `&&`, `))`) into single operators.

    shlex groups a run of punctuation characters into one token, so `);` could
    otherwise never match a closing parenthesis.
    """
    out = []
    for token in tokens:
        if _is_punct_token(token):
            out.extend(_split_punct(token))
        else:
            out.append(token)
    return out


def _atomise(tokens):
    """Collapse `$( … )` groups into one word atom so `;` inside cannot split."""
    tokens = _normalise(tokens)
    atoms = []
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if _is_punct_token(tok):
            for op in _split_punct(tok):
                atoms.append(op)
            i += 1
            continue
        if tok == '$' and i + 1 < n and tokens[i + 1] == '(':
            end = _matching_paren(tokens, i + 1)
            if end is not None:
                atoms.append(('w', '$(' + _tokens_text(tokens[i + 2:end]) + ')'))
                i = end + 1
                continue
        if tok.endswith('$') and i + 1 < n and tokens[i + 1] == '(':
            end = _matching_paren(tokens, i + 1)
            if end is not None:
                atoms.append(('w', tok[:-1] + '$(' + _tokens_text(tokens[i + 2:end]) + ')'))
                i = end + 1
                continue
        atoms.append(('w', tok))
        i += 1
    return atoms


def _lex(text):
    lexer = shlex.shlex(text, posix=True, punctuation_chars=PUNCT_CHARS)
    lexer.whitespace = ' \t\r'
    lexer.whitespace_split = True
    lexer.commenters = '#'
    return list(lexer)


def _skip_heredoc_body(atoms, i, delim):
    """Skip a heredoc body; return the index past the delimiter, or None."""
    at_line_start = True
    n = len(atoms)
    while i < n:
        atom = atoms[i]
        if isinstance(atom, tuple):
            if at_line_start and atom[1] == delim:
                return i + 1
            at_line_start = False
        elif atom == '\n':
            at_line_start = True
        i += 1
    return None


def _statements(atoms, state):
    """Group atoms into simple commands, honouring redirections and heredocs."""
    stmts = []
    argv = []
    redirs = []
    parts = []
    heredocs = []
    op = ''
    pending_redir = None
    pending_fd = None
    i = 0
    n = len(atoms)

    while i < n:
        atom = atoms[i]

        if pending_redir is not None:
            if isinstance(atom, tuple):
                target = atom[1]
                redir_op = pending_redir
                pending_redir = None
                if redir_op in ('<<', '<<-'):
                    if target.startswith('-'):
                        # `<<-EOF`: shlex splits the tab-stripping dash off.
                        redir_op, target = '<<-', target[1:]
                    heredocs.append(target)
                    parts.append(f'{redir_op} {shlex.quote(target)}')
                elif redir_op in ('>&', '<&') and (target.isdigit() or target == '-'):
                    # fd duplication (2>&1) — not a file write
                    parts.append(f'{pending_fd or ""}{redir_op}{target}')
                else:
                    redirs.append((redir_op, target, pending_fd))
                    parts.append(f'{pending_fd or ""}{redir_op} {shlex.quote(target)}')
                pending_fd = None
                i += 1
                continue
            # A redirection with no target: stop pretending we understood it.
            state.complete = False
            pending_redir = None
            pending_fd = None
            continue

        if isinstance(atom, tuple):
            word = atom[1]
            if (word.isdigit() and i + 1 < n
                    and isinstance(atoms[i + 1], str) and atoms[i + 1] in _REDIRS):
                pending_fd = word
                i += 1
                continue
            argv.append(word)
            parts.append(_quote(word))
            i += 1
            continue

        if atom == ')':
            # A closing parenthesis ends the current simple command. `case`
            # pattern terminators (`a)`) land here too, so a `case` body can
            # never be swallowed by its header.
            if argv or redirs:
                stmts.append(_Statement(list(argv), op, list(redirs),
                                        ' '.join(parts), bool(heredocs)))
                argv, redirs, parts = [], [], []
                op = ''
            i += 1
            continue

        if atom in _REDIRS:
            pending_redir = atom
            i += 1
            continue

        if atom in _SEPS:
            heredoc_here = bool(heredocs)
            if argv or redirs:
                stmts.append(_Statement(list(argv), op, list(redirs),
                                        ' '.join(parts), heredoc_here))
                argv, redirs, parts = [], [], []
                op = ''
            if heredoc_here:
                for delim in heredocs:
                    i = _skip_heredoc_body(atoms, i, delim)
                    if i is None:
                        # The closing delimiter never came: we cannot tell
                        # which of the remaining lines the shell would run.
                        state.complete = False
                        return stmts
                heredocs = []
                continue
            if not stmts and not argv and not redirs:
                i += 1
                continue
            if op == '' or op == '\n':
                op = atom
            i += 1
            continue

        # A parenthesis is structural, not a command.
        i += 1

    if pending_redir is not None:
        # A redirection with no target (`echo hi >`): the shell would refuse it.
        state.complete = False
    if argv or redirs:
        stmts.append(_Statement(list(argv), op, list(redirs),
                                ' '.join(parts), bool(heredocs)))
    return stmts


# ── Substitutions ───────────────────────────────────────────────────────────


def _strip_substitutions(token):
    """Return (leftover, [inner texts], unparsed)."""
    texts = []
    leftover = []
    unparsed = False
    i = 0
    n = len(token)
    while i < n:
        ch = token[i]
        if ch == '`':
            end = token.find('`', i + 1)
            if end < 0:
                unparsed = True
                leftover.append(token[i:])
                break
            texts.append(token[i + 1:end])
            i = end + 1
            continue
        if ch == '$' and i + 1 < n and token[i + 1] == '(':
            depth = 0
            j = i + 1
            while j < n:
                if token[j] == '(':
                    depth += 1
                elif token[j] == ')':
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if depth != 0:
                unparsed = True
                leftover.append(token[i:])
                break
            texts.append(token[i + 2:j])
            i = j + 1
            continue
        leftover.append(ch)
        i += 1
    return ''.join(leftover), texts, unparsed


def _extract_substitutions(argv):
    """Separate command substitutions from the argv they sit in."""
    clean = []
    texts = []
    unparsed = False
    for token in argv:
        if '$(' in token or '`' in token:
            leftover, found, bad = _strip_substitutions(token)
            texts.extend(text for text in found if text.strip())
            unparsed = unparsed or bad
            if leftover:
                clean.append(leftover)
        else:
            clean.append(token)
    return clean, texts, unparsed


# ── Wrappers ────────────────────────────────────────────────────────────────


def _strip_flags(args, value_flags=()):
    """Drop leading flags; `value_flags` also consume the next argument."""
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--':
            return args[i + 1:]
        if not arg.startswith('-') or arg == '-':
            return args[i:]
        base = arg.split('=', 1)[0]
        if base in value_flags and '=' not in arg:
            i += 2
        else:
            i += 1
    return []


def _strip_env_flags(args):
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ('-i', '--ignore-environment', '-0', '--null', '-v', '--debug'):
            i += 1
            continue
        if arg in ('-u', '--unset'):
            i += 2
            continue
        if arg.startswith('--unset='):
            i += 1
            continue
        if arg.startswith('-'):
            i += 1
            continue
        if '=' in arg:
            i += 1
            continue
        break
    return args[i:]


def _shell_script(argv):
    """(script, label) for `sh -c '…'`, or (None, None)."""
    verb = _base(argv[0])
    args = argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--':
            i += 1
            break
        if arg.startswith('-') and not arg.startswith('--'):
            if 'c' in arg[1:]:
                script = args[i + 1] if i + 1 < len(args) else None
                return script, f'{verb} -c'
            i += 1
            continue
        if arg.startswith('--'):
            i += 1
            continue
        break
    return None, None


_SU_SUBCOMMAND_FLAGS = {'-c', '--command'}
_SU_VALUE_FLAGS = {'-g', '--group', '-G', '--supp-group', '-s', '--shell',
                   '-w', '--whitelist-environment', '-u', '--user'}


def _su_script(argv):
    """(script, label) for `su [-] user -c '…'`, or (None, None)."""
    args = argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in _SU_SUBCOMMAND_FLAGS:
            script = args[i + 1] if i + 1 < len(args) else None
            return script, 'su'
        if arg.startswith('--command='):
            return arg.split('=', 1)[1], 'su'
        if arg.startswith('-c') and not arg.startswith('--') and len(arg) > 2:
            return arg[2:], 'su'
        if arg in _SU_VALUE_FLAGS:
            i += 2
            continue
        if arg.startswith('-'):
            i += 1
            continue
        i += 1
    return None, None


_VALUE_FLAGS_SUDO = {
    '-u', '--user', '-g', '--group', '-p', '--prompt', '-C', '--close-from',
    '-h', '--host', '-U', '--other-user', '-r', '--role', '-t', '--type',
    '-T', '--command-timeout', '-R', '--chroot', '-D', '--chdir',
}


def _docker_exec(args):
    """(container, command argv) for the arguments after `docker exec`."""
    value_flags = {'-u', '--user', '-w', '--workdir', '-e', '--env',
                   '--env-file', '--detach-keys', '--index'}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--':
            i += 1
            break
        if arg.startswith('-') and arg != '-':
            base = arg.split('=', 1)[0]
            if base in value_flags and '=' not in arg:
                i += 2
            else:
                i += 1
            continue
        break
    if i >= len(args):
        return None
    container = args[i]
    command = args[i + 1:]
    if not command:
        return None
    return container, command


_COMPOSE_GLOBAL_VALUE_FLAGS = {
    '-f', '--file', '-p', '--project-name', '--profile', '--env-file',
    '--project-directory', '--ansi', '--progress', '--parallel',
}


def _docker_compose_exec(args):
    """(service, command argv) for `docker compose … exec …`."""
    rest = _strip_flags(args, _COMPOSE_GLOBAL_VALUE_FLAGS)
    if not rest or rest[0] != 'exec':
        return None
    return _docker_exec(rest[1:])


def _unwrap(argv, state):
    """Strip one wrapper prefix; return (argv, context label, inner target)."""
    verb = _base(argv[0])
    args = argv[1:]

    if verb == 'sudo':
        rest = _strip_flags(args, _VALUE_FLAGS_SUDO)
        if not rest:
            return None
        return rest, 'sudo', ''

    if verb == 'timeout':
        i = 0
        while i < len(args) and args[i].startswith('-'):
            arg = args[i]
            if arg in ('-s', '--signal', '-k', '--kill-after') and i + 1 < len(args):
                i += 2
            else:
                i += 1
        if i >= len(args):
            return None
        duration = args[i]
        rest = args[i + 1:]
        if not rest:
            return None
        return rest, f'timeout {duration}', ''

    if verb == 'nice':
        rest = _strip_flags(args, {'-n', '--adjustment'})
        if not rest:
            return None
        return rest, 'nice', ''

    if verb == 'ionice':
        rest = _strip_flags(args, {'-c', '--class', '-n', '--classdata',
                                   '-p', '--pid', '-P', '--pgid', '-u', '--uid'})
        if not rest:
            return None
        return rest, 'ionice', ''

    if verb == 'env':
        rest = _strip_env_flags(args)
        if not rest:
            return None
        return rest, 'env', ''

    if verb == 'pct':
        if len(args) >= 2 and args[0] == 'exec' and args[1].isdigit():
            rest = args[2:]
            if rest and rest[0] == '--':
                rest = rest[1:]
            if not rest:
                return None
            return rest, f'pct {args[1]}', f'pct:{args[1]}'
        return None

    if verb == 'docker':
        rest = _strip_flags(args, {'-H', '--host', '--context', '--config',
                                   '-c', '--log-level', '-l', '--tlscacert',
                                   '--tlscert', '--tlskey'})
        if rest and rest[0] == 'exec':
            parsed = _docker_exec(rest[1:])
            if parsed:
                container, command = parsed
                return command, f'docker {container}', f'docker:{container}'
        elif rest and rest[0] == 'compose':
            parsed = _docker_compose_exec(rest[1:])
            if parsed:
                service, command = parsed
                return command, f'docker compose {service}', f'docker:{service}'
        return None

    if verb == 'docker-compose':
        parsed = _docker_compose_exec(args)
        if parsed:
            service, command = parsed
            return command, f'docker compose {service}', f'docker:{service}'
        return None

    return None


def _strip_assignments(argv):
    """Drop leading `NAME=value` words (`FOO=bar cmd …`)."""
    i = 0
    while i < len(argv) and _ASSIGN_RE.match(argv[i]):
        i += 1
    return argv[i:]


def _has_wrapper(argv):
    """True when a statement will be re-parsed by the wrapper rules."""
    if not argv:
        return False
    verb = _base(argv[0])
    if verb in _SHELLS or verb == 'su':
        return True
    return _unwrap(argv, _ParseState()) is not None


# ── Steps ───────────────────────────────────────────────────────────────────


def _step(text, op, kind, why, ctx, state):
    return {
        'text': text,
        'op': op or '',
        'kind': kind,
        'why': why,
        'section': state.section,
        'context': list(ctx),
        'elevated': ('sudo' in ctx or 'su' in ctx),
    }


def _redirect_suffix(st):
    out = []
    for redir_op, target, _fd in st.redirs:
        if redir_op in _WRITE_REDIRS:
            out.append(f' {redir_op} {shlex.quote(target)}')
    return ''.join(out)


def _write_targets(st):
    return [(redir_op, target) for redir_op, target, _fd in st.redirs
            if redir_op in _WRITE_REDIRS and target != '/dev/null']


def _section_name(argv):
    """Section label for an `echo`/`printf` marker, or None."""
    verb = _base(argv[0])
    if verb not in ('echo', 'printf'):
        return None
    args = argv[1:]
    if verb == 'echo':
        while args and args[0] in ('-n', '-e', '-E', '-ne', '-en', '-nE', '-En', '-eE'):
            args = args[1:]
    else:
        if args and args[0] == '-v' and len(args) > 2:
            args = args[2:]
        if args and args[0] == '--':
            args = args[1:]
    text = ' '.join(args).strip()
    if text.endswith('\\n'):
        text = text[:-2]
    if text.endswith('\n'):
        text = text[:-1]
    text = text.strip()
    if not text or not _SECTION_RE.match(text):
        return None
    if sum(1 for ch in text if ch in _SECTION_CHARS) < 2:
        return None
    name = text.strip(_SECTION_CHARS + ' \t')
    if not name or len(name) > MAX_SECTION:
        return None
    if not _SECTION_BODY_RE.match(name) or not re.search(r'[A-Za-z0-9]', name):
        return None
    return name


def _parse_text(text, ctx, depth, state, first_op=''):
    """Parse one command string into steps (used for nested text too)."""
    if depth > MAX_WRAP_DEPTH:
        state.complete = False
        return [_step(text.strip(), first_op, UNKNOWN, 'nesting too deep', ctx, state)]
    try:
        tokens = _lex(text)
    except ValueError:
        state.complete = False
        return [_step(text.strip(), first_op, UNKNOWN, 'unterminated quote', ctx, state)]

    stmts = _statements(_atomise(tokens), state)
    steps = []
    first = True
    i = 0
    while i < len(stmts):
        j = i
        while j + 1 < len(stmts) and stmts[j + 1].op == '|':
            j += 1
        group = stmts[i:j + 1]
        group_steps = _parse_group(group, ctx, depth, state)
        if group_steps:
            if first and first_op and group_steps[0]['op'] == '':
                group_steps[0]['op'] = first_op
            first = False
            steps.extend(group_steps)
        i = j + 1
    return steps


def _group_text(group):
    parts = []
    for index, st in enumerate(group):
        parts.append(st.text if index == 0 else f'{st.op} {st.text}')
    return ' '.join(parts)


def _parse_group(group, ctx, depth, state):
    """Parse one pipeline group, decoding an encoded payload when there is one."""
    text = _group_text(group)
    if (depth <= MAX_WRAP_DEPTH and detect_encoded_exec(text)
            and not _has_wrapper(group[0].argv)):
        op = group[0].op
        preview = decode_preview(text)
        if preview and preview['text'].strip():
            state.flag('encoded')
            redirects = []
            for st in group:
                if st.heredoc:
                    state.flag('heredoc')
                for token in st.argv + [target for _o, target, _f in st.redirs]:
                    if cmd_rules.sensitive_path(token):
                        state.flag('sensitive-path')
                redirects.extend((redir_op, target) for redir_op, target, _f in st.redirs
                                 if redir_op in _WRITE_REDIRS and target != '/dev/null')
            inner = _parse_text(preview['text'], ctx + ['decoded'], depth + 1, state)
            if inner and inner[0]['op'] == '':
                inner[0]['op'] = op
            for redir_op, target in redirects:
                # The wrapper's own redirection still writes, even though the
                # decoded payload replaced the pipeline that carried it.
                state.flag('redirect-to-file')
                inner.append(_step(f'{redir_op} {shlex.quote(target)}', '', WRITE,
                                   f'redirect to {target}', ctx, state))
            return inner
        state.flag('encoded')
        state.complete = False
        return [_step(text, op, UNKNOWN, 'encoded payload (could not decode)', ctx, state)]

    steps = []
    for st in group:
        steps.extend(_parse_statement(st, ctx, depth, state))
    return steps


def _parse_statement(st, ctx, depth, state):
    """Classify one simple command, unwrapping wrappers and substitutions."""
    argv = list(st.argv)
    ctx = list(ctx)

    if not argv:
        # A bare redirection (`> /tmp/x`) truncates the target.
        targets = _write_targets(st)
        if targets:
            state.flag('redirect-to-file')
            return [_step(st.text, st.op, WRITE, f'redirect to {targets[0][1]}', ctx, state)]
        return []

    if st.heredoc:
        state.flag('heredoc')

    if argv[0] in _KEYWORDS_DROP:
        # `for i in …; do …; done`: the header executes nothing, but a
        # substitution inside it does, so keep those steps.
        _clean, header_subs, _bad = _extract_substitutions(argv)
        steps = []
        for text in header_subs:
            steps.extend(_parse_text(text, ctx + ['subst'], depth + 1, state))
        return steps

    if argv[0] in _KEYWORDS_TRIM:
        argv = argv[1:]
        if not argv:
            return []

    name = _section_name(argv)
    if name is not None:
        state.set_section(name)
        return []

    while True:
        if depth > MAX_WRAP_DEPTH:
            state.complete = False
            return [_step(st.text, st.op, UNKNOWN, 'wrapping too deep', ctx, state)]

        verb = _base(argv[0])

        if verb in _SHELLS or verb == 'su':
            if verb in _SHELLS:
                script, label = _shell_script(argv)
            else:
                script, label = _su_script(argv)
            if script is None:
                if st.heredoc:
                    state.flag('heredoc')
                    return [_step(st.text, st.op, UNKNOWN,
                                  f'{verb} reads its script from the heredoc', ctx, state)]
                return [_step(st.text, st.op, UNKNOWN,
                              f'{verb} without -c (interactive or script file)', ctx, state)]
            ctx.append(label)
            script = script + _redirect_suffix(st)
            return _parse_text(script, ctx, depth + 1, state, first_op=st.op)

        if st.heredoc and verb in _SCRIPT_READERS:
            state.flag('heredoc')
            return [_step(st.text, st.op, UNKNOWN,
                          f'heredoc fed to {verb}', ctx, state)]

        unwrapped = _unwrap(argv, state)
        if unwrapped is None:
            break
        argv, label, target = unwrapped
        if label:
            ctx.append(label)
        if target:
            state.add_inner(target)
        depth += 1

    clean_argv, substitutions, unparsed = _extract_substitutions(argv)
    assignments = _strip_assignments(clean_argv)

    if not assignments:
        steps = []
        for text in substitutions:
            steps.extend(_parse_text(text, ctx + ['subst'], depth + 1, state))
        targets = _write_targets(st)
        if targets:
            state.flag('redirect-to-file')
            steps.append(_step(st.text, st.op, WRITE,
                               f'redirect to {targets[0][1]}', ctx, state))
            return steps
        if not clean_argv:
            # The statement is only `$( … )`: whatever it prints is executed.
            steps.append(_step(st.text, st.op, UNKNOWN,
                               'command is built by a substitution', ctx, state))
        return steps

    kind, why = cmd_rules.classify(assignments)
    if _base(assignments[0]) in ('ssh', 'scp', 'sftp', 'rsync', 'mosh'):
        state.flag('nested-ssh')

    if unparsed:
        kind, why = UNKNOWN, 'substitution could not be parsed'

    sub_steps = []
    for text in substitutions:
        sub_steps.extend(_parse_text(text, ctx + ['subst'], depth + 1, state))
    for sub in sub_steps:
        merged = cmd_rules.worst(kind, sub['kind'])
        if merged != kind:
            kind = merged
            why = ('contains an unknown substitution' if sub['kind'] == UNKNOWN
                   else f'contains a {sub["kind"]} substitution')

    targets = _write_targets(st)
    if targets:
        state.flag('redirect-to-file')
        kind = cmd_rules.worst(kind, WRITE)
        why = f'redirect to {targets[0][1]}'

    for token in argv + [redir_target for _op, redir_target, _fd in st.redirs]:
        if cmd_rules.sensitive_path(token):
            state.flag('sensitive-path')
            break

    text = _display(argv) + _redirect_suffix(st)

    # Consistency with the existing risk map: anything it flags is never
    # advertised as a pure read, even when a verb table says otherwise.
    if kind == READ and get_cmd_risk(text):
        kind, why = UNKNOWN, 'flagged by the risk rules'

    return [_step(text, st.op, kind, why, ctx, state)] + sub_steps


# ── Result ──────────────────────────────────────────────────────────────────


def _headline(steps, summary):
    total = summary['steps']
    if not total:
        return 'no steps'
    parts = [
        f'{total} step' if total == 1 else f'{total} steps',
        f"{summary['read']} read",
        f"{summary['write']} write",
    ]
    if summary['unknown']:
        parts.append(f"{summary['unknown']} unknown")
    head = ' · '.join(parts)
    notable = None
    for kind in (WRITE, UNKNOWN, READ):
        for step in steps:
            if step['kind'] == kind:
                notable = step
                break
        if notable is not None:
            break
    if notable is not None:
        text = ' '.join(notable['text'].split())
        if len(text) > 72:
            text = text[:71] + '…'
        head += f': {text}'
    return head


def _result(steps, state):
    counts = {READ: 0, WRITE: 0, UNKNOWN: 0}
    for step in steps:
        counts[step['kind']] = counts.get(step['kind'], 0) + 1
    if steps and steps[0]['op']:
        # The first step has nothing before it: a leftover operator (from a
        # dropped section marker) would render as a stray prefix chip.
        steps[0]['op'] = ''
    summary = {
        'steps': len(steps),
        'read': counts[READ],
        'write': counts[WRITE],
        'unknown': counts[UNKNOWN],
    }
    return {
        'version': VERSION,
        'steps': steps,
        'sections': list(state.sections),
        'summary': summary,
        'headline': _headline(steps, summary),
        'inner_targets': list(state.inner_targets),
        'flags': list(state.flags),
        'complete': state.complete,
    }


def _breakdown(command):
    state = _ParseState()
    if not command.strip():
        return _result([], state)
    try:
        steps = _parse_text(command, [], 0, state)
    except Exception:  # noqa: BLE001 — breakdown() must be total
        # Any surprise in an exotic string degrades to one unknown step; a
        # parser that raises would take the whole approval queue down with it.
        state.complete = False
        steps = [_step(command.strip(), '', UNKNOWN, 'parsing failed', [], state)]
    return _result(steps, state)


@functools.lru_cache(maxsize=512)
def _cached(command):
    return _breakdown(command)


def breakdown(command: str) -> dict:
    """Break one command into read/write/unknown steps. Never raises.

    Results are cached by command text (the queue is polled), and every call
    returns a private copy so callers can annotate their step dicts.
    """
    if not isinstance(command, str):
        command = '' if command is None else str(command)
    return copy.deepcopy(_cached(command))
