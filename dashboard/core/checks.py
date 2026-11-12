"""F22: changes carry a check.

A plan item may carry one **read** that runs after the item's command reports
success, and stops the plan when it fails. This module owns the whole path:

* `validate_check` — the closed shape, checked when the item is proposed and
  again before every run (the read policy can change between the two);
* `on_result` — the F5 hook. It writes `pending` and hands the item to the
  worker; it never runs anything itself;
* `_run_check` — the worker. It re-checks the agent's standing and the read
  policy, then runs the attempt through `ssh_exec` (under the agent) or
  `run_tool` (dashboard-side), with `check_attempts <= retries + 1`;
* the per-attempt **check token** (AV1): a random value on every attempt,
  stored only on the plan rows, never in a request row's reason and never in
  a view. An ssh attempt carries it as a signed v2 prefix field, so
  `receive_request` honours a `plan-check` claim only for a request the
  dashboard's own runner signed.

Every state transition is a single conditional UPDATE bound to the attempt's
token, so a restart, a duplicate result or a race cannot run a check twice.
"""
import json
import queue
import re
import secrets
import threading
import time

from core.cmd_parse import breakdown
from core.read_policy import is_read_integration_call, is_read_ssh
from db.misc import get_run_limit_settings
from db.requests import get_request_row
from db.session_plans import (
    CHECK_INTERNAL, PlanError, bump_check_attempt, create_plan, finish_check,
    finish_check_unclaimed, get_plan, get_plan_item, rotate_check_token,
    set_check_item_parent, set_check_item_token, set_check_pending,
    set_check_plan_refs, set_check_running, set_check_skipped, stop_plan,
)

MAX_WAIT_S = 30
MAX_RETRIES = 3
MAX_ATTEMPTS = MAX_RETRIES + 1
# (retries + 1) x (wait_s + the ssh/tool timeout). A stuck check becomes
# `not_run` after this, and is never re-dispatched.
WORST_CASE_SECONDS = MAX_ATTEMPTS * (MAX_WAIT_S + 30)
CHECK_ROW_LIMIT_SECONDS = 30
TOOL_TIMEOUT_SECONDS = 30
MAX_CHECK_COMMAND = 4096

OPS = ('eq', 'ne', 'in', 'lt', 'gt')
_PATH_PART = r'[A-Za-z0-9_-]+|\d+'
PATH_RE = re.compile(r'^(?:%s)(?:\.(?:%s)){0,3}$' % (_PATH_PART, _PATH_PART))
TOOL_RE = re.compile(r'^[A-Za-z0-9_.:-]{1,120}$')
_TOKEN_RE = re.compile(r'[A-Za-z0-9_-]{16,128}')

_QUEUE = queue.Queue(maxsize=64)
_STOP = threading.Event()
_THREADS = []
_STARTED = threading.Lock()
_WAIT_LOCK = threading.Lock()


