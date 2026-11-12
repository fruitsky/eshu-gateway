"""F12: `ssh_exec` — an agent's SSH command through Eshu over MCP.

The agent never holds a key. The dashboard opens one SSH connection per call
as `eshu-gateway` with its own service key, sends a signed prefix that names
the agent (and the session, when there is one), and receives exactly one
`ESHU-MCP-ENVELOPE` line back. The gateway keeps every decision it makes for
direct SSH: freeze, blocklists, allowlists, Zero-Trust, JIT and the read lane.
The dashboard can only add gating (`ESHU_FORCE_JIT`, the agent policy).

This module is the whole dashboard side: policy, signing, the runner, the
envelope and the F5 hook. The gateway side is §5–§8 of the design note.
"""
import base64
import json
import os
import re
import secrets
import signal
import subprocess
import time

from core import ssh_crypto, ssh_keys
from core.agent_context import get_current_agent
from core.cmd_parse import breakdown as cmd_breakdown
from core.read_policy import is_read_ssh
from core.secret_scrub import scrub_string
from db.agent_policies import DEFAULT_SSH_MODE, get_agent_policy
from db.audit import record_audit_event
from db.exec_results import save_result
from db.gateways import (
    get_gateway, get_gateways, get_gateway_ssh_host_key,
    get_gateway_ssh_key_derived,
)
from db.requests import get_request_row, get_request_status
from db.sessions import get_session

def _row_was_unattended(request_id: int) -> bool:
    """F19: was this row approved without a human deciding on this command?"""
    try:
        row = get_request_row(request_id) or {}
    except Exception:
        return False
    return bool(row.get('unattended'))
# The gateway's own limits (§2, §8).
MAX_COMMAND = 8192
MAX_OUTPUT = 65536
DEFAULT_TIMEOUT = 60
MAX_TIMEOUT = 120
WAIT_SECONDS = 60
POLL_INTERVAL = 2

ENVELOPE_PREFIX = 'ESHU-MCP-ENVELOPE '
AGENT_FORMAT = 'eshu-ssh-agent-v1'
TICKET_FORMAT = 'v2'
STATUSES = ('ran', 'pending', 'refused')

HOST_RE = re.compile(r'[A-Za-z0-9_.:-]{1,253}')
# S4: the host argument is always a bare IP, never a hostname or an
# option. Mirrors the gateway's own TARGET_IP check.
IP_RE = re.compile(r'[0-9A-Fa-f:.]{1,64}')
AGENT_NAME_RE = re.compile(r'[A-Za-z0-9_.:-]{1,64}')
SESSION_ID_RE = re.compile(r'[A-Za-z0-9_-]{1,64}')
CONTROL_ALLOWED = frozenset('\t')


def as_json(value) -> str:
    return json.dumps(value, separators=(',', ':'))


def refused(code: str, **extra) -> dict:
    out = {'status': 'refused', 'reason': code}
    out.update(extra)
    return out


# ── the command the gateway will actually run (G3 / G2) ───────────────────

def command_problem(command: str) -> str:
    """Why this command must not be signed, or '' when it may be."""
    if not isinstance(command, str) or not command:
        return 'empty-command'
    try:
        size = len(command.encode('utf-8'))
    except UnicodeEncodeError:
        return 'invalid-encoding'
    if size > MAX_COMMAND:
        return 'command-too-long'
    # BK2: one line, one command. The gateway runs `bash -c`, so a CR or LF
    # would let a second command ride along that no operator saw on the card.
    # Refuse before anything is signed or any SSH connection is opened.
    if '\n' in command or '\r' in command:
        return 'multiline_command'
    # G3: the gateway trims before it hashes, so the dashboard must refuse
    # anything the trim would change instead of signing other bytes.
    if command != command.strip():
        return 'command-whitespace'
    for char in command:
        if char in CONTROL_ALLOWED:
            continue
        if ord(char) < 32 or ord(char) == 127:
            return 'command-control-character'
    first_word = command.split()[0] if command.split() else ''
    if first_word.startswith('ESHU_'):
        return 'command-prefix-lookalike'
    return ''


# ── host resolution and policy ───────────────────────────────────────────

def resolve_gateway(host: str):
    """A registered gateway by exact IP, or by a unique hostname. None else."""
    host = (host or '').strip()
    if not host or not HOST_RE.fullmatch(host) or host.startswith('-'):
        return None
    gateway = get_gateway(host)
    if gateway:
        return gateway
    matches = [g for g in get_gateways()
               if (g.get('hostname') or '').lower() == host.lower()]
    return matches[0] if len(matches) == 1 else None


