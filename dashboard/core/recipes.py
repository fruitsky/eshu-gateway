"""F16: the recipe engine.

Validates a recipe document, pins it to a content hash, builds the run's plan,
executes the steps in order (claiming each by run and step), evaluates the
checks, and rolls back only what the run created. See docs/features/
FEATURE_PLAN.md, Chunk F16, Rounds 1–5 for the design of record.
"""
import copy
import json
import re
import threading
import time

from core.cmd_parse import breakdown
from core.integration_proxy import ProxyError, execute_integration_call
from core.read_policy import is_read_integration_call
from core.recipe_params import (
    PLACEHOLDER, RecipeParamError, SENTINEL, SLOT_VERBS, SEPARATORS,
    normalise_param, resolve_values, result_value, substitute_args,
    substitute_command, validate_slot_step, validate_template,
)
from core.recipe_tools import (
    ACCESS_LIST_TOOLS, FORBIDDEN_RECIPE_TOOLS, RECIPE_TOOLS, RECIPE_UNDO,
    classify, spec_for, validate_args,
)
from db.audit import record_audit_event
from db.gateways import get_gateway_run_limits, get_trigger_freeze
from db.integrations import get_integration, get_tool
from db.misc import get_run_limit_settings
from db.recipes import (
    RecipeError, add_run_step, add_version, approve_version, content_hash,
    create_recipe,
    create_run, current_version, get_recipe, get_run, get_version,
    pending_runs_for_approved_plans, set_run, set_step,
)
from db.exec_results import get_result
from db.requests import (
    create_recipe_request, expire_recipe_reservation, get_request_row,
)
from db.session_plans import claim_recipe_item, create_recipe_plan, get_plan

NAME_RE = re.compile(r'^[a-z][a-z0-9_]{2,63}$')
STEP_ID_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,15}$')
HOST_RE = re.compile(r'^[A-Za-z0-9_.:-]{1,64}$')
PARAM_PATH = re.compile(r'^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+){0,3}$')
MAX_STEPS = 10
MAX_SSH_STEPS = 3
MAX_PARAMS = 16
MAX_STORE = 4000
POLL_SECONDS = 2
STEP_TIMEOUT = 60

_CHECK_KINDS = ('eq', 'equals', 'in', 'contains', 'present', 'exit_code',
                'status_code')
_PRED_KEYS = ({'exit_code'}, {'status_code'}, {'field', 'equals'},
              {'field', 'in'}, {'field', 'contains'}, {'field', 'present'},
              {'all'}, {'any'})


# ── validation ─────────────────────────────────────────────────────────

def _fail(code, message):
    raise RecipeError(code, message)


def _doc(doc):
    if not isinstance(doc, dict):
        _fail('recipe_invalid', 'the document must be an object')
    allowed = {'name', 'description', 'parameters', 'preconditions', 'steps',
               'allowed_agents'}
    unknown = set(doc) - allowed
    if unknown:
        _fail('recipe_invalid', 'unknown keys: %s' % ', '.join(sorted(unknown)))
    if 'auto_run' in doc:
        _fail('auto_run_locked', 'auto_run is not part of a recipe document in v1')
    return doc


def _parameters(params):
    if not isinstance(params, list) or not params:
        _fail('recipe_invalid', 'parameters must be a non-empty list')
    if len(params) > MAX_PARAMS:
        _fail('too_many_params', 'at most %d parameters' % MAX_PARAMS)
    seen = set()
    out = []
    for spec in params:
        if not isinstance(spec, dict):
            _fail('recipe_invalid', 'each parameter must be an object')
        name = str(spec.get('name') or '')
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,31}', name):
            _fail('recipe_invalid', 'invalid parameter name')
        if name in seen:
            _fail('recipe_invalid', 'duplicate parameter %s' % name)
        seen.add(name)
        kind = str(spec.get('type') or '')
        if kind not in ('hostname', 'fqdn', 'ipv4', 'port', 'slug', 'int', 'enum'):
            _fail('recipe_invalid', 'unknown parameter type %r' % kind)
        item = {'name': name, 'type': kind}
        if kind == 'enum':
            values = spec.get('values')
            if not isinstance(values, list) or not values or len(values) > 16:
                _fail('recipe_invalid', '%s needs 1–16 enum values' % name)
            if any(not isinstance(v, (str, int)) for v in values):
                _fail('recipe_invalid', '%s enum values must be strings' % name)
            item['values'] = [str(v) for v in values]
        if kind == 'int':
            low, high = spec.get('min'), spec.get('max')
            if not isinstance(low, int) or not isinstance(high, int) or low > high:
                _fail('recipe_invalid', '%s needs integer min and max' % name)
            item['min'], item['max'] = low, high
        if spec.get('example') is not None:
            item['example'] = spec['example']
        out.append(item)
    return out


def _resolve_integration(name):
    integration = get_integration(str(name or ''))
    if not integration or not integration.get('enabled'):
        _fail('recipe_integration_missing', 'integration %r is missing or disabled' % name)
    return integration


def _tool_role(call, declared, role, where):
    if not isinstance(call, dict):
        _fail('recipe_invalid', '%s needs a call' % where)
    integration = _resolve_integration(call.get('integration'))
    tool_name = str(call.get('tool') or '')
    kind = str(integration.get('kind') or '').lower()
    verdict = classify(kind, tool_name)
    if role == 'undo':
        if verdict != 'undo':
            _fail('recipe_tool_forbidden', '%s is not an undo tool' % tool_name)
        spec = RECIPE_UNDO[(kind, tool_name)]
    else:
        if verdict != 'call':
            _fail('recipe_tool_forbidden', '%s.%s cannot be used here'
                  % (kind, tool_name))
        spec = RECIPE_TOOLS[(kind, tool_name)]
    if role in ('check', 'precheck'):
        tool = get_tool(integration['name'], tool_name)
        ok, why = is_read_integration_call(tool or {}, (tool or {}).get('transport') or 'http',
                                           call.get('args') or {})
        if not ok:
            _fail('recipe_check_not_read', '%s is not a read (%s)' % (tool_name, why))
    body = validate_args(spec, call.get('args') or {}, declared, where=where)
    return integration, kind, tool_name, body, spec


