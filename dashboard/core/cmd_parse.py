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
* Wrappers (`sudo`, `timeout`, `pct exec`, `qm guest exec`, `docker exec`,
  `sh -c`, …) are
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
_REDIRS = {'>', '>>', '>|', '&>', '&>>', '>&', '<', '<<', '<<<', '<&'}
_WRITE_REDIRS = {'>', '>>', '&>', '&>>', '>&', '>|'}
_SHELLS = {'sh', 'bash', 'dash', 'zsh', 'ksh', 'ash'}
_INTERPRETERS = {'python', 'python2', 'python3', 'perl', 'perl5', 'ruby',
                 'node', 'nodejs', 'php'}
_SCRIPT_READERS = _SHELLS | _INTERPRETERS

_DOCKER_GLOBAL_VALUE_FLAGS = {'-H', '--host', '--context', '--config', '-c',
                              '--log-level', '-l', '--tlscacert', '--tlscert',
                              '--tlskey'}
_RUNUSER_VALUE_FLAGS = {'-u', '--user', '-g', '--group', '-G', '--supp-group',
                        '-s', '--shell', '-w', '--whitelist-environment'}

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

    __slots__ = ('complete', 'flags', 'inner_targets', 'sections', 'section',
                 'functions')

    def __init__(self):
        self.complete = True
        self.flags = []
        self.inner_targets = []
        self.sections = []
        self.section = ''
        self.functions = {}

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

    __slots__ = ('argv', 'op', 'redirs', 'text', 'heredoc', 'heredoc_specs')

    def __init__(self, argv, op, redirs, text, heredoc=False, heredoc_specs=()):
        self.argv = argv
        self.op = op
        self.redirs = redirs
        self.text = text
        self.heredoc = heredoc
        self.heredoc_specs = list(heredoc_specs)


# ── Tokenising ──────────────────────────────────────────────────────────────


def _base(token):
    return os.path.basename(token) if token else ''


def _quote(token):
    """Shell-quote a token for display; keep substitutions readable."""
    if _has_substitution(token):
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
        elif ch == '&' and nxt == '>' and run[i + 2:i + 3] == '>':
            out.append('&>>')
            i += 3
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
        # Process substitution is a word, not a redirection (R3) — check
        # before the punctuation branch turns `<` into a redirect atom.
        if tok in ('<', '>') and i + 1 < n and tokens[i + 1] == '(':
            end = _matching_paren(tokens, i + 1)
            if end is not None:
                atoms.append(('w', tok + '(' + _tokens_text(tokens[i + 2:end]) + ')'))
                i = end + 1
                continue
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


def _unquote_word(word):
    """Strip shell quoting from a heredoc delimiter word."""
    out = []
    i = 0
    while i < len(word):
        ch = word[i]
        if ch == '\\' and i + 1 < len(word):
            out.append(word[i + 1])
            i += 2
            continue
        if ch in ('"', "'"):
            quote = ch
            i += 1
            while i < len(word) and word[i] != quote:
                out.append(word[i])
                i += 1
            i += 1
            continue
        out.append(ch)
        i += 1
    return ''.join(out)


def _heredoc_ops(text, start, end):
    """Every heredoc operator between start and end of one line, in order."""
    ops = []
    i = start
    in_single = False
    in_double = False
    word_start = True
    while i < end:
        ch = text[i]
        if in_single:
            if ch == "'":
                in_single = False
            i += 1
            word_start = False
            continue
        if in_double:
            if ch == '\\':
                i += 2
                continue
            if ch == '"':
                in_double = False
            i += 1
            continue
        if ch == '\\':
            i += 2
            word_start = False
            continue
        if ch == "'":
            in_single = True
            i += 1
            word_start = False
            continue
        if ch == '"':
            in_double = True
            i += 1
            word_start = False
            continue
        if ch == '`':
            j = text.find('`', i + 1, end)
            i = end if j < 0 else j + 1
            word_start = False
            continue
        if ch == '#' and word_start:
            while i < end and text[i] != '\n':
                i += 1
            continue
        if (ch == '<' and text[i + 1:i + 2] == '<'
                and text[i + 2:i + 3] != '<' and text[i - 1:i] != '<'):
            op = '<<'
            j = i + 2
            if text[j:j + 1] == '-':
                op = '<<-'
                j += 1
            while j < end and text[j] in ' \t':
                j += 1
            word_begin = j
            quoted = False
            while j < end and text[j] not in ' \t\n;&|<>()':
                if text[j] in ('"', "'", '\\'):
                    quoted = True
                j += 1
            delim = _unquote_word(text[word_begin:j])
            if delim:
                ops.append((op, delim, quoted))
            i = j
            word_start = False
            continue
        word_start = ch in ' \t\n;&|()<>'
        i += 1
    return ops


