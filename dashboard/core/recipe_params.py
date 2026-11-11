"""F16: typed recipe parameters and the substitution rules.

The closed type set, the T1 template rules and Y1's slot-verb allowlist live
here so the propose/approve/run paths all call one implementation. Nothing
in this module touches the database or the network.
"""
import ipaddress
import re
import shlex

from core.cmd_parse import breakdown

# ── the closed type set (§2, V2) ───────────────────────────────────────

# V2: RFC labels, so a label may not start or end with `-` and a value cannot
# become an option of the verb it is passed to.
_LABEL = r'[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?'
TYPES = {
    'hostname': re.compile(r'^%s$' % _LABEL),
    'fqdn': re.compile(r'^%s(\.%s)+$' % (_LABEL, _LABEL)),
    'ipv4': None,                      # parsed with ipaddress
    'port': re.compile(r'^[0-9]{1,5}$'),
    'slug': re.compile(r'^[a-z0-9_]{1,64}$'),
    'int': re.compile(r'^-?[0-9]{1,9}$'),
    'enum': None,                      # must be in the parameter's values
}

PARAM_NAME = re.compile(r'^[a-z][a-z0-9_]{0,31}$')
PLACEHOLDER = re.compile(r'\{([a-z][a-z0-9_]{0,31})\}')
SENTINEL = 'eshueshusentinel'

# V1: verbs that run their arguments (or a later word). The deny list is the
# second check behind Y1's allowlist.
EXEC_VERBS = frozenset({
    'sh', 'bash', 'zsh', 'dash', 'ksh', 'ash', 'busybox', 'eval', 'exec',
    'env', 'sudo', 'doas', 'xargs', 'timeout', 'nice', 'ionice', 'nohup',
    'watch', 'ssh', 'flock', 'strace', 'ltrace', 'chroot', 'runuser', 'su',
    'find', 'awk', 'sed', 'parallel', 'tar', 'rsync', 'git',
})

# Y1: the only verbs a slot-bearing step may run.
SLOT_VERBS = frozenset({'getent', 'dig', 'host', 'ping', 'nslookup'})

# Y1/AA1: one command, no pipeline or separator. `<` and `>` are redirections
# and write a file, so they are separators for this purpose too.
SEPARATORS = re.compile(r'[|;&<>`]|\$\(')


class RecipeParamError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# ── parameters ─────────────────────────────────────────────────────────

def normalise_param(spec, raw):
    """Validate and normalise one declared parameter value."""
    kind = spec['type']
    text = str(raw if raw is not None else '').strip()
    if kind in ('hostname', 'fqdn'):
        # Normalise first: the regexes are lower-case by design.
        text = text.lower().rstrip('.')
    if kind == 'ipv4':
        try:
            return str(ipaddress.IPv4Address(text))
        except ValueError:
            raise RecipeParamError('invalid_param',
                                   '%s must be an IPv4 address' % spec['name'])
    if kind == 'enum':
        values = [str(v) for v in spec.get('values') or []]
        if text not in values:
            raise RecipeParamError('invalid_param',
                                   '%s must be one of %s' % (spec['name'], values))
        return text
    pattern = TYPES.get(kind)
    if pattern is None or not pattern.fullmatch(text):
        raise RecipeParamError('invalid_param',
                               '%s is not a valid %s' % (spec['name'], kind))
    if kind == 'port':
        value = int(text)
        if not 1 <= value <= 65535:
            raise RecipeParamError('invalid_param', '%s is out of range' % spec['name'])
        return value
    if kind == 'int':
        value = int(text)
        low, high = spec.get('min'), spec.get('max')
        if low is not None and value < low:
            raise RecipeParamError('invalid_param', '%s is below %s' % (spec['name'], low))
        if high is not None and value > high:
            raise RecipeParamError('invalid_param', '%s is above %s' % (spec['name'], high))
        return value
    return text


def resolve_values(recipe, supplied):
    """Every declared parameter is required; no extra values are accepted."""
    declared = {spec['name']: spec for spec in recipe['parameters']}
    unknown = set(supplied or {}) - set(declared)
    if unknown:
        raise RecipeParamError('unknown_param',
                               'unknown parameters: %s' % ', '.join(sorted(unknown)))
    missing = [name for name in declared if name not in (supplied or {})]
    if missing:
        raise RecipeParamError('missing_param',
                               'missing parameters: %s' % ', '.join(sorted(missing)))
    return {name: normalise_param(spec, supplied[name])
            for name, spec in declared.items()}


# ── templates (§2, R1, T1, V1, Y1, AA1, AA2) ──────────────────────────

def check_template(template, declared):
    """R1/T1: the text rules. Returns nothing; raises on refusal."""
    if not isinstance(template, str) or not template.strip():
        raise RecipeParamError('invalid_step', 'an SSH command is required')
    if any(ch in template for ch in ('$', '`', '\n', '\r', "'", '"', '\\')):
        raise RecipeParamError(
            'invalid_step',
            'SSH templates may not contain $, backticks, newlines, quotes or backslashes')
    outside = PLACEHOLDER.sub('', template)
    if '{' in outside or '}' in outside:
        raise RecipeParamError('invalid_step',
                               'braces must be declared placeholders')
    unknown = [n for n in PLACEHOLDER.findall(template) if n not in declared]
    if unknown:
        raise RecipeParamError('unknown_param',
                               'template uses %s' % ', '.join(sorted(set(unknown))))
    for token in shlex.split(PLACEHOLDER.sub(SENTINEL, template)):
        if SENTINEL in token and token != SENTINEL:
            raise RecipeParamError('invalid_step',
                                   'a placeholder must be a whole word')