def _access_list_value(value):
    """W2: `{"public": true}` or `{"id": int, "name": str}` — nothing else."""
    if not isinstance(value, dict):
        _fail('recipe_access_list_invalid', 'access_list must be an object')
    if set(value) == {'public'}:
        if value['public'] is not True:
            _fail('recipe_access_list_invalid', 'public must be true')
        return {'public': True, 'access_list_id': 0,
                'label': 'Public (no access list)'}
    if set(value) == {'id', 'name'}:
        ident, name = value.get('id'), value.get('name')
        if isinstance(ident, bool) or not isinstance(ident, int) \
                or not 1 <= ident <= 2147483647:
            _fail('recipe_access_list_invalid', 'access_list id is out of range')
        if not isinstance(name, str) or not 1 <= len(name) <= 80 \
                or any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in name):
            _fail('recipe_access_list_invalid', 'access_list name is invalid')
        return {'id': ident, 'name': name, 'access_list_id': ident,
                'label': '%s (#%d)' % (name, ident)}
    _fail('recipe_access_list_invalid',
          'access_list must be {"public": true} or {"id": int, "name": str}')


def verify_access_list(integration, access_list):
    """W2: the id and name must exist in NPM, checked server-side.

    Not a recipe tool: this is the dashboard's own read, so no agent can see or
    select an access list through it.
    """
    if access_list.get('public'):
        return access_list
    tool = {
        'name': 'eshu_access_lists', 'method': 'GET',
        'path_template': '/nginx/access-lists', 'params': [], 'enabled': 1,
        'transport': 'http', 'read_only': True,
    }
    try:
        res = execute_integration_call(integration, tool, {}, agent='recipe')
        body = res.get('body') or ''
        rows = json.loads(body) if body else []
    except (ProxyError, ValueError):
        _fail('recipe_access_list_unknown',
              'NPM access lists could not be read; refusing the version')
    if not isinstance(rows, list):
        _fail('recipe_access_list_unknown', 'NPM returned no access list')
    for row in rows:
        if isinstance(row, dict) and row.get('id') == access_list['id'] \
                and str(row.get('name') or '') == access_list['name']:
            return access_list
    _fail('recipe_access_list_unknown',
          'access list %d/%r does not exist in NPM'
          % (access_list['id'], access_list['name']))


def _ssh_spec(ssh, declared, where):
    if not isinstance(ssh, dict) or set(ssh) != {'host', 'command'}:
        _fail('recipe_invalid', '%s needs host and command' % where)
    host = ssh.get('host')
    if not isinstance(host, dict) or set(host) != {'enum'} \
            or not isinstance(host['enum'], list) or len(host['enum']) != 1:
        _fail('recipe_invalid', '%s.host must be {"enum": [one host]}' % where)
    name = str(host['enum'][0])
    if not HOST_RE.fullmatch(name):
        _fail('recipe_invalid', '%s.host is not a host name or IP' % where)
    from core.ssh_exec import resolve_gateway
    if not resolve_gateway(name):
        _fail('recipe_host_unknown', '%s is not a registered gateway' % name)
    validate_template(ssh.get('command'), declared)
    validate_slot_step(ssh.get('command'), declared)
    return {'host': {'enum': [name]}, 'command': ssh['command']}


def _predicate(pred, declared, depth=0):
    if not isinstance(pred, dict) or set(pred) not in _PRED_KEYS:
        _fail('recipe_invalid', 'a pass predicate is not one of the known shapes')
    if depth > 2:
        _fail('recipe_invalid', 'predicates nest at most two deep')
    if 'all' in pred or 'any' in pred:
        items = pred.get('all') or pred.get('any')
        if not isinstance(items, list) or not 1 <= len(items) <= 4:
            _fail('recipe_invalid', 'all/any needs 1–4 predicates')
        return {('all' if 'all' in pred else 'any'): 
                [_predicate(item, declared, depth + 1) for item in items]}
    if 'field' in pred:
        path = pred.get('field')
        if not isinstance(path, str) or not PARAM_PATH.fullmatch(path):
            _fail('recipe_invalid', 'field must be a dotted path')
        op = [k for k in pred if k != 'field'][0]
        value = pred[op]
        if op == 'present' and value is not True:
            _fail('recipe_invalid', 'present must be true')
        if op in ('equals', 'in'):
            _value_scalar(value)
        if op == 'contains':
            _contains_spec(value, declared)
    return pred


def _value_scalar(value):
    if isinstance(value, dict):
        if set(value) != {'param'}:
            _fail('recipe_invalid', 'only a param slot is allowed here')
        return
    if isinstance(value, (str, int, float, bool)):
        return
    _fail('recipe_invalid', 'the value must be a scalar or a param slot')


def _contains_spec(value, declared):
    if isinstance(value, dict):
        if set(value) == {'param'} or set(value) == {'key', 'value'} \
                or set(value) == {'where'}:
            return
        _fail('recipe_invalid', 'contains needs a scalar, {"key","value"} or {"where"}')
    if isinstance(value, (str, int, float, bool)):
        return
    _fail('recipe_invalid', 'contains needs a scalar or an object')


def _check_block(block, declared, where):
    if block is None or block == 'self':
        return block
    if not isinstance(block, dict) or block.get('kind') not in ('tool', 'ssh'):
        _fail('recipe_invalid', '%s must be "self" or a check' % where)
    kind = block['kind']
    if kind == 'tool':
        _tool_role(block.get('call') or {}, declared, 'check', where)
    else:
        _ssh_spec(block.get('ssh') or {}, declared, where)
    _predicate(block.get('pass') or {}, declared)
    return block


def _access_lists_in(doc, remote):
    for step in doc['steps']:
        if step.get('kind') != 'tool':
            continue
        call = step.get('call') or {}
        integration = _resolve_integration(call.get('integration'))
        kind = str(integration.get('kind') or '').lower()
        if (kind, str(call.get('tool') or '')) not in ACCESS_LIST_TOOLS:
            continue
        body = (call.get('args') or {}).get('body') or {}
        if 'access_list' not in body:
            _fail('recipe_access_list_required',
                  'create_proxy_host requires an explicit access_list')
        value = _access_list_value(body['access_list'])
        if remote:
            verify_access_list(integration, value)


def validate_document(document, remote=True):
    """Validate the whole document; raises RecipeError with a stable code."""
    try:
        return _validate_document(document, remote)
    except RecipeParamError as exc:
        raise RecipeError(exc.code, exc.message)