class CheckError(ValueError):
    """A check the caller should see as a stable error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ── validation ─────────────────────────────────────────────────────────

def _int_bound(raw, default, lo, hi, code):
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise CheckError(code, '%s must be an integer' % code.split('_')[-1])
    if raw < lo or raw > hi:
        raise CheckError(code, 'must be between %d and %d' % (lo, hi))
    return raw


def _validate_expect(raw):
    if not isinstance(raw, dict):
        raise CheckError('invalid_expect', 'expect must be an object')
    extra = set(raw) - {'path', 'op', 'value'}
    if extra:
        raise CheckError('invalid_expect', 'expect has unknown keys')
    path = raw.get('path')
    if not isinstance(path, str) or not PATH_RE.fullmatch(path):
        raise CheckError('invalid_expect_path',
                         'path must be 1-4 dotted keys or indices')
    op = raw.get('op')
    if op not in OPS:
        raise CheckError('invalid_expect_op', 'op must be one of ' + '/'.join(OPS))
    value = raw.get('value')
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise CheckError('expect_value_type',
                         'value must be a string or a number')
    if op in ('lt', 'gt') and isinstance(value, str):
        raise CheckError('expect_value_type',
                         'lt/gt need a numeric value')
    if op == 'in' and not isinstance(value, (str, int, float)):
        raise CheckError('expect_value_type', 'in needs the member to look for')
    return {'path': path, 'op': op, 'value': value}


def validate_check(raw, remote=False):
    """The closed shape of one check; raises CheckError.

    `remote=True` is the run-time pass: the same rules, re-applied to the
    stored definition, plus the read policy again (a tool or a command that
    stopped being a read is `check_not_a_read`, never a run).
    """
    if not isinstance(raw, dict):
        raise CheckError('invalid_check', 'check must be an object')
    kind = str(raw.get('kind') or '').strip().lower()
    wait_s = _int_bound(raw.get('wait_s'), 0, 0, MAX_WAIT_S, 'invalid_wait')
    retries = _int_bound(raw.get('retries'), 0, 0, MAX_RETRIES,
                         'invalid_retries')
    if kind == 'ssh':
        command = raw.get('command')
        if not isinstance(command, str) or not command.strip():
            raise CheckError('invalid_check', 'an ssh check needs a command')
        if _bad_command_char(command) is not None:
            raise CheckError('invalid_check',
                             'the check command has control characters')
        command = command.strip()
        if len(command.encode('utf-8')) > MAX_CHECK_COMMAND:
            raise CheckError('check_too_long', 'the check command is too long')
        ok, why = is_read_ssh(breakdown(command))
        if not ok:
            raise CheckError('check_not_a_read',
                             why or 'a check must be a read')
        return {'kind': 'ssh', 'command': command, 'wait_s': wait_s,
                'retries': retries}
    if kind == 'tool':
        from db.integrations import get_integration, get_tool
        integration_name = str(raw.get('integration') or '').strip()
        tool_name = str(raw.get('tool') or '').strip()
        if not TOOL_RE.fullmatch(integration_name) or not TOOL_RE.fullmatch(tool_name):
            raise CheckError('invalid_check', 'integration and tool must be plain names')
        integration = get_integration(integration_name)
        tool = get_tool(integration_name, tool_name)
        if not integration or not integration.get('enabled'):
            raise CheckError('check_tool_missing',
                             '%s is missing or disabled' % integration_name)
        if not tool or not tool.get('enabled'):
            raise CheckError('check_tool_disabled',
                             '%s.%s is missing or disabled'
                             % (integration_name, tool_name))
        args = raw.get('args') or {}
        if not isinstance(args, dict):
            raise CheckError('invalid_check', 'args must be an object')
        ok, why = is_read_integration_call(
            tool, tool.get('transport') or 'http', args)
        if not ok:
            raise CheckError('check_not_a_read', why or 'a check must be a read')
        return {'kind': 'tool', 'integration': integration_name,
                'tool': tool_name, 'args': args,
                'expect': _validate_expect(raw.get('expect')),
                'wait_s': wait_s, 'retries': retries}
    raise CheckError('invalid_check', "check kind must be 'ssh' or 'tool'")


def _bad_command_char(command):
    for ch in command:
        if ch in (' ', '\t'):
            continue
        if ord(ch) < 0x20 or ord(ch) == 0x7f or ch.isspace():
            return ch
    return None


# ── expect ─────────────────────────────────────────────────────────────

def _walk(value, path):
    current = value
    for part in path.split('.'):
        if isinstance(current, list):
            if not part.isdigit():
                raise CheckError('expect_path_missing', 'a list needs an index')
            index = int(part)
            if index >= len(current):
                raise CheckError('expect_path_missing', 'index out of range')
            current = current[index]
        elif isinstance(current, dict):
            if part not in current:
                raise CheckError('expect_path_missing', 'key missing')
            current = current[part]
        else:
            raise CheckError('expect_path_missing', 'not an object or list')
    return current


def _same_scalar(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    return False


def evaluate_expect(expect, value):
    """(state, reason, observed, expected) for one tool result.

    `eq`/`ne` are scalar-exact with no cross-type coercion; `in` needs the
    resolved value to be a list; `lt`/`gt` need numbers on both sides. A
    missing path is `not_run`: we could not tell, which is not "it failed".
    """
    path, op, expected = expect['path'], expect['op'], expect['value']
    try:
        observed = _walk(value, path)
    except CheckError:
        return 'not_run', 'expect_path_missing', None, expected
    if op == 'eq':
        ok = _same_scalar(observed, expected)
    elif op == 'ne':
        ok = not _same_scalar(observed, expected)
    elif op == 'in':
        if not isinstance(observed, list):
            return 'not_run', 'expect_not_comparable', observed, expected
        ok = any(_same_scalar(item, expected) for item in observed)
    else:  # lt / gt
        if isinstance(observed, bool) or not isinstance(observed, (int, float)):
            return 'not_run', 'expect_not_comparable', observed, expected
        ok = observed < expected if op == 'lt' else observed > expected
    return ('passed' if ok else 'failed'), '', observed, expected


# ── the F5 hook ────────────────────────────────────────────────────────

def on_result(request_id: int, exit_code: int, limit_kind: str = '') -> None:
    """First result for a linked request: write `pending` and enqueue.

    Non-zero exit → the item's own failure: the check is `skipped` and the
    plan stops, with no check dispatched. A limiter stop cannot tell us
    whether the state is good, so it is `not_run` / `check_limit`.
    """
    try:
        row = get_request_row(int(request_id))
    except Exception:
        return
    if not row or not row.get('plan_item_id'):
        return
    item = get_plan_item(int(row['plan_item_id']))
    if not item or not (item.get('check_json') or ''):
        return
    plan = get_plan(int(item['plan_id']))
    if not plan:
        return
    if exit_code != 0:
        if set_check_skipped(item['id']):
            stop_plan(plan['id'], 'item_failed:%s' % item['id'])
            _audit('plan_stopped', plan, item, 'item_failed')
        return
    if (limit_kind or '') in ('time', 'bytes'):
        finish_check_unclaimed(item['id'], 'not_run', 'check_limit')
        if plan.get('status') == 'approved':
            stop_plan(plan['id'], 'check_not_run:%s:check_limit' % item['id'])
        _audit('check_stopped', plan, item, 'check_limit')
        return
    token = secrets.token_urlsafe(24)
    if not set_check_pending(item['id'], token):
        return   # a duplicate result, or the check already ran
    try:
        _QUEUE.put_nowait((item['id'], token))
    except queue.Full:
        # Stay `pending`; the sweep turns it into `not_run`, never a pass.
        pass


def _audit(event, plan, item, reason=''):
    try:
        from db.audit import record_audit_event
        record_audit_event(
            event,
            details=('Plan #%s item #%s check: %s'
                     % (plan.get('id'), item.get('id'), reason or event)))
    except Exception:
        pass


def card_warning(request_id: int):
    """AV2: does the plan behind this card have an open or failed check?

    Computed on demand for the one card the operator has open, never per row
    in the queue. `None` when the request is not linked or nothing is open.
    """
    try:
        row = get_request_row(int(request_id))
        if not row or not row.get('plan_item_id'):
            return None
        item = get_plan_item(int(row['plan_item_id']))
        if not item:
            return None
        plan = get_plan(int(item['plan_id']))
        if not plan:
            return None
        for other in plan.get('items') or []:
            if int(other['id']) > int(item['id']):
                continue
            if not other.get('check'):
                continue
            state = other.get('check_state') or ''
            if state in ('pending', 'running'):
                return {'state': 'open', 'item': other['id'],
                        'text': ('An earlier change in this plan (#%s) has a '
                                 'check open. Approve only if you know it is '
                                 'safe to continue.' % other['id'])}
            if state in ('failed', 'not_run'):
                return {'state': state, 'item': other['id'],
                        'text': ('An earlier change in this plan (#%s) has a '
                                 'check %s (%s). Approving this one runs it '
                                 'anyway.' % (other['id'], state,
                                              other.get('check_reason') or '-'))}
    except Exception:
        return None
    return None


# ── the worker ─────────────────────────────────────────────────────────

def _run_limit_json():
    """The check's row limit: min(30, the operator's default) seconds."""
    settings = get_run_limit_settings() or {}
    try:
        seconds = int(settings.get('seconds') or CHECK_ROW_LIMIT_SECONDS)
    except (TypeError, ValueError):
        seconds = CHECK_ROW_LIMIT_SECONDS
    limit = dict(settings)
    limit['seconds'] = max(1, min(CHECK_ROW_LIMIT_SECONDS, seconds))
    return json.dumps(limit, separators=(',', ':')), limit


def _wait_between(wait_s):
    if wait_s <= 0:
        return
    with _WAIT_LOCK:
        _STOP.wait(wait_s)


def _agent_standing(plan):
    """AR4: the agent's standing, re-read before every attempt."""
    from db.agent_policies import DEFAULT_SSH_MODE, get_agent_policy
    from db.agent_tokens import get_agent_by_name
    from db.sessions import get_session
    session = get_session(plan.get('session_id') or '')
    name = (session.get('agent') or '').strip() if session else ''
    if not name:
        raise CheckError('check_agent_inactive', 'the session has no agent')
    agent = get_agent_by_name(name)
    if not agent:
        raise CheckError('check_agent_inactive', 'the agent no longer exists')
    if agent.get('revoked'):
        raise CheckError('check_agent_inactive', 'the agent was revoked')
    agent['policy'] = get_agent_policy(agent['id'])
    agent['ssh_mode'] = (agent['policy'].get('ssh_mode') or DEFAULT_SSH_MODE)
    return agent