def _read_heredoc_body(text, pos, op, delim):
    """Read one heredoc body starting at `pos`; returns (body, body range, end)."""
    n = len(text)
    if pos >= n:
        return '', n, n, n, False
    line_pos = pos
    while line_pos <= n:
        line_end = text.find('\n', line_pos)
        if line_end < 0:
            line_end = n
        line = text[line_pos:line_end]
        if (line.lstrip('\t') if op == '<<-' else line) == delim:
            return text[pos:line_pos], pos, line_pos, line_end, True
        if line_end >= n:
            break
        line_pos = line_end + 1
    return text[pos:], pos, n, n, False


def _scan_heredocs(text):
    """Find heredocs in the raw text: delimiter quoting, body, body range.

    The token stream loses whether `<<EOF` was quoted, and the body is data
    unless the delimiter is unquoted. Both matter for review round 1 (R2).
    Every heredoc on an operator line is collected before its bodies are read:
    `cat <<A <<B` reads A's body first, then B's (review round 2, S2).
    """
    specs = []
    i = 0
    n = len(text)
    in_single = False
    in_double = False
    while i < n:
        ch = text[i]
        if in_single:
            if ch == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            if ch == '\\':
                i += 2
                continue
            if ch == '"':
                in_double = False
            i += 1
            continue
        if ch == '\\':
            i += 2
            continue
        if ch == "'":
            in_single = True
            i += 1
            continue
        if ch == '"':
            in_double = True
            i += 1
            continue
        if ch == '`':
            end = text.find('`', i + 1)
            i = n if end < 0 else end + 1
            continue
        if ch == '#' and (i == 0 or text[i - 1] in ' \t\n;&|()<>'):
            while i < n and text[i] != '\n':
                i += 1
            continue
        if (ch == '<' and text[i + 1:i + 2] == '<'
                and text[i + 2:i + 3] != '<' and text[i - 1:i] != '<'):
            line_end = text.find('\n', i)
            if line_end < 0:
                line_end = n
            group = _heredoc_ops(text, i, line_end)
            if not group:
                i += 1
                continue
            pos = line_end + 1 if line_end < n else n
            for op, delim, quoted in group:
                body, body_start, body_end, end_pos, found = _read_heredoc_body(
                    text, pos, op, delim)
                specs.append({
                    'op': op, 'delim': delim, 'quoted': quoted,
                    'body': body,
                    'body_start': body_start,
                    'body_end': body_end if found else n,
                    'end': end_pos if found else n,
                    'found': found,
                })
                if not found:
                    break
                pos = end_pos + 1
            i = pos
            continue
        i += 1
    return specs


def _strip_comments(text, heredoc_specs):
    """Remove shell comments, but only where a word can start (R1).

    `shlex` starts a comment at any `#` it meets, including inside a word
    (`ls /tmp/#dir`), which hides the rest of the line from the breakdown.
    Heredoc bodies are copied verbatim: `#` is data there.
    """
    bodies = [(spec['body_start'], spec['body_end']) for spec in heredoc_specs
              if spec['body_end'] > spec['body_start']]
    out = []
    i = 0
    n = len(text)
    word_start = True
    in_single = False
    in_double = False
    while i < n:
        for start, end in bodies:
            if i == start:
                out.append(text[start:end])
                i = end
                word_start = False
                break
        else:
            ch = text[i]
            if in_single:
                out.append(ch)
                if ch == "'":
                    in_single = False
                i += 1
                continue
            if in_double:
                if ch == '\\' and i + 1 < n:
                    out.append(text[i:i + 2])
                    i += 2
                    continue
                out.append(ch)
                if ch == '"':
                    in_double = False
                i += 1
                continue
            if ch == '\\' and i + 1 < n:
                out.append(text[i:i + 2])
                i += 2
                word_start = False
                continue
            if ch == "'":
                in_single = True
                out.append(ch)
                i += 1
                word_start = False
                continue
            if ch == '"':
                in_double = True
                out.append(ch)
                i += 1
                word_start = False
                continue
            if ch == '#' and word_start:
                while i < n and text[i] != '\n':
                    i += 1
                continue
            out.append(ch)
            word_start = ch in ' \t\n;&|()<>'
            i += 1
    return ''.join(out)