def _validate_document(document, remote=True):
    doc = copy.deepcopy(_doc(document))
    name = str(doc.get('name') or '')
    if not NAME_RE.fullmatch(name):
        _fail('recipe_invalid', 'the name must match [a-z][a-z0-9_]{2,63}')
    doc['name'] = name
    doc['description'] = str(doc.get('description') or '')[:500]
    doc['parameters'] = _parameters(doc.get('parameters') or [])
    declared = {spec['name'] for spec in doc['parameters']}
    allowed = doc.get('allowed_agents') or []
    if not isinstance(allowed, list) or any(not isinstance(a, str) for a in allowed):
        _fail('recipe_invalid', 'allowed_agents must be a list of names')
    doc['allowed_agents'] = [a[:64] for a in allowed]

    preconditions = doc.get('preconditions') or []
    if not isinstance(preconditions, list) or len(preconditions) > 3:
        _fail('recipe_invalid', 'at most three preconditions')
    for pre in preconditions:
        if not isinstance(pre, dict) or pre.get('kind') != 'tool':
            _fail('recipe_invalid', 'a precondition must be a tool read')
        _tool_role(pre.get('call') or {}, declared, 'precheck', 'precondition')
        _predicate(pre.get('pass') or {}, declared)

    steps = doc.get('steps')
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        _fail('too_many_steps', 'a recipe has 1–%d steps' % MAX_STEPS)
    seen = set()
    ssh_count = 0
    for step in steps:
        if not isinstance(step, dict):
            _fail('recipe_invalid', 'each step must be an object')
        step_id = str(step.get('id') or '')
        if not STEP_ID_RE.fullmatch(step_id) or step_id in seen:
            _fail('recipe_invalid', 'step ids must be unique and plain')
        seen.add(step_id)
        kind = step.get('kind')
        if kind not in ('tool', 'ssh'):
            _fail('recipe_invalid', 'a step is tool or ssh')
        step['title'] = str(step.get('title') or '')[:120]
        if kind == 'tool':
            integration, ikind, tool_name, body, spec = _tool_role(
                step.get('call') or {}, declared, 'step', 'step %s' % step_id)
        else:
            ssh_count += 1
            if ssh_count > MAX_SSH_STEPS:
                _fail('too_many_ssh_steps', 'at most %d SSH steps' % MAX_SSH_STEPS)
            step['ssh'] = _ssh_spec(step.get('ssh') or {}, declared,
                                    'step %s' % step_id)
        undo = step.get('undo')
        if undo is not None:
            if not isinstance(undo, dict) or undo.get('kind') != 'tool':
                _fail('recipe_invalid', 'an undo is a tool call or null')
            u_integration, u_kind, u_tool, _, _ = _tool_role(
                undo.get('call') or {}, declared, 'undo', 'undo of %s' % step_id)
            if kind != 'tool' or (u_kind, u_tool) != spec.get('undo'):
                _fail('recipe_tool_forbidden',
                      'the undo of step %s must be its declared partner' % step_id)
        step['precheck'] = _check_block(step.get('precheck'), declared,
                                        'precheck of %s' % step_id)
        if isinstance(step['precheck'], dict) and step['precheck'].get('kind') == 'ssh':
            _fail('recipe_precheck_kind', 'a precheck must be a tool read')
        step['check'] = _check_block(step.get('check'), declared,
                                     'check of %s' % step_id)
    if doc.get('preconditions') is None:
        doc['preconditions'] = []
    if doc.get('allowed_agents') is None:
        doc['allowed_agents'] = []
    _access_lists_in(doc, remote)
    return doc


# ── proposing and approving ────────────────────────────────────────────

def propose(document, owner='', agent=''):
    """An agent (or the operator) submits a draft; never runnable."""
    doc = validate_document(document)
    recipe = get_recipe(name=doc['name'])
    if recipe:
        version = add_version(recipe['id'], doc)
    else:
        recipe = create_recipe(doc['name'], owner, doc)
        version = get_version(recipe['current_version_id'])
    record_audit_event('recipe_proposed', details='Recipe %s v%d proposed by %s'
                       % (doc['name'], version['version'], agent or owner or 'operator'),
                       actor=(agent or owner or '')[:64])
    return {'recipe_id': recipe['id'], 'version': version['version'],
            'content_hash': version['content_hash'], 'status': 'draft'}


def approve(recipe_id, version, actor='operator'):
    recipe = get_recipe(recipe_id)
    if not recipe:
        _fail('not_found', 'recipe not found')
    from db.recipes import get_version_by_number
    ver = get_version_by_number(recipe['id'], int(version))
    if not ver:
        _fail('not_found', 'version not found')
    # Re-validate the stored document against the live registry/allowlists.
    validate_document(ver['document'])
    approved = approve_version(recipe['id'], int(version), actor)
    record_audit_event('recipe_approved', details='Recipe %s v%d approved by %s'
                       % (recipe['name'], int(version), actor), actor=actor[:64])
    return approved


# ── substitution and the plan ──────────────────────────────────────────

def _filled(document, values):
    doc = copy.deepcopy(document)
    declared = {p['name'] for p in doc['parameters']}
    for step in doc['steps']:
        step['_had_slot'] = False
        step['_check_had_slot'] = False
        if step.get('kind') == 'ssh':
            step['_had_slot'] = bool(PLACEHOLDER.search(step['ssh']['command']))
            step['ssh']['command'] = substitute_command(
                step['ssh']['command'], values, declared)
        else:
            step['call']['args'] = substitute_args(step['call']['args'], values)
        for which in ('precheck', 'check'):
            block = step.get(which)
            if isinstance(block, dict):
                if block.get('kind') == 'ssh':
                    if which == 'check':
                        step['_check_had_slot'] = bool(
                            PLACEHOLDER.search(block['ssh']['command']))
                    block['ssh']['command'] = substitute_command(
                        block['ssh']['command'], values, declared)
                else:
                    block['call']['args'] = substitute_args(block['call']['args'], values)
                if 'pass' in block:
                    block['pass'] = substitute_args(block['pass'], values)
        undo = step.get('undo')
        if isinstance(undo, dict):
            undo['call']['args'] = substitute_args(
                undo['call']['args'], values, keep_results=True)
    for pre in doc.get('preconditions') or []:
        pre['call']['args'] = substitute_args(pre['call']['args'], values)
        pre['pass'] = substitute_args(pre['pass'], values)
    return doc