def host_allowed(hosts, gateway: dict, host: str) -> bool:
    """`ssh_hosts_allow`: empty means none (opt-in), `*` means any gateway."""
    hosts = [str(h).strip() for h in (hosts or []) if str(h).strip()]
    if not hosts:
        return False
    if '*' in hosts:
        return True
    wanted = {(gateway.get('ip') or '').lower(),
              (gateway.get('hostname') or '').lower(), (host or '').lower()}
    return any(h.lower() in wanted for h in hosts)


def agent_ssh_policy(agent) -> dict:
    policy = get_agent_policy(agent.get('id')) if agent and agent.get('id') else {}
    return {
        'ssh_hosts_allow': list((policy or {}).get('ssh_hosts_allow') or []),
        'ssh_mode': (policy or {}).get('ssh_mode') or DEFAULT_SSH_MODE,
    }


def session_problem(agent, session_id: str) -> str:
    """'' when the caller may name this session, else the refusal reason."""
    if not session_id:
        return ''
    if not SESSION_ID_RE.fullmatch(session_id):
        return 'invalid-session-id'
    session = get_session(session_id)
    if not session:
        return 'unknown-session'
    owner = (session.get('agent') or '').strip()
    if not owner or owner != (agent.get('name') or ''):
        # Same fail-closed rule as F3's plan tools: another agent's session must
        # not receive bytes this caller chose.
        return 'session_not_yours'
    return ''


def capability_problem(gateway: dict) -> str:
    """Fail closed on a gateway that is not ready for this format (§9)."""
    agent_format = (gateway.get('ssh_agent_format') or '').strip()
    ticket_format = (gateway.get('ssh_ticket_format') or '').strip()
    if agent_format != AGENT_FORMAT or ticket_format != TICKET_FORMAT:
        return 'ssh_not_ready'
    if not get_gateway_ssh_host_key(gateway.get('ip')):
        return 'ssh_host_unpinned'
    return ''


# ── the runner (G7) ─────────────────────────────────────────────────────

def build_argv(gateway: dict, command_line: str, known_hosts: str,
               key_path: str) -> list:
    """The fixed ssh argv. The IP, never a hostname (no DNS at call time)."""
    return [
        'ssh',
        '-F', '/dev/null',
        '-i', key_path,
        '-l', 'eshu-gateway',
        '-o', 'BatchMode=yes',
        '-o', 'IdentitiesOnly=yes',
        '-o', 'StrictHostKeyChecking=yes',
        '-o', f'UserKnownHostsFile={known_hosts}',
        '-o', 'GlobalKnownHostsFile=/dev/null',
        '-o', 'ConnectTimeout=10',
        '-o', 'ServerAliveInterval=15',
        '-o', 'LogLevel=ERROR',
        gateway.get('ip') or '',
        command_line,
    ]


def run_ssh(gateway: dict, command_line: str, known_hosts: str,
            key_path: str, timeout: int) -> dict:
    """Run one ssh call with no shell. Returns rc/stdout/stderr/timed_out.

    The local process group is killed at the timeout. That bounds how long the
    agent waits; it does **not** stop the remote command (F19 is the real
    bound), which the design states explicitly.
    """
    argv = build_argv(gateway, command_line, known_hosts, key_path)
    env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'LANG': 'C',
           'HOME': ssh_keys.key_dir()}
    try:
        proc = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, errors='replace',
            start_new_session=True, env=env)
    except OSError as exc:
        return {'rc': 127, 'stdout': '', 'stderr': str(exc), 'timed_out': False}
    try:
        out, err = proc.communicate(timeout=timeout)
        return {'rc': proc.returncode, 'stdout': out or '',
                'stderr': err or '', 'timed_out': False}
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            out, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            out, err = '', ''
        return {'rc': 124, 'stdout': out or '', 'stderr': err or '',
                'timed_out': True}


# ── the envelope (§8) ───────────────────────────────────────────────────