def _lex(text):
    lexer = shlex.shlex(text, posix=True, punctuation_chars=PUNCT_CHARS)
    lexer.whitespace = ' \t\r'
    lexer.whitespace_split = True
    # Comments are stripped by _strip_comments: shlex's own rule starts a
    # comment mid-word (`/tmp/#dir`), which is not what the shell does (R1).
    lexer.commenters = ''
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


def _statements(atoms, state, heredoc_specs=()):
    """Group atoms into simple commands, honouring redirections and heredocs."""
    specs = list(heredoc_specs)
    spec_index = 0
    stmts = []
    argv = []
    redirs = []
    parts = []
    heredocs = []       # (delimiter, spec) awaiting their body, read at the newline
    stmt_heredoc = False  # the statement being built carries a heredoc
    stmt_specs = []
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
                    spec = specs[spec_index] if spec_index < len(specs) else None
                    spec_index += 1
                    heredocs.append((target, spec))
                    stmt_heredoc = True
                    if spec:
                        stmt_specs.append(spec)
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
                                        ' '.join(parts), stmt_heredoc,
                                        list(stmt_specs)))
                argv, redirs, parts = [], [], []
                stmt_heredoc = False
                stmt_specs = []
                op = ''
            i += 1
            continue

        if atom in _REDIRS:
            pending_redir = atom
            i += 1
            continue

        if atom in _SEPS:
            if argv or redirs:
                stmts.append(_Statement(list(argv), op, list(redirs),
                                        ' '.join(parts), stmt_heredoc,
                                        list(stmt_specs)))
                argv, redirs, parts = [], [], []
                stmt_heredoc = False
                stmt_specs = []
                op = ''
            # A heredoc body starts on the line *after* the operator line, so
            # `cat <<EOF; rm x` keeps the `rm` as its own statement (S1) and
            # the bodies are only skipped when the newline arrives.
            if atom == '\n' and heredocs:
                for delim, _spec in heredocs:
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
    if heredocs:
        # A heredoc whose body never started on its own line.
        state.complete = False
    if argv or redirs:
        stmts.append(_Statement(list(argv), op, list(redirs),
                                ' '.join(parts), stmt_heredoc, list(stmt_specs)))
    return stmts


# ── Substitutions ───────────────────────────────────────────────────────────


def _matching_paren_text(text, start):
    """Index of the ')' matching text[start] == '(', or None."""
    depth = 0
    i = start
    n = len(text)
    while i < n:
        if text[i] == '(':
            depth += 1
        elif text[i] == ')':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _strip_substitutions(token):
    """Return (leftover, [(kind, inner text)], unparsed).

    kind is `subst` for `$()`/backticks and `procsubst` / `procsubst-out`
    for `<()` / `>()` (review round 1, R3).
    """
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
            texts.append(('subst', token[i + 1:end]))
            i = end + 1
            continue
        if ch == '$' and i + 1 < n and token[i + 1] == '(':
            j = _matching_paren_text(token, i + 1)
            if j is None:
                unparsed = True
                leftover.append(token[i:])
                break
            texts.append(('subst', token[i + 2:j]))
            i = j + 1
            continue
        if ch in '<>' and i + 1 < n and token[i + 1] == '(':
            j = _matching_paren_text(token, i + 1)
            if j is None:
                unparsed = True
                leftover.append(token[i:])
                break
            kind = 'procsubst-out' if ch == '>' else 'procsubst'
            texts.append((kind, token[i + 2:j]))
            i = j + 1
            continue
        leftover.append(ch)
        i += 1
    return ''.join(leftover), texts, unparsed


def _has_substitution(token):
    return ('$(' in token or '`' in token or '<(' in token or '>(' in token)