def _tripwire_frozen():
    """A tripwire freeze stops unattended work; an operator freeze does not.

    F24 has not shipped, so the key has no naming data yet: while it is set,
    a check is `not_run` / `check_frozen`. Stricter than the note on purpose —
    a fleet tripwire must not be outrun by a queued check.
    """
    try:
        from db.misc import get_meta_json
        value = get_meta_json('tripwire_freeze', '')
    except Exception:
        value = ''
    return value not in (None, '', 0, '0', False)


def _run_check(item_id, token):
    item = get_plan_item(int(item_id))
    if not item or (item.get('check_token') or '') != token:
        return
    plan = get_plan(int(item['plan_id']))
    if not plan:
        return
    if not set_check_running(item['id'], token, plan['id']):
        return   # a stale token, or another worker has it
    if plan.get('status') != 'approved':
        finish_check(item['id'], token, 'not_run', 'check_plan_over')
        return
    if _tripwire_frozen():
        _finish_not_run(item, token, plan, 'check_frozen')
        return
    try:
        check = validate_check(json.loads(item['check_json']), remote=True)
    except CheckError as exc:
        _finish_not_run(item, token, plan, exc.code)
        return
    except ValueError:
        _finish_not_run(item, token, plan, 'invalid_check')
        return
    try:
        agent = _agent_standing(plan)
    except CheckError as exc:
        _finish_not_run(item, token, plan, exc.code)
        return

    attempts = check['retries'] + 1
    state, reason, exit_code, observed, expected = '', '', None, None, None
    for attempt in range(attempts):
        attempt_token = token if attempt == 0 else secrets.token_urlsafe(24)
        if attempt and not rotate_check_token(item['id'], token, attempt_token):
            return
        token = attempt_token
        if not bump_check_attempt(item['id'], token, attempts):
            state, reason = 'not_run', 'check_attempts_exhausted'
            break
        if attempt:
            _wait_between(check['wait_s'])
        if check['kind'] == 'ssh':
            state, reason, exit_code = _ssh_attempt(plan, item, check, agent,
                                                    token)
        else:
            state, reason, observed, expected = _tool_attempt(plan, item, check,
                                                              agent, token)
        if state == 'passed':
            break
        if state == 'not_run':
            break
    if not state:
        state, reason = 'not_run', 'check_attempts_exhausted'
    if not finish_check(item['id'], token, state, reason, exit_code=exit_code,
                        observed=observed, expected=expected):
        return
    if state in ('failed', 'not_run') and plan.get('status') == 'approved':
        stop_plan(plan['id'], 'check_%s:%s:%s'
                  % ('failed' if state == 'failed' else 'not_run',
                     item['id'], reason or state))
        _audit('plan_stopped', plan, item, '%s:%s' % (state, reason))