def _check_filled(command, had_slot):
    """Run-time re-check on the bytes that will actually execute (V1/Y1)."""
    bd = breakdown(command)
    if not bd.get('complete'):
        _fail('recipe_step_failed', 'the filled command does not parse as complete')
    steps = bd.get('steps') or []
    if len(steps) != 1 or SEPARATORS.search(command):
        _fail('recipe_step_failed', 'the filled command is not a single command')
    if had_slot:
        step = steps[0]
        if step.get('context'):
            _fail('recipe_step_failed', 'the filled command gained a wrapper')
        verb = (str(step.get('text') or '').split() or [''])[0].lower()
        if verb not in SLOT_VERBS:
            _fail('recipe_step_failed',
                  'the filled command does not run an allowlisted lookup')


def _host_name(ssh):
    return ssh['host']['enum'][0]


def _plan_items(doc, values, run_id):
    items = []
    for index, step in enumerate(doc['steps'], 1):
        step_id = step['id']
        title = step.get('title') or step_id
        if step['kind'] == 'ssh':
            command = step['ssh']['command']
            host = _host_name(step['ssh'])
            _check_filled(command, step.get('_had_slot'))
            items.append({'kind': 'ssh', 'host': host, 'command': command,
                          'max_uses': 1, 'step_id': step_id})
        else:
            body = _args_for_execution(step['call'], step.get('undo'))
            digest = _args_hash(body)
            summary = ''
            inner = (step['call'].get('args') or {}).get('body') or {}
            if 'access_list' in inner:
                summary = 'Access list: %s' % _access_list_value(
                    inner['access_list'])['label']
            items.append({'kind': 'mcp', 'host': '*',
                          'command': 'recipe: %s' % title,
                          'integration': step['call']['integration'],
                          'tool': step['call']['tool'],
                          'args_hash': digest, 'summary': summary,
                          'args_json': _display(body), 'step_id': step_id})
        check = step.get('check')
        if isinstance(check, dict) and check.get('kind') == 'ssh':
            items.append({'kind': 'ssh', 'host': _host_name(check['ssh']),
                          'command': check['ssh']['command'], 'max_uses': 1,
                          'step_id': '%s:check' % step_id})
    return items


def _template_of(step, which):
    return (step.get(which) or {}).get('command') or ''


def _args_for_execution(call, undo=None):
    body = copy.deepcopy((call or {}).get('args') or {})
    integration = get_integration(str((call or {}).get('integration') or ''))
    kind = str((integration or {}).get('kind') or '').lower()
    if (kind, str((call or {}).get('tool') or '')) in ACCESS_LIST_TOOLS:
        inner = body.get('body') or {}
        access = inner.pop('access_list', None)
        if access is not None:
            inner['access_list_id'] = 0 if access.get('public') else int(access['id'])
        body['body'] = inner
    return body


def _args_hash(args):
    from db.session_plans import args_hash
    return args_hash(args)


def _display(args):
    from core.secret_scrub import scrub_payload
    text = json.dumps(scrub_payload(args or {}), sort_keys=True,
                      separators=(',', ':'))
    return text[:2000]


# ── preconditions, checks and steps ────────────────────────────────────

def _last_value(value, path):
    for part in str(path or '').split('.'):
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            raise RecipeError('expect_path_missing', 'no %s in the result' % path)
    return value


def _contains(container, spec):
    if not isinstance(container, list):
        return False
    if isinstance(spec, dict) and set(spec) == {'key', 'value'}:
        key, wanted = spec['key'], spec['value']
        return any(isinstance(item, dict) and item.get(key) == wanted
                   for item in container)
    if isinstance(spec, dict) and set(spec) == {'where'}:
        wanted = spec['where']
        for item in container:
            if not isinstance(item, dict):
                continue
            if all((wanted[key] in item.get(key, []))
                   if isinstance(item.get(key), list) else item.get(key) == wanted[key]
                   for key in wanted):
                return True
        return False
    return any(item == spec for item in container)


def evaluate_pass(pred, value=None, exit_code=None, status_code=None):
    """The closed predicate set (R4). True/False; unevaluable fails closed."""
    if 'exit_code' in pred:
        return exit_code is not None and exit_code == pred['exit_code']
    if 'status_code' in pred:
        return status_code is not None and status_code == pred['status_code']
    if 'all' in pred:
        return all(evaluate_pass(item, value, exit_code, status_code)
                   for item in pred['all'])
    if 'any' in pred:
        return any(evaluate_pass(item, value, exit_code, status_code)
                   for item in pred['any'])
    if 'field' in pred:
        try:
            found = _last_value(value, pred['field'])
        except RecipeError:
            return False
        if 'present' in pred:
            return found is not None
        if 'equals' in pred:
            return _same(found, pred['equals'])
        if 'in' in pred:
            return isinstance(pred['in'], list) and any(
                _same(found, item) for item in pred['in'])
        if 'contains' in pred:
            return _contains(found, pred['contains'])
    return False