def _extract_substitutions(argv):
    """Separate command substitutions from the argv they sit in."""
    clean = []
    texts = []
    unparsed = False
    for token in argv:
        if _has_substitution(token):
            leftover, found, bad = _strip_substitutions(token)
            texts.extend(item for item in found if item[1].strip())
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


def _runuser_script(argv):
    """(script, 'runuser') for `runuser -c '…'`, or (None, None)."""
    args = argv[1:]
    for i, arg in enumerate(args):
        if arg in ('-c', '--command'):
            return (args[i + 1] if i + 1 < len(args) else None), 'runuser'
        if arg.startswith('--command='):
            return arg.split('=', 1)[1], 'runuser'
    return None, None


def _unwrap(argv, state):
    """Strip one wrapper prefix; return (argv, context label, inner target)."""
    verb, allowed = cmd_rules.resolve_verb(argv[0])
    if not allowed:
        return None
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

    if verb == 'qm':
        # `qm guest exec <vmid> [--] cmd…` runs inside a VM, like `pct exec`
        # (F7). `qm list|status|config` are handled by the verb table.
        if (len(args) >= 3 and args[0] == 'guest' and args[1] == 'exec'
                and args[2].isdigit()):
            rest = args[3:]
            if rest and rest[0] == '--':
                rest = rest[1:]
            if not rest:
                return None
            return rest, f'qm {args[2]}', f'qm:{args[2]}'
        return None

    if verb == 'ip':
        rest = _strip_flags(args, {'-n', '-netns', '-b', '-batch', '-rc', '-rcvbuf'})
        if len(rest) >= 3 and rest[0] == 'netns' and rest[1] == 'exec':
            namespace = rest[2]
            command = rest[3:]
            if command and command[0] == '--':
                command = command[1:]
            if command:
                return command, f'netns {namespace}', ''
        return None

    if verb == 'runuser':
        rest = _strip_flags(args, _RUNUSER_VALUE_FLAGS)
        if rest and rest[0] != '--' and '--' in rest:
            rest = rest[1:]  # the USER positional before `--`
        if rest and rest[0] == '--':
            rest = rest[1:]
        if not rest:
            return None
        return rest, 'runuser', ''

    if verb == 'docker':
        rest = _strip_flags(args, _DOCKER_GLOBAL_VALUE_FLAGS)
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
    verb, allowed = cmd_rules.resolve_verb(argv[0])
    if not allowed:
        return False
    if verb in _SHELLS or verb in ('su', 'runuser'):
        return True
    return _unwrap(argv, _ParseState()) is not None


# ── Steps ───────────────────────────────────────────────────────────────────


def _step(text, op, kind, why, ctx, state, extra=None):
    step = {
        'text': text,
        'op': op or '',
        'kind': kind,
        'why': why,
        'section': state.section,
        'context': list(ctx),
        'elevated': any(label in ('sudo', 'su', 'runuser') for label in ctx),
    }
    if extra:
        step.update(extra)
    return step


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
    verb, allowed = cmd_rules.resolve_verb(argv[0])
    if not allowed or verb not in ('echo', 'printf'):
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


MAX_SCRIPT_CHARS = 8192


def _script_text(body):
    """The script a step carries, capped like the decoded preview."""
    if len(body) <= MAX_SCRIPT_CHARS:
        return body
    return body[:MAX_SCRIPT_CHARS] + '…'


def _count_lines(text):
    return len([line for line in (text or '').splitlines() if line.strip()])


def _line_count_label(text):
    count = _count_lines(text)
    return f'{count} line' if count == 1 else f'{count} lines'


def _dangerous_env_change(argv):
    """True when the statement sets a variable that changes later commands (R5)."""
    i = 0
    while i < len(argv):
        name = cmd_rules.assignment_name(argv[i])
        if not name:
            break
        if cmd_rules.dangerous_env_name(name):
            return True
        i += 1
    verb, allowed = cmd_rules.resolve_verb(argv[0])
    if allowed and verb == 'env':
        for token in argv[1:]:
            if token.startswith('-'):
                continue
            name = cmd_rules.assignment_name(token)
            if not name:
                break
            if cmd_rules.dangerous_env_name(name):
                return True
    return False


def _follow_cluster(args, letters='fF'):
    for arg in args:
        if arg.startswith('--'):
            if arg in ('--follow', '--follow=name', '--follow=descriptor'):
                return True
            continue
        if arg.startswith('-') and arg != '-':
            if any(ch in letters for ch in arg[1:]):
                return True
    return False