def parse_envelope(stdout: str) -> dict:
    """Exactly one envelope line and nothing else, or a `bad-envelope` error."""
    lines = [line for line in (stdout or '').splitlines() if line.strip()]
    if len(lines) != 1 or not lines[0].startswith(ENVELOPE_PREFIX):
        return {'error': 'bad-envelope'}
    try:
        payload = json.loads(lines[0][len(ENVELOPE_PREFIX):])
    except ValueError:
        return {'error': 'bad-envelope'}
    if not isinstance(payload, dict):
        return {'error': 'bad-envelope'}
    required = {'status', 'request_id', 'exit_code', 'output_b64',
                'truncated', 'messages'}
    if not required.issubset(payload):
        # A partial envelope is a broken gateway, not a default: refuse it
        # rather than guessing a status or an exit code.
        return {'error': 'bad-envelope'}
    status = payload['status']
    request_id = payload['request_id']
    exit_code = payload['exit_code']
    output_b64 = payload['output_b64']
    truncated = payload['truncated']
    messages = payload['messages']
    if status not in STATUSES:
        return {'error': 'bad-envelope'}
    if not isinstance(request_id, int) or isinstance(request_id, bool) or request_id < 0:
        return {'error': 'bad-envelope'}
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        return {'error': 'bad-envelope'}
    if not isinstance(output_b64, str) or not isinstance(truncated, bool):
        return {'error': 'bad-envelope'}
    if not isinstance(messages, list) or any(not isinstance(m, str) for m in messages):
        return {'error': 'bad-envelope'}
    return {'status': status, 'request_id': request_id, 'exit_code': exit_code,
            'output_b64': output_b64, 'truncated': truncated,
            'messages': messages}


def decode_output(envelope: dict) -> dict:
    """Decode, scrub and cap the envelope's output (never trust its length)."""
    raw = envelope.get('output_b64') or ''
    try:
        data = base64.b64decode(raw, validate=True) if raw else b''
    except (ValueError, TypeError):
        return {'error': 'bad-envelope'}
    try:
        text = data.decode('utf-8', 'replace')
    except Exception:
        text = ''
    scrubbed = scrub_string(text)
    encoded = scrubbed.encode('utf-8')
    truncated = bool(envelope.get('truncated'))
    if len(encoded) > MAX_OUTPUT:
        encoded = encoded[-MAX_OUTPUT:]
        truncated = True
    return {'output': encoded.decode('utf-8', 'replace'), 'truncated': truncated}


# ── the tool ────────────────────────────────────────────────────────────

def _signed_command(gateway: dict, key_hex: str, agent_name: str,
                    session_id: str, force_jit: bool, command: str) -> str:
    ts = str(int(time.time()))
    nonce = secrets.token_hex(16)
    canonical = ssh_crypto.canonical(
        command, agent_name, session=session_id, mcp='1',
        force_jit='1' if force_jit else '0', ts=ts, nonce=nonce,
        gateway_ip=gateway.get('ip') or '')
    signature = ssh_crypto.sign(key_hex, canonical)
    return ssh_crypto.prefix_line(agent_name, session_id, force_jit, ts,
                                  nonce, signature) + command


def _store_result(request_id: int, exit_code: int, duration_ms: int,
                  output: str, truncated: bool):
    if request_id <= 0:
        return
    try:
        save_result(request_id, exit_code, duration_ms, output, truncated)
    except Exception:
        pass