def _same(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    return type(a) is type(b) and a == b


def _agent_row(name):
    """The run's agent, with its current policy (AW1/AW2)."""
    if not name:
        return None
    from db.agent_policies import get_agent_policy
    from db.agent_tokens import get_agent_by_name
    agent = get_agent_by_name(name)
    if not agent:
        return None
    agent['policy'] = get_agent_policy(agent['id'])
    return agent


def _agent_standing(run, integration_name='', tool_name='', args=None,
                    tool=None, integration=None):
    """AW2: the agent's standing, re-read before every step and undo step.

    The operator approved the run's exact arguments, so the *approval* gate is
    correctly skipped — but an agent the operator disabled, revoked or
    restricted after the approval must not keep writing. Returns the agent row
    (or None for an operator-started run, which has no agent identity).
    """
    if (run.get('origin') or 'agent') == 'operator':
        return None
    name = (run.get('agent') or '').strip()
    agent = _agent_row(name)
    if not agent:
        _fail('recipe_agent_unknown',
              'the agent %r no longer exists; the run stops' % name)
    if agent.get('revoked'):
        _fail('recipe_agent_revoked',
              'the agent %r was revoked; the run stops' % name)
    if integration_name:
        from core.agent_policy import evaluate_call
        allowed, why = evaluate_call(agent, integration_name, tool_name,
                                     args or {}, tool, integration)
        if not allowed:
            _fail('recipe_agent_policy',
                  'the agent may no longer call %s.%s: %s'
                  % (integration_name, tool_name, why or 'refused'))
    return agent


def _run_tool_call(run, integration_name, tool_name, args, step_id):
    integration = get_integration(integration_name)
    tool = get_tool(integration_name, tool_name)
    # AW2: before the call itself, not only before the step — a caller that
    # stubs this function is a test, and the loop re-checks as well.
    _agent_standing(run, integration_name, tool_name, args, tool, integration)
    if not integration or not integration.get('enabled') or not tool \
            or not tool.get('enabled'):
        _fail('recipe_tool_missing', '%s.%s is missing or disabled'
              % (integration_name, tool_name))
    try:
        res = execute_integration_call(
            integration, tool, args or {}, agent=(run.get('agent') or 'recipe'),
            reason='recipe:%d:%s' % (int(run['id']), step_id))
    except ProxyError as exc:
        _fail('recipe_step_failed', str(exc.message or 'integration refused'))
    status = res.get('status_code')
    if res.get('error') or (isinstance(status, int) and not 200 <= status < 300):
        _fail('recipe_step_failed',
              str(res.get('error') or 'integration refused (HTTP %s)' % status))
    body = res.get('body') or ''
    try:
        value = json.loads(body) if body else None
    except ValueError:
        value = body
    return value, status, res


def _run_ssh(command, host, run, step_id, had_slot=False):
    from core.ssh_exec import ssh_exec
    _check_filled(command, had_slot)
    limit = get_run_limit_settings()
    ip = _gateway_ip(host)
    # F19/AF1: a recipe step runs unattended under the row's limit, so a
    # gateway that cannot report `run_limits: v1` never gets one (the
    # reservation would not be handed out either).
    if (get_gateway_run_limits(ip) or '').strip() != 'v1':
        _fail('recipe_no_limits',
              'the gateway does not report run_limits v1; the step cannot run bounded')
    # AW1: an SSH step needs an agent identity, because the per-agent host
    # allowlist and ssh_mode are what bound a command the engine runs without
    # the operator watching. An operator-started run has none, so it is
    # refused at start and again here; it does not get a made-up identity.
    if (run.get('origin') or 'agent') == 'operator':
        _fail('recipe_operator_ssh',
              'an operator-started run cannot use SSH steps; start it as an '
              'agent so the step has a host allowlist')
    agent = _agent_standing(run)
    req_id = create_recipe_request(
        ip, command, int(run['id']), step_id,
        session_id=run.get('session_id') or '',
        agent=run.get('agent') or '',
        run_limit=json.dumps(limit, separators=(',', ':')))

    def _expire_reservation():
        # AW3: a reservation the engine never got to use must not stay
        # claimable for its 90 s TTL.
        expire_recipe_reservation(req_id)

    try:
        result = ssh_exec(_gateway_ip(host), command,
                          session_id=run.get('session_id') or '',
                          timeout=STEP_TIMEOUT, wait=False, agent=agent,
                          force_jit=True)
    except Exception as exc:  # noqa: BLE001 — a runner failure is a step failure
        _expire_reservation()
        _fail('recipe_step_failed', str(exc)[:200])
    finally:
        set_step(run['id'], step_id, request_id=req_id)
    if result.get('status') == 'pending':
        _expire_reservation()
        _fail('recipe_step_failed',
              'the gateway did not claim the reserved request')
    if result.get('status') != 'ran':
        _expire_reservation()
        _fail('recipe_step_failed',
              str(result.get('message') or result.get('reason') or 'not run'))
    return result.get('exit_code'), result.get('output') or ''


def _gateway_ip(host):
    from core.ssh_exec import resolve_gateway
    gateway = resolve_gateway(host)
    if not gateway:
        _fail('recipe_host_unknown', '%s is not a registered gateway' % host)
    return gateway.get('ip') or host


def _store(value):
    from core.secret_scrub import scrub_payload
    try:
        text = json.dumps(scrub_payload(value), ensure_ascii=False)
    except (TypeError, ValueError):
        text = ''
    return text[:MAX_STORE]


def _trim(value, params):
    """Keep the element a check matched, not the whole list (smaller point)."""
    if not isinstance(value, dict):
        return value
    wanted = [str(v) for v in (params or {}).values() if isinstance(v, (str, int))]
    for key in ('items', 'hosts'):
        rows = value.get(key)
        if not isinstance(rows, list):
            continue
        kept = []
        for row in rows:
            text = json.dumps(row, ensure_ascii=False)
            if any(str(w) in text for w in wanted):
                kept.append(row)
        if kept:
            return {**value, key: kept[:5]}
    return value


# ── the run ────────────────────────────────────────────────────────────

def start_run(recipe_name, params, agent='', session_id='', operator='',
              origin=''):
    """Propose one run of an approved recipe.

    AW5: `origin` is the caller's, never inferred from an empty agent name.
    An MCP caller with no agent identity must pass `origin='agent'` and is
    refused (`recipe_no_agent`); the operator's dashboard route passes
    `origin='operator'`, and that standing is never available to MCP.
    """
    if origin not in ('agent', 'operator'):
        _fail('recipe_origin_required',
              "start_run needs origin='agent' or 'operator'")
    recipe = get_recipe(name=recipe_name) if not str(recipe_name).isdigit() \
        else get_recipe(recipe_id=recipe_name)
    if not recipe:
        _fail('not_found', 'recipe not found')
    if recipe.get('status') != 'approved' or not recipe.get('current_version_id'):
        _fail('recipe_not_approved', 'the recipe has no approved version')
    version = get_version(recipe['current_version_id'])
    if not version or not version.get('approved_at'):
        _fail('recipe_not_approved', 'the current version is not approved')
    document = validate_document(version['document'], remote=False)
    allowed = document.get('allowed_agents') or []
    if origin == 'agent':
        # AW5: the caller's standing is the agent's. Without a resolvable
        # row there is no host allowlist and no policy, so refuse before any
        # precondition runs and before a run or plan row exists.
        if not (agent or '').strip():
            _fail('recipe_no_agent',
                  'no agent identity on this call; use an agent token')
        if not _agent_row(agent):
            _fail('recipe_no_agent',
                  'the agent %r has no token row' % agent)
        if allowed and agent not in allowed:
            _fail('recipe_agent_not_allowed',
                  'this recipe is not available to that agent')
    elif allowed:
        pass  # the operator may always start an approved recipe
    try:
        values = resolve_values(document, params)
        filled = _filled(document, values)
    except RecipeParamError as exc:
        _fail(exc.code, exc.message)
    # AW1: an operator-started run has no agent identity, so an SSH step in
    # it can never run. Refused at start, before a precondition runs and
    # before the operator is shown a plan whose step could not happen.
    if origin == 'operator':
        for step in filled['steps']:
            if step['kind'] == 'ssh' or (
                    isinstance(step.get('check'), dict)
                    and step['check'].get('kind') == 'ssh'):
                _fail('recipe_operator_ssh',
                      'an operator-started run cannot use SSH steps; start it '
                      'as an agent so the step has a host allowlist')
    for pre in filled.get('preconditions') or []:
        value, _, _ = _run_tool_call(
            {'id': 0, 'agent': agent or operator or 'operator',
             'origin': origin},
            pre['call']['integration'], pre['call']['tool'],
            _args_for_execution(pre['call']), 'precondition')
        if not evaluate_pass(pre['pass'], value=value):
            _fail('recipe_precondition_failed',
                  'the precondition for %s does not hold' % document['name'])
    sid = (session_id or '').strip() or ('recipe-op-%d' % int(time.time() * 1000))
    from db.session_plans import open_plan_for
    if open_plan_for(sid):
        # R5: one run approval per session at a time; a second run waits rather
        # than replacing (or queueing behind) the plan the operator is reading.
        _fail('plan_waiting', 'this session already has a plan awaiting approval')
    run_id = create_run(recipe, version, sid, agent or operator or '',
                        values, kind='run', origin=origin)
    for step in filled['steps']:
        add_run_step(run_id, step['id'], step['kind'], step.get('title') or '')
        if isinstance(step.get('check'), dict) and step['check'].get('kind') == 'ssh':
            add_run_step(run_id, '%s:check' % step['id'], 'ssh',
                         '%s (check)' % (step.get('title') or step['id']))
    items = _plan_items(filled, values, run_id)
    plan = create_recipe_plan(sid, run_id, items)
    set_run(run_id, plan_id=plan['plan_id'])
    record_audit_event('recipe_run_proposed', details=
                       'Recipe %s v%d run #%d proposed (plan #%d, session %s)'
                       % (recipe['name'], version['version'], run_id,
                          plan['plan_id'], sid))
    return {'ok': True, 'status': 'pending', 'run_id': run_id,
            'plan_id': plan['plan_id'], 'preview': preview_run(run_id)}


def preview_run(run_id):
    run = get_run(run_id)
    if not run:
        return None
    recipe = get_recipe(run['recipe_id'])
    version = get_version(run['version_id'])
    document = validate_document(version['document'], remote=False)
    filled = _filled(document, run['parameters'])
    return {'run_id': run['id'], 'recipe': recipe['name'],
            'version': version['version'],
            'content_hash': run['content_hash'],
            'status': run['status'], 'steps': _preview_steps(filled),
            'steps_state': run['steps']}


def _preview_steps(filled):
    out = []
    for step in filled['steps']:
        entry = {'id': step['id'], 'kind': step['kind'],
                 'title': step.get('title') or ''}
        if step['kind'] == 'ssh':
            entry['host'] = _host_name(step['ssh'])
            entry['command'] = step['ssh']['command']
            entry['breakdown'] = breakdown(step['ssh']['command'])
            entry['slot_step'] = bool(step.get('_had_slot'))
        else:
            entry['integration'] = step['call']['integration']
            entry['tool'] = step['call']['tool']
            entry['args'] = _args_for_execution(step['call'])
            entry['args_display'] = _display(entry['args'])
            if 'access_list' in (step['call'].get('args') or {}).get('body', {}):
                entry['access_list'] = _access_list_value(
                    step['call']['args']['body']['access_list'])['label']
        check = step.get('check')
        entry['check'] = ('self' if check == 'self'
                          else _display(check) if isinstance(check, dict) else None)
        entry['undo'] = _display(step['undo']) if step.get('undo') else None
        out.append(entry)
    return out


def _evaluate_check(run, step, step_id, value, exit_code, status_code):
    check = step.get('check')
    if check is None:
        return True, ''
    if check == 'self':
        if step['kind'] == 'ssh':
            return exit_code == 0, 'exit %s' % exit_code
        return True, ''
    if check['kind'] == 'ssh':
        claimed = claim_recipe_item(run['id'], '%s:check' % step_id)
        if not claimed:
            _fail('recipe_step_failed', 'the check item could not be claimed')
        check_exit, _ = _run_ssh(check['ssh']['command'], _host_name(check['ssh']),
                                 run, '%s:check' % step_id,
                                 had_slot=step.get('_check_had_slot'))
        ok = evaluate_pass(check['pass'], exit_code=check_exit)
        return ok, 'check exit %s' % check_exit
    check_value, check_status, _ = _run_tool_call(
        run, check['call']['integration'], check['call']['tool'],
        _args_for_execution(check['call']), '%s:check' % step_id)
    ok = evaluate_pass(check['pass'], value=check_value, status_code=check_status)
    return ok, _store(check_value)


def execute_run(run_id):
    with _RUN_LOCK:
        return _execute_run_locked(run_id)


def _execute_run_locked(run_id):
    run = get_run(run_id)
    if not run or run.get('status') != 'pending':
        return
    plan = get_plan(run['plan_id']) if run.get('plan_id') else None
    if not plan or plan.get('status') != 'approved':
        return
    recipe = get_recipe(run['recipe_id'])
    version = get_version(run['version_id'])
    document = validate_document(version['document'], remote=False)
    filled = _filled(document, run['parameters'])
    set_run(run['id'], status='running', started_at=int(time.time()))
    record_audit_event('recipe_run_started', details=
                       'Recipe %s run #%d started (plan #%d)'
                       % (recipe['name'], run['id'], plan['id']))
    for index, step in enumerate(filled['steps']):
        step_id = step['id']
        if get_trigger_freeze() is not None:
            _stop_run(run, 'stopped', step_id, 'a freeze is on')
            return
        # AW2: the standing check comes before the item is claimed, so a
        # revoked agent does not spend the approval it can no longer use.
        try:
            if step['kind'] == 'ssh':
                _agent_standing(run)
            else:
                _agent_standing(run, step['call']['integration'],
                                step['call']['tool'],
                                _args_for_execution(step['call']))
        except (RecipeError, RecipeParamError) as exc:
            set_step(run['id'], step_id, status='failed',
                     error=getattr(exc, 'message', str(exc))[:300])
            _stop_run(run, 'stopped', step_id,
                      getattr(exc, 'message', str(exc)))
            return
        if not claim_recipe_item(run['id'], step_id):
            _stop_run(run, 'stopped', step_id, 'the step item is not claimable')
            return
        set_step(run['id'], step_id, status='running')
        try:
            created = True
            value = status_code = exit_code = None
            precheck = step.get('precheck')
            if precheck is not None:
                pre_value, pre_status, _ = _run_tool_call(
                    run, precheck['call']['integration'],
                    precheck['call']['tool'],
                    _args_for_execution(precheck['call']), '%s:pre' % step_id)
                if evaluate_pass(precheck['pass'], value=pre_value,
                                 status_code=pre_status):
                    created = False
                    set_step(run['id'], step_id, status='already-present',
                             created=0)
            if created:
                if step['kind'] == 'ssh':
                    exit_code, output = _run_ssh(step['ssh']['command'],
                                                 _host_name(step['ssh']), run,
                                                 step_id, step.get('_had_slot'))
                    value, status_code = None, None
                    set_step(run['id'], step_id, status='ok', created=1,
                             result_json=_store({'exit_code': exit_code,
                                                 'output': output}),
                             check_state='')
                else:
                    value, status_code, _ = _run_tool_call(
                        run, step['call']['integration'],
                        step['call']['tool'],
                        _args_for_execution(step['call']), step_id)
                    exit_code = None
                    set_step(run['id'], step_id, status='ok', created=1,
                             result_json=_store(_trim(value, run['parameters'])))
            ok, detail = _evaluate_check(run, step, step_id, value, exit_code,
                                         status_code)
            set_step(run['id'], step_id, check_state='passed' if ok else 'failed')
            if not ok:
                _stop_run(run, 'failed', step_id, 'check failed: %s' % detail[:160])
                return
        except (RecipeError, RecipeParamError) as exc:
            message = getattr(exc, 'message', str(exc))
            set_step(run['id'], step_id, status='failed', error=message[:300])
            _stop_run(run, 'failed', step_id, message)
            return
    set_run(run['id'], status='ok', finished_at=int(time.time()))
    record_audit_event('recipe_run_finished', details=
                       'Recipe run #%d finished ok' % run['id'])


def _stop_run(run, status, failed_step, reason):
    set_run(run['id'], status=status, failed_step=failed_step or '',
            finished_at=int(time.time()))
    for step in get_run(run['id'])['steps']:
        if step['status'] in ('pending', 'running'):
            set_step(run['id'], step['step_id'], status='skipped')
    record_audit_event('recipe_step_failed', details=
                       'Recipe run #%d stopped at %s: %s'
                       % (run['id'], failed_step or '-', reason[:200]))


# ── rollback ───────────────────────────────────────────────────────────

def rollback(run_id, agent='', session_id=''):
    run = get_run(run_id)
    if not run or run.get('kind') != 'run':
        _fail('not_found', 'run not found')
    if run.get('status') not in ('ok', 'failed'):
        _fail('recipe_not_finished', 'only a finished run can be rolled back')
    recipe = get_recipe(run['recipe_id'])
    version = get_version(run['version_id'])
    document = validate_document(version['document'], remote=False)
    filled = _filled(document, run['parameters'])
    undo_steps = []
    for step in reversed(filled['steps']):
        state = [s for s in run['steps'] if s['step_id'] == step['id']]
        if not state or not state[0].get('created'):
            continue
        undo = step.get('undo')
        if not isinstance(undo, dict):
            continue
        stored = state[0].get('result_json') or ''
        try:
            result = json.loads(stored) if stored else {}
        except ValueError:
            result = {}
        try:
            args = substitute_args(undo['call']['args'], run['parameters'],
                                   results=result)
        except RecipeParamError as exc:
            _fail(exc.code, exc.message)
        undo_steps.append({'step_id': step['id'], 'integration': undo['call']['integration'],
                           'tool': undo['call']['tool'], 'args': args,
                           'title': 'undo %s' % (step.get('title') or step['id'])})
    if not undo_steps:
        _fail('rollback_nothing', 'no step of this run can be undone')
    rb_run = create_run(recipe, version, run['session_id'], run['agent'] or agent,
                        run['parameters'], kind='rollback', of_run_id=run['id'],
                        origin=run.get('origin') or 'agent')
    for undo in undo_steps:
        add_run_step(rb_run, undo['step_id'], 'tool', undo['title'])
    items = [{'kind': 'mcp', 'host': '*', 'command': 'undo: %s' % u['title'],
              'integration': u['integration'], 'tool': u['tool'],
              'args_hash': _args_hash(u['args']),
              'args_json': _display(u['args']), 'step_id': u['step_id']}
             for u in undo_steps]
    sid = (session_id or run['session_id'] or ('recipe-rb-%d' % rb_run))
    plan = create_recipe_plan(sid, rb_run, items)
    set_run(rb_run, plan_id=plan['plan_id'])
    record_audit_event('recipe_rollback_proposed', details=
                       'Recipe run #%d rollback #%d proposed (plan #%d)'
                       % (run['id'], rb_run, plan['plan_id']))
    return {'ok': True, 'status': 'pending', 'rollback_run_id': rb_run,
            'plan_id': plan['plan_id'], 'undo': undo_steps}


def execute_rollback(run_id):
    with _RUN_LOCK:
        return _execute_rollback_locked(run_id)


def _execute_rollback_locked(run_id):
    run = get_run(run_id)
    if not run or run.get('kind') != 'rollback' or run.get('status') != 'pending':
        return
    plan = get_plan(run['plan_id']) if run.get('plan_id') else None
    if not plan or plan.get('status') != 'approved':
        return
    original = get_run(run['of_run_id'])
    recipe = get_recipe(run['recipe_id'])
    version = get_version(run['version_id'])
    document = validate_document(version['document'], remote=False)
    filled = _filled(document, original['parameters'])
    step_by_id = {s['id']: s for s in filled['steps']}
    set_run(run['id'], status='running', started_at=int(time.time()))
    for step in run['steps']:
        source = step_by_id.get(step['step_id'])
        undo = (source or {}).get('undo') or {}
        if get_trigger_freeze() is not None:
            _stop_run(run, 'stopped', step['step_id'], 'a freeze is on')
            return
        stored = [s for s in original['steps'] if s['step_id'] == step['step_id']]
        try:
            result = json.loads((stored[0].get('result_json')
                                 if stored else '') or '{}')
        except ValueError:
            result = {}
        try:
            # AW6: the undo's real arguments (the stored result substituted
            # in), so an arg_policy that limits them is judged on the call
            # that is about to run.
            args = substitute_args(undo.get('call', {}).get('args') or {},
                                   original['parameters'], results=result)
        except RecipeParamError as exc:
            set_step(run['id'], step['step_id'], status='failed',
                     error=exc.message[:300])
            _stop_run(run, 'stopped', step['step_id'], exc.message)
            return
        try:
            _agent_standing(run, (undo.get('call') or {}).get('integration')
                            or '', (undo.get('call') or {}).get('tool') or '',
                            args)
        except (RecipeError, RecipeParamError) as exc:
            set_step(run['id'], step['step_id'], status='failed',
                     error=getattr(exc, 'message', str(exc))[:300])
            _stop_run(run, 'stopped', step['step_id'],
                      getattr(exc, 'message', str(exc)))
            return
        if not claim_recipe_item(run['id'], step['step_id']):
            _stop_run(run, 'stopped', step['step_id'], 'the undo item is not claimable')
            return
        set_step(run['id'], step['step_id'], status='running')
        try:
            v4_check(undo, result, source, run)
            _run_tool_call(run, undo['call']['integration'],
                           undo['call']['tool'], args, step['step_id'])
            set_step(run['id'], step['step_id'], status='ok', check_state='passed')
        except (RecipeError, RecipeParamError) as exc:
            message = getattr(exc, 'message', str(exc))
            set_step(run['id'], step['step_id'], status='failed',
                     error=message[:300])
            _stop_run(run, 'stopped', step['step_id'], message)
            return
    set_run(run['id'], status='rolled_back', finished_at=int(time.time()))
    record_audit_event('recipe_rollback', details='Recipe run #%d rolled back'
                       % run['of_run_id'])


def v4_check(undo, result, source, run):
    """V4: re-read the object by its stored id before deleting it."""
    integration_name = (undo.get('call') or {}).get('integration')
    tool_name = (undo.get('call') or {}).get('tool')
    integration = get_integration(integration_name)
    kind = str((integration or {}).get('kind') or '').lower()
    if (kind, tool_name) != ('npm', 'delete_proxy_host'):
        return
    args = substitute_args(undo['call']['args'], run['parameters'], results=result)
    ident = args.get('id')
    if not isinstance(ident, int):
        _fail('rollback_target_changed', 'the undo has no object id')
    value, _, _ = _run_tool_call(run, integration_name, 'proxy_host',
                                 {'id': ident}, 'rollback')
    if not isinstance(value, dict) or integral(value.get('id')) != ident:
        _fail('rollback_target_changed', 'the object is gone or has a new id')
    written = (source.get('call') or {}).get('args') or {}
    inner = written.get('body') or {}
    domains = value.get('domain_names') if isinstance(value.get('domain_names'), list) else []
    wanted = (inner.get('domain_names') or [])
    if wanted and not all(domain in domains for domain in wanted):
        _fail('rollback_target_changed', 'the domains no longer match')
    if inner.get('forward_host') and value.get('forward_host') != inner['forward_host']:
        _fail('rollback_target_changed', 'the forward host no longer matches')
    if inner.get('forward_port') is not None \
            and value.get('forward_port') != inner['forward_port']:
        _fail('rollback_target_changed', 'the forward port no longer matches')


def integral(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ── the worker ─────────────────────────────────────────────────────────

_STOP = threading.Event()
_THREAD = None
_RUN_LOCK = threading.Lock()


def advance():
    """Start runs whose plan got approved; retire runs whose plan did not."""
    from db.recipes import expired_waiting_runs
    for run in expired_waiting_runs():
        set_run(run['id'], status='stopped', finished_at=int(time.time()))
    for run in pending_runs_for_approved_plans():
        try:
            if run.get('kind') == 'rollback':
                execute_rollback(run['id'])
            else:
                execute_run(run['id'])
        except Exception as exc:  # noqa: BLE001 — a worker must not die
            print('[recipes] run %s failed: %s' % (run.get('id'), exc), flush=True)


def _loop():
    while not _STOP.is_set():
        try:
            advance()
        except Exception as exc:  # noqa: BLE001
            print('[recipes] worker error: %s' % exc, flush=True)
        _STOP.wait(POLL_SECONDS)


def start_worker():
    global _THREAD
    if _THREAD and _THREAD.is_alive():
        return _THREAD
    _THREAD = threading.Thread(target=_loop, name='recipe-worker', daemon=True)
    _THREAD.start()
    return _THREAD


def stop_worker():
    _STOP.set()


def preview_version(recipe_id, version, params):
    """The review card's "preview with these values" (Q4)."""
    from db.recipes import get_version_by_number
    ver = get_version_by_number(int(recipe_id), int(version))
    if not ver:
        _fail('not_found', 'version not found')
    doc = validate_document(ver['document'], remote=False)
    values = resolve_values(doc, params or {})
    filled = _filled(doc, values)
    return {'version': ver['version'], 'content_hash': ver['content_hash'],
            'steps': _preview_steps(filled),
            'parameters': values}


def recipe_summary(recipe):
    from db.recipes import get_version
    version = get_version(recipe['current_version_id']) if recipe.get('current_version_id') else None
    return {'id': recipe['id'], 'name': recipe['name'],
            'status': recipe['status'], 'owner': recipe.get('owner') or '',
            'auto_run': False,
            'current_version': version['version'] if version else 0,
            'content_hash': version['content_hash'] if version else '',
            'approved_at': version['approved_at'] if version else 0,
            'approved_by': version['approved_by'] if version else ''}