def _repo_config(argv):
    """True for the git reads that can run repository-configured programs
    (`core.fsmonitor`, textconv, pager) — review round 2, S6."""
    verb, allowed = cmd_rules.resolve_verb(argv[0])
    if not allowed or verb != 'git':
        return False
    return bool(cmd_rules.git_read_subcommand(argv))


def _follows(argv):
    """True for commands that stream and never end (review round 1, R7)."""
    verb, allowed = cmd_rules.resolve_verb(argv[0])
    if not allowed:
        return False
    args = argv[1:]
    if verb in ('tail', 'journalctl'):
        return _follow_cluster(args)
    if verb == 'dmesg':
        return _follow_cluster(args, letters='w')
    if verb == 'docker':
        rest = _strip_flags(args, _DOCKER_GLOBAL_VALUE_FLAGS)
        if not rest:
            return False
        if rest[0] == 'events':
            return True
        if rest[0] == 'logs':
            return _follow_cluster(rest[1:])
    return False


def _sensitive_command(argv):
    """Reads that print secrets even though they are not `cat .env` (R8)."""
    verb, allowed = cmd_rules.resolve_verb(argv[0])
    if not allowed:
        return False
    if verb in ('env', 'printenv') and len(argv) == 1:
        return True
    if verb == 'docker':
        rest = _strip_flags(argv[1:], _DOCKER_GLOBAL_VALUE_FLAGS)
        if not rest:
            return False
        if rest[0] == 'inspect':
            return True
        if rest[0] == 'compose':
            compose = _strip_flags(rest[1:], _COMPOSE_GLOBAL_VALUE_FLAGS)
            return bool(compose) and compose[0] == 'config'
    return False


def _heredoc_step(st, verb, ctx, state):
    """One step for a heredoc fed to a shell or an interpreter (R9)."""
    body = st.heredoc_specs[0]['body'] if st.heredoc_specs else ''
    if verb in _INTERPRETERS and body:
        return _step(st.text, st.op, UNKNOWN,
                     f'{verb} script from stdin ({_line_count_label(body)})',
                     ctx, state, {'script': _script_text(body)})
    return _step(st.text, st.op, UNKNOWN, f'heredoc fed to {verb}', ctx, state)


def _atoms_text(atoms):
    """Rebuild re-parseable text from atoms (used for function bodies)."""
    parts = []
    for atom in atoms:
        if isinstance(atom, tuple):
            parts.append(_quote(atom[1]))
        else:
            parts.append(atom)
    return ' '.join(parts)


def _function_definition(atoms, i):
    """Return (name, body_start, body_atoms) when a definition starts at i."""
    atom = atoms[i]
    if not isinstance(atom, tuple):
        return None
    if i + 2 < len(atoms) and atoms[i + 1] == '(' and atoms[i + 2] == ')':
        name = atom[1]
        j = i + 3
    elif atom[1] == 'function' and i + 1 < len(atoms) and isinstance(atoms[i + 1], tuple):
        name = atoms[i + 1][1]
        j = i + 2
        if j + 1 < len(atoms) and atoms[j] == '(' and atoms[j + 1] == ')':
            j += 2
    else:
        return None
    if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', name):
        return None
    if j >= len(atoms) or not (isinstance(atoms[j], tuple) and atoms[j][1] == '{'):
        return None
    depth = 0
    k = j
    while k < len(atoms):
        current = atoms[k]
        if isinstance(current, tuple) and current[1] == '{':
            depth += 1
        elif isinstance(current, tuple) and current[1] == '}':
            depth -= 1
            if depth == 0:
                return name, j, atoms[j + 1:k]
        k += 1
    return name, j, atoms[j + 1:]


def _extract_functions(atoms, state, depth):
    """Drop function headers; record the worst kind of each body (R10)."""
    out = []
    i = 0
    n = len(atoms)
    while i < n:
        found = _function_definition(atoms, i)
        if found is None:
            out.append(atoms[i])
            i += 1
            continue
        name, body_start, body_atoms = found
        if name not in state.functions:
            probe = _ParseState()
            probe.functions = dict(state.functions)
            try:
                body_steps = _parse_text(_atoms_text(body_atoms), [], depth + 1, probe)
            except Exception:  # noqa: BLE001 — a body we cannot parse is unknown
                body_steps = []
            kind = READ
            for step in body_steps:
                kind = cmd_rules.worst(kind, step['kind'])
            state.functions[name] = kind
        i = body_start
    return out