def ssh_exec(host: str, command: str, session_id: str = '', timeout=None,
             wait: bool = False, agent=None) -> dict:
    """Run one command on one registered gateway, or explain the refusal."""
    agent = agent or get_current_agent()
    started = time.time()

    def _audit(outcome: str, gateway=None):
        try:
            record_audit_event(
                'ssh_exec', (gateway or {}).get('ip') or '',
                (gateway or {}).get('hostname') or '',
                details=(f"agent={(agent or {}).get('name') or '-'} "
                         f"sha={ssh_crypto.command_sha(command or '')[:16]} "
                         f"status={outcome}"),
                actor=(agent or {}).get('name') or '')
        except Exception:
            pass

    if not agent:
        return refused('no_agent', message='ssh_exec needs an authenticated agent token.')
    name = (agent.get('name') or '').strip()
    if not AGENT_NAME_RE.fullmatch(name):
        return refused('invalid_agent_name')

    problem = command_problem(command)
    if problem:
        _audit(problem)
        return refused(problem)

    gateway = resolve_gateway(host)
    if not gateway:
        _audit('unknown_host')
        return refused('unknown_host',
                       message='host must be a registered gateway IP or hostname.')

    policy = agent_ssh_policy(agent)
    if not host_allowed(policy['ssh_hosts_allow'], gateway, host):
        _audit('host_not_allowed', gateway)
        return refused('host_not_allowed',
                       message=f"this agent has no SSH access to {gateway.get('ip')}.")
    if policy['ssh_mode'] == 'read_only':
        ok, why = is_read_ssh(cmd_breakdown(command))
        if not ok:
            _audit('read_only_refused', gateway)
            return refused('read-only agent',
                           message=f'read-only agent: {why or "write command"}.')

    session_issue = session_problem(agent, session_id or '')
    if session_issue:
        _audit(session_issue, gateway)
        return refused(session_issue)

    capability_issue = capability_problem(gateway)
    if capability_issue:
        _audit(capability_issue, gateway)
        return refused(capability_issue)

    key_hex = get_gateway_ssh_key_derived(gateway.get('ip'))
    if not key_hex:
        _audit('ssh_key_unprovisioned', gateway)
        return refused('ssh_key_unprovisioned',
                       message='the gateway token must be set again to derive the SSH signing key.')

    host_key = get_gateway_ssh_host_key(gateway.get('ip'))
    try:
        known_hosts = ssh_keys.write_known_hosts(gateway.get('ip'), host_key)
        key_path = ssh_keys.ensure_key_pair()
    except ssh_keys.KeyError_ as exc:
        _audit(exc.code, gateway)
        return refused(exc.code, message=exc.message)

    try:
        timeout_s = int(timeout) if timeout is not None else DEFAULT_TIMEOUT
    except (TypeError, ValueError):
        timeout_s = DEFAULT_TIMEOUT
    timeout_s = max(1, min(timeout_s, MAX_TIMEOUT))

    force_jit = policy['ssh_mode'] == DEFAULT_SSH_MODE
    try:
        command_line = _signed_command(gateway, key_hex, name, session_id or '',
                                       force_jit, command)
    except ValueError:
        _audit('signing_failed', gateway)
        return refused('signing_failed')

    # S4: build_argv must never see a host that starts with `-`. The IP is the
    # gateway row's primary key and is validated on registration; this is
    # defence in depth at the last point before argv is built.
    ip = gateway.get('ip') or ''
    if not IP_RE.fullmatch(ip):
        _audit('invalid_gateway_ip', gateway)
        return refused('invalid_gateway_ip',
                       message='the gateway record has no valid IP address.')

    run = run_ssh(gateway, command_line, known_hosts, key_path, timeout_s)
    duration_ms = int((time.time() - started) * 1000)
    if run.get('timed_out'):
        _audit('timeout', gateway)
        return refused('timeout', message=f'no answer within {timeout_s}s.')

    envelope = parse_envelope(run.get('stdout') or '')
    if 'error' in envelope:
        # No envelope is a refusal, never a silent success. The plain text the
        # gateway prints (if any) is the useful explanation; scrub it.
        detail = scrub_string((run.get('stdout') or '').strip().splitlines()[-1]
                              if (run.get('stdout') or '').strip() else '')
        _audit('bad-envelope', gateway)
        return refused('bad-envelope',
                       message=detail[:300] or 'the gateway did not return an envelope.')

    status = envelope['status']
    request_id = envelope['request_id']
    if status == 'ran':
        decoded = decode_output(envelope)
        if 'error' in decoded:
            _audit('bad-envelope', gateway)
            return refused('bad-envelope')
        output = decoded['output']
        _store_result(request_id, envelope['exit_code'], duration_ms,
                      output, decoded['truncated'])
        _audit('ran', gateway)
        return {'status': 'ran', 'request_id': request_id,
                'exit_code': envelope['exit_code'], 'output': output,
                'truncated': decoded['truncated']}

    reason = scrub_string(envelope['messages'][0]) if envelope['messages'] else ''
    if status == 'pending':
        if wait and request_id > 0:
            deadline = time.time() + WAIT_SECONDS
            while time.time() < deadline:
                current = get_request_status(request_id)
                if current in ('denied', 'expired', 'abandoned'):
                    _audit('denied', gateway)
                    return refused(current, request_id=request_id, message=reason)
                if current == 'approved' and _row_was_unattended(request_id):
                    # F19/L3: an unattended row is never handed to the poller;
                    # it is claimed by id, and the re-run gets the limit from
                    # the same response. Waiting for `consumed` here would spin
                    # to the deadline because nothing else can consume it.
                    _audit('wait-unattended', gateway)
                    return ssh_exec(host, command, session_id=session_id,
                                    timeout=timeout, wait=False, agent=agent)
                if current == 'consumed':
                    # M2: the ticket is claimable only after the poller has
                    # taken it — that is what flips the row to `consumed` and
                    # deposits the lockbox line. Re-running at `approved`
                    # races the poll: the gateway finds no ticket, files a
                    # second JIT request and the operator is asked twice.
                    _audit('wait-consumed', gateway)
                    return ssh_exec(host, command, session_id=session_id,
                                    timeout=timeout, wait=False, agent=agent)
                time.sleep(POLL_INTERVAL)
        _audit('pending', gateway)
        return {'status': 'pending', 'request_id': request_id,
                'reason': reason or 'waiting for operator approval'}

    _audit('refused', gateway)
    return {'status': 'refused', 'request_id': request_id,
            'reason': reason or 'refused'}