def _finish_not_run(item, token, plan, reason):
    if finish_check(item['id'], token, 'not_run', reason):
        if plan.get('status') == 'approved':
            stop_plan(plan['id'], 'check_not_run:%s:%s' % (item['id'], reason))
            _audit('plan_stopped', plan, item, reason)


def _check_reason(plan, item, token):
    # AV1: the token is a signed prefix field, never the reason text. The
    # reason here names the plan and item only.
    return 'plan-check:%s:%s' % (plan['id'], item['id'])


def _ssh_attempt(plan, item, check, agent, token):
    """One ssh attempt through the real runner, under the plan's agent."""
    from core.agent_context import reset_current_agent, set_current_agent
    from core.ssh_exec import ssh_exec
    from db.session_plans import create_plan as _create_plan
    host = (item.get('host') or '').strip()
    if not host or host == '*':
        return 'not_run', 'check_no_host', None
    try:
        created = _create_plan(
            plan['session_id'],
            [{'kind': 'ssh', 'host': host, 'command': check['command'],
              'max_uses': check['retries'] + 1}],
            note='check for plan #%d item #%d' % (plan['id'], item['id']),
            origin='check', internal=CHECK_INTERNAL, parent=plan)
        # The check plan's one item carries the attempt's token (AR6) and
        # names the parent item the request row will point at.
        set_check_item_token(created['plan_id'], token)
        set_check_item_parent(created['plan_id'], item['id'])
        set_check_plan_refs(item['id'], created['plan_id'],
                            created.get('item_id') or 0)
    except PlanError as exc:
        return 'not_run', 'check_plan_refused', None
    context = set_current_agent(agent)
    try:
        result = ssh_exec(host, check['command'],
                          session_id=plan.get('session_id') or '',
                          timeout=CHECK_ROW_LIMIT_SECONDS, wait=False,
                          agent=agent, force_jit=True, check_token=token)
    except Exception:
        return 'not_run', 'check_runner_failed', None
    finally:
        reset_current_agent(context)
    status = result.get('status')
    if status == 'ran':
        exit_code = result.get('exit_code')
        return ('passed' if exit_code == 0 else 'failed'), '', exit_code
    if status == 'pending':
        from db.gateways import get_gateway_run_limits
        from core.ssh_exec import resolve_gateway
        gateway = resolve_gateway(host) or {}
        if (get_gateway_run_limits(gateway.get('ip') or host) or '') != 'v1':
            return 'not_run', 'check_no_limits', None
        return 'not_run', 'check_unclaimed', None
    reason = str(result.get('reason') or 'refused')
    if reason in ('host_not_allowed', 'read-only agent', 'unknown_host',
                  'session_not_yours', 'no_agent'):
        return 'not_run', 'check_agent_inactive', None
    return 'not_run', 'check_refused', None