def _parse_text(text, ctx, depth, state, first_op=''):
    """Parse one command string into steps (used for nested text too)."""
    if depth > MAX_WRAP_DEPTH:
        state.complete = False
        return [_step(text.strip(), first_op, UNKNOWN, 'nesting too deep', ctx, state)]
    try:
        heredoc_specs = _scan_heredocs(text)
        tokens = _lex(_strip_comments(text, heredoc_specs))
    except ValueError:
        state.complete = False
        return [_step(text.strip(), first_op, UNKNOWN, 'unterminated quote', ctx, state)]

    atoms = _extract_functions(_atomise(tokens), state, depth)
    stmts = _statements(atoms, state, heredoc_specs)
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


def _group_sink(group):
    """The program that consumes the group's output: 'shell', interp, or ''."""
    argv = list(group[-1].argv)
    for _attempt in range(MAX_WRAP_DEPTH + 1):
        if not argv:
            return ''
        verb, allowed = cmd_rules.resolve_verb(argv[0])
        if not allowed:
            return ''
        if verb in _SHELLS or verb == 'eval':
            return 'shell'
        if verb in _INTERPRETERS:
            return verb
        unwrapped = _unwrap(argv, _ParseState())
        if unwrapped is None:
            return ''
        argv = unwrapped[0]
    return ''


def _parse_group(group, ctx, depth, state):
    """Parse one pipeline group, decoding an encoded payload when there is one."""
    text = _group_text(group)
    if (depth <= MAX_WRAP_DEPTH and detect_encoded_exec(text)
            and not _has_wrapper(group[0].argv)):
        op = group[0].op
        preview = decode_preview(text)
        if preview and preview['text'].strip():
            state.flag('encoded')
            sink = _group_sink(group)
            if sink == 'shell':
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
                    # The wrapper's own redirection still writes, even though
                    # the decoded payload replaced the pipeline carrying it.
                    state.flag('redirect-to-file')
                    inner.append(_step(f'{redir_op} {shlex.quote(target)}', '', WRITE,
                                       f'redirect to {target}', ctx, state))
                return inner
            if sink:
                # Python/perl/ruby/node: one opaque script, not shell steps (R9).
                body = preview['text']
                return [_step(text, op, UNKNOWN,
                              f'decoded {sink} script ({_line_count_label(body)})',
                              ctx, state, {'script': _script_text(body)})]
            # No recognised sink: keep the outer steps; the payload is still
            # flagged `encoded` above.
        else:
            state.flag('encoded')
            state.complete = False
            return [_step(text, op, UNKNOWN,
                          'encoded payload (could not decode)', ctx, state)]

    steps = []
    for st in group:
        steps.extend(_parse_statement(st, ctx, depth, state))
    return steps


def _sub_context(kind, ctx):
    if kind == 'heredoc':
        return ctx + ['heredoc']
    if kind.startswith('procsubst'):
        return ctx + ['procsubst']
    return ctx + ['subst']