def _verb(step):
    """The first word of a parsed step, or ''."""
    text = str(step.get('text') or '')
    return text.split()[0].lower() if text.split() else ''


def validate_template(template, declared):
    """V1: no placeholder may become a command or an executor's argument."""
    check_template(template, declared)
    probe = PLACEHOLDER.sub(SENTINEL, template)
    for step in breakdown(probe).get('steps') or []:
        verb = _verb(step)
        if verb == SENTINEL or verb in EXEC_VERBS:
            raise RecipeParamError(
                'invalid_step',
                'a placeholder may not be a command or an argument to %s'
                % (verb or 'a command'))
        if verb == 'find' and any(w in ('-exec', '-execdir', '-ok')
                                  for w in str(step.get('text') or '').split()):
            raise RecipeParamError('invalid_step',
                                   'find -exec cannot take a placeholder')
    return True


def validate_slot_step(template, declared):
    """Y1/AA1/AA2: a slot-bearing template is one allowlisted lookup."""
    if not PLACEHOLDER.search(template):
        return None                     # fixed text; V1 already ran
    probe = PLACEHOLDER.sub(SENTINEL, template)
    if SEPARATORS.search(probe):
        raise RecipeParamError(
            'invalid_step',
            'a step with a parameter is a single command, with no pipe, '
            'separator or redirection')
    steps = breakdown(probe).get('steps') or []
    if len(steps) != 1:
        raise RecipeParamError(
            'invalid_step',
            'a step with a parameter is a single command')
    step = steps[0]
    # A wrapper (sudo, env, timeout, …) makes the verb something not on the
    # allowlist; refuse it explicitly rather than relying on the verb check.
    if step.get('context'):
        raise RecipeParamError(
            'invalid_step',
            'a step with a parameter may not run through a wrapper')
    verb = _verb(step)
    if verb not in SLOT_VERBS:
        raise RecipeParamError(
            'invalid_step',
            'a step with a parameter must run one of %s; %r cannot hold it'
            % (', '.join(sorted(SLOT_VERBS)), verb or 'a command'))
    if verb == 'ping':
        _check_ping_bound(step, probe)
    return True


def _check_ping_bound(step, probe):
    """AA2: a slot-bearing ping is bounded: -c 1..5 and a -W timeout."""
    words = str(step.get('text') or '').split()
    count = wait = None
    i = 1
    while i < len(words):
        word = words[i]
        if word == '-c' and i + 1 < len(words):
            count = words[i + 1]
            i += 2
            continue
        if word == '-W' and i + 1 < len(words):
            wait = words[i + 1]
            i += 2
            continue
        i += 1
    if not (count and count.isdigit() and 1 <= int(count) <= 5):
        raise RecipeParamError(
            'invalid_step',
            'a ping with a parameter must carry -c 1..5')
    if not (wait and wait.isdigit() and 1 <= int(wait) <= 60):
        raise RecipeParamError(
            'invalid_step',
            'a ping with a parameter must carry -W 1..60')


def substitute_command(template, values, declared):
    """R1/T1/V2/Y1: the shell-quoted value, never shell syntax."""
    check_template(template, declared)
    for name, value in values.items():
        if str(value).startswith('-'):
            raise RecipeParamError(
                'invalid_param',
                '%s may not start with "-" inside an SSH command' % name)

    def _one(match):
        name = match.group(1)
        if name not in values:
            raise RecipeParamError('unknown_param', 'template uses {%s}' % name)
        return shlex.quote(str(values[name]))

    return PLACEHOLDER.sub(_one, template)


def substitute_args(node, values, results=None, keep_results=False):
    """Tool calls: `{"param": name}` and `{"result": path}` slots.

    `keep_results` leaves a result slot unresolved, for the preview and the
    stored document (it is resolved from the step's own result at rollback)."""
    if isinstance(node, dict):
        keys = set(node)
        if keys == {'param'}:
            name = node['param']
            if name not in values:
                raise RecipeParamError('unknown_param', 'slot uses %s' % name)
            return values[name]
        if keys == {'result'}:
            if keep_results and results is None:
                return node
            if results is None:
                raise RecipeParamError('unknown_param',
                                       'a result slot is only valid in an undo')
            return result_value(results, node['result'])
        return {key: substitute_args(value, values, results, keep_results)
                for key, value in node.items()}
    if isinstance(node, list):
        return [substitute_args(item, values, results, keep_results)
                for item in node]
    return node


def result_value(result, path):
    """Resolve a dotted result path; a missing value refuses (T2)."""
    value = result
    for part in str(path or '').split('.'):
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            raise RecipeParamError('rollback_missing_result',
                                   'the stored result has no %s' % path)
    if isinstance(value, (dict, list)):
        raise RecipeParamError('rollback_missing_result',
                               '%s is not a scalar' % path)
    return value