def _tool_attempt(plan, item, check, agent, token):
    """One tool attempt through run_tool's read path, as the plan's agent."""
    from core.agent_context import reset_current_agent, set_current_agent
    from core.tool_runner import run_tool
    context = set_current_agent(agent)
    try:
        result = run_tool(check['integration'], check['tool'],
                          dict(check['args'] or {}),
                          reason=_check_reason(plan, item, token))
    except Exception:
        return 'not_run', 'check_tool_failed', None, None
    finally:
        reset_current_agent(context)
    try:
        value = json.loads(result) if isinstance(result, str) else result
    except ValueError:
        return 'not_run', 'check_tool_failed', None, None
    if isinstance(value, dict) and value.get('error'):
        return 'not_run', 'check_tool_refused', None, None
    state, reason, observed, expected = evaluate_expect(check['expect'], value)
    return state, reason, observed, expected


# ── startup and the sweep ──────────────────────────────────────────────

def sweep_interrupted(now=None):
    """Turn abandoned pending/running items into `not_run`; never re-dispatch.

    A check whose outcome the dashboard never saw is not evidence that
    anything passed. Its plan stops, and the operator can approve a new one.
    """
    from db.session_plans import interrupted_checks
    now = int(now or time.time())
    count = 0
    for row in interrupted_checks(WORST_CASE_SECONDS, now=now):
        token = row.get('check_token') or ''
        if finish_check(row['id'], token, 'not_run', 'check_interrupted'):
            plan = get_plan(int(row['plan_id']))
            if plan and plan.get('status') == 'approved':
                stop_plan(plan['id'], 'check_not_run:%s:check_interrupted'
                          % row['id'])
            _audit('check_interrupted', plan or {}, row, 'check_interrupted')
            count += 1
    return count


def _worker_loop():
    while not _STOP.is_set():
        try:
            item_id, token = _QUEUE.get(timeout=30)
        except queue.Empty:
            try:
                sweep_interrupted()
                _expire_check_plans()
            except Exception as exc:  # noqa: BLE001 — a job must not die
                print('[checks] sweep failed: %s' % exc, flush=True)
            continue
        try:
            _run_check(item_id, token)
        except Exception as exc:  # noqa: BLE001 — one check must not kill the job
            print('[checks] check %s failed: %s' % (item_id, exc), flush=True)
        finally:
            try:
                _QUEUE.task_done()
            except ValueError:
                pass


def _expire_check_plans():
    from db.session_plans import expire_plans
    expire_plans(int(time.time()))


def start_check_worker(threads=2):
    """Start the bounded workers once (the app startup hook)."""
    global _THREADS
    with _STARTED:
        if _THREADS:
            return
        _STOP.clear()
        for index in range(max(1, int(threads))):
            thread = threading.Thread(target=_worker_loop,
                                      name='plan-check-%d' % index, daemon=True)
            thread.start()
            _THREADS.append(thread)


def stop_check_worker():
    _STOP.set()


def check_row_limit():
    """(json text, seconds) for the request row a check claim creates."""
    text, limit = _run_limit_json()
    return text, limit['seconds']