def _parse_statement(st, ctx, depth, state):
    """Classify one simple command, unwrapping wrappers and substitutions."""
    argv = list(st.argv)
    ctx = list(ctx)
    heredoc_specs = list(st.heredoc_specs)

    if not argv:
        # A bare redirection (`> /tmp/x`) truncates the target.
        targets = _write_targets(st)
        if targets:
            state.flag('redirect-to-file')
            return [_step(st.text, st.op, WRITE, f'redirect to {targets[0][1]}', ctx, state)]
        return []

    if st.heredoc:
        state.flag('heredoc')

    dangerous_env = False

    if argv[0] in _KEYWORDS_DROP:
        # `for i in …; do …; done`: the header executes nothing, but a
        # substitution inside it does, so keep those steps.
        _clean, header_subs, _bad = _extract_substitutions(argv)
        steps = []
        for _kind, text in header_subs:
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
        dangerous_env = dangerous_env or _dangerous_env_change(argv)
        if depth > MAX_WRAP_DEPTH:
            state.complete = False
            return [_step(st.text, st.op, UNKNOWN, 'wrapping too deep', ctx, state)]

        verb, allowed = cmd_rules.resolve_verb(argv[0])

        if allowed and (verb in _SHELLS or verb in ('su', 'runuser')):
            if verb in _SHELLS:
                script, label = _shell_script(argv)
            elif verb == 'su':
                script, label = _su_script(argv)
            else:
                script, label = _runuser_script(argv)
            if script is not None:
                ctx.append(label)
                script = script + _redirect_suffix(st)
                return _parse_text(script, ctx, depth + 1, state, first_op=st.op)
            if verb == 'runuser':
                unwrapped = _unwrap(argv, state)
                if unwrapped is not None:
                    argv, label, target = unwrapped
                    if label:
                        ctx.append(label)
                    if target:
                        state.add_inner(target)
                    depth += 1
                    continue
            if st.heredoc:
                state.flag('heredoc')
                return [_heredoc_step(st, verb, ctx, state)]
            return [_step(st.text, st.op, UNKNOWN,
                          f'{verb} without -c (interactive or script file)', ctx, state)]

        if st.heredoc and allowed and verb in _SCRIPT_READERS:
            state.flag('heredoc')
            return [_heredoc_step(st, verb, ctx, state)]

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

    # R2: heredoc bodies (unquoted delimiters only) and redirection words run
    # their substitutions even though the text looks like data.
    extra_subs = []
    for spec in heredoc_specs:
        if spec and not spec['quoted'] and spec['body']:
            _left, found, bad = _strip_substitutions(spec['body'])
            unparsed = unparsed or bad
            extra_subs.extend(('heredoc', text) for _kind, text in found if text.strip())
    for _op, redir_target, _fd in st.redirs:
        if _has_substitution(redir_target):
            _left, found, bad = _strip_substitutions(redir_target)
            unparsed = unparsed or bad
            extra_subs.extend(('subst', text) for _kind, text in found if text.strip())

    if not assignments:
        steps = []
        for sub_kind, text in substitutions + extra_subs:
            steps.extend(_parse_text(text, _sub_context(sub_kind, ctx), depth + 1, state))
        targets = _write_targets(st)
        if targets:
            state.flag('redirect-to-file')
            steps.append(_step(st.text, st.op, WRITE,
                               f'redirect to {targets[0][1]}', ctx, state))
            return steps
        if dangerous_env:
            steps.append(_step(st.text, st.op, UNKNOWN,
                               'changes how later commands run', ctx, state))
            return steps
        if not clean_argv:
            # The statement is only `$( … )`: whatever it prints is executed.
            steps.append(_step(st.text, st.op, UNKNOWN,
                               'command is built by a substitution', ctx, state))
        return steps

    verb, allowed = cmd_rules.resolve_verb(assignments[0])
    function_kind = state.functions.get(verb) if allowed else None

    if function_kind is not None:
        kind, why = function_kind, f'calls function {verb}'
    else:
        kind, why = cmd_rules.classify(assignments)
        if allowed and verb in ('ssh', 'scp', 'sftp', 'rsync', 'mosh'):
            state.flag('nested-ssh')
        if _follows(assignments):
            state.flag('follows')
        if _sensitive_command(assignments):
            state.flag('sensitive-path')
        if _repo_config(assignments):
            state.flag('repo-config')

    if dangerous_env:
        kind, why = UNKNOWN, 'changes how later commands run'

    if unparsed:
        kind, why = UNKNOWN, 'substitution could not be parsed'

    sub_steps = []
    had_output_sink = False
    for sub_kind, text in substitutions + extra_subs:
        sub_steps.extend(_parse_text(text, _sub_context(sub_kind, ctx), depth + 1, state))
        if sub_kind == 'procsubst-out':
            had_output_sink = True
    for sub in sub_steps:
        merged = cmd_rules.worst(kind, sub['kind'])
        if merged != kind:
            kind = merged
            why = ('contains an unknown substitution' if sub['kind'] == UNKNOWN
                   else f'contains a {sub["kind"]} substitution')
    if had_output_sink:
        # `>( … )` is a sink: the command's output feeds another program, so
        # the step is never a plain read (R3).
        kind = cmd_rules.worst(kind, WRITE)

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
