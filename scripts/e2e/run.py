#!/usr/bin/env python3
"""E2E-1: one-command smoke suite against a live dashboard and its dev gateway.

The suite plays the agent (MCP + the one agent-only window request) and the
operator (the scoped `e2e_` approver token; never the dashboard password).
Case constants are synthetic. The table prints ids, never command output, the
goal text, entity ids, hostnames, tokens or key paths.

Run with `--list` to print the cases without a connection, or `--json` for the
same rows as JSON. Configuration is environment-only; see "How to run" in
docs/features/FEATURE_PLAN.md.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import ipaddress
import json
import os
import re
import secrets
import shlex
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

DEFAULT_BASE_URL = 'http://127.0.0.1:8000'
ALLOWLIST_COMMAND = 'hostname'
JIT_COMMAND = 'id -un'
JIT_DENY_COMMAND = 'printf e2e-deny'
REPLAY_COMMAND = 'cat /etc/hostname'
PLAN_COMMAND = 'printf e2e-plan'
PLAN_A = 'printf e2e-plan-a'
PLAN_B = 'printf e2e-plan-b'
WAIT_COMMAND = 'printf e2e-wait'
WINDOW_COMMAND = 'printf e2e-window'
MULTILINE_COMMAND = 'echo one\necho two'
COMMANDS = (ALLOWLIST_COMMAND, JIT_COMMAND, JIT_DENY_COMMAND, REPLAY_COMMAND,
            PLAN_COMMAND, PLAN_A, PLAN_B, WAIT_COMMAND, WINDOW_COMMAND)
TOKEN_FILES = ('ESHU_E2E_AGENT_TOKEN_FILE', 'ESHU_E2E_APPROVER_TOKEN_FILE')


class UnsafeConfig(Exception):
    """The suite refuses to start (exit 2)."""


class SkipCase(Exception):
    pass


class Failure(Exception):
    pass


def expect(condition, reason):
    if not condition:
        raise Failure(reason)


def _is_loopback(host):
    if not host:
        return False
    if host.lower() == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _read_secret_file(variable, env):
    path = (env.get(variable) or '').strip()
    if not path:
        raise UnsafeConfig('%s is required' % variable)
    try:
        info = os.stat(path)
    except OSError:
        raise UnsafeConfig('%s does not exist or cannot be read' % variable)
    if not stat.S_ISREG(info.st_mode) or os.path.islink(path):
        raise UnsafeConfig('%s must be a regular file' % variable)
    if info.st_mode & 0o077:
        raise UnsafeConfig('%s must not be group/world-readable' % variable)
    if info.st_size <= 0 or info.st_size > 16384:
        raise UnsafeConfig('%s is empty or too large' % variable)
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            value = fh.read().strip()
    except OSError:
        raise UnsafeConfig('%s cannot be read' % variable)
    if not value:
        raise UnsafeConfig('%s is empty' % variable)
    return value


def check_test_agent(agent, cfg):
    """Refuse unless the one returned agent is the narrow test agent."""
    policy = (agent or {}).get('policy') or {}
    if not policy.get('test_agent'):
        raise UnsafeConfig('the agent is not test_agent=1')
    if policy.get('ssh_mode') != 'normal':
        raise UnsafeConfig('the test agent must use ssh_mode=normal')
    hosts = policy.get('ssh_hosts_allow') or []
    if hosts != [cfg.gateway]:
        raise UnsafeConfig('ssh_hosts_allow must be exactly the test gateway')
    if policy.get('tools_mode') != 'allowlist':
        raise UnsafeConfig('the test agent must use an allowlist')
    return policy


@dataclass
class Config:
    base_url: str
    gateway: str
    agent_token: str = field(repr=False)
    approver_token: str = field(repr=False)
    gateway_ssh_key: str = field(default='', repr=False)
    gateway_ssh_user: str = 'eshu-gateway'
    case: str = ''
    keep: bool = False
    dns_name: str = ''
    allowance_entity: str = ''
    allowance_other_entity: str = ''
    run_id: str = ''

    @classmethod
    def from_env(cls, env=None):
        env = dict(os.environ if env is None else env)
        base = (env.get('ESHU_E2E_BASE_URL') or DEFAULT_BASE_URL).strip()
        parsed = urllib.parse.urlsplit(base)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            raise UnsafeConfig('ESHU_E2E_BASE_URL must be an http(s) URL')
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise UnsafeConfig('ESHU_E2E_BASE_URL must not carry credentials, '
                               'a query or a fragment')
        if not _is_loopback(parsed.hostname):
            if parsed.scheme != 'https':
                raise UnsafeConfig('a non-loopback URL must be https://')
            if env.get('ESHU_E2E_ALLOW_REMOTE') != '1':
                raise UnsafeConfig('set ESHU_E2E_ALLOW_REMOTE=1 for a '
                                   'non-loopback target')
        gateway = (env.get('ESHU_E2E_GATEWAY') or '').strip()
        if not gateway:
            raise UnsafeConfig('ESHU_E2E_GATEWAY is required')
        try:
            ipaddress.ip_address(gateway)
        except ValueError:
            raise UnsafeConfig('ESHU_E2E_GATEWAY must be one IP, never a hostname')
        agent_token = _read_secret_file('ESHU_E2E_AGENT_TOKEN_FILE', env)
        approver_token = _read_secret_file('ESHU_E2E_APPROVER_TOKEN_FILE', env)
        key = (env.get('ESHU_E2E_GATEWAY_SSH_KEY') or '').strip()
        if key:
            try:
                info = os.stat(key)
            except OSError:
                raise UnsafeConfig('ESHU_E2E_GATEWAY_SSH_KEY cannot be read')
            if not stat.S_ISREG(info.st_mode) or os.path.islink(key):
                raise UnsafeConfig('ESHU_E2E_GATEWAY_SSH_KEY must be a regular file')
            if info.st_mode & 0o077:
                raise UnsafeConfig('ESHU_E2E_GATEWAY_SSH_KEY must not be '
                                   'group/world-readable')
        return cls(
            base_url=base.rstrip('/'),
            gateway=gateway,
            agent_token=agent_token,
            approver_token=approver_token,
            gateway_ssh_key=key,
            gateway_ssh_user=(env.get('ESHU_E2E_GATEWAY_SSH_USER')
                              or 'eshu-gateway').strip() or 'eshu-gateway',
            case=(env.get('ESHU_E2E_CASE') or '').strip(),
            keep=(env.get('ESHU_E2E_KEEP') or '') == '1',
            dns_name=(env.get('ESHU_E2E_DNS_NAME') or '').strip(),
            allowance_entity=(env.get('ESHU_E2E_ALLOWANCE_ENTITY') or '').strip(),
            allowance_other_entity=(env.get(
                'ESHU_E2E_ALLOWANCE_OTHER_ENTITY') or '').strip(),
            run_id=secrets.token_hex(4),
        )


class Http:
    """One JSON-over-HTTP client. The token is only ever used in a header."""

    def __init__(self, base_url, token):
        self.base_url = base_url.rstrip('/')
        self.token = token
        self.opener = urllib.request.build_opener()

    def request(self, method, path, body=None):
        data = None
        if body is not None:
            data = json.dumps(body).encode('utf-8')
        req = urllib.request.Request(self.base_url + path, data=data,
                                     method=method.upper())
        if self.token:
            req.add_header('Authorization', 'Bearer ' + self.token)
        req.add_header('Content-Type', 'application/json')
        try:
            with self.opener.open(req, timeout=30) as resp:
                raw = resp.read()
                status = resp.status
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = exc.code
        except Exception:
            raise Failure('the dashboard could not be reached')
        try:
            payload = json.loads(raw.decode('utf-8')) if raw else {}
        except (ValueError, UnicodeDecodeError):
            payload = {'detail': 'non-json response'}
        return status, payload


class Operator:
    """The scoped approver token, used only on its allowed routes."""

    def __init__(self, http):
        self.http = http
        base = getattr(http, 'base_url', '')
        self.public_http = Http(base, '') if base else http

    def get(self, path):
        return self.http.request('GET', path)

    def post(self, path, body=None):
        return self.http.request('POST', path, body)

    def delete(self, path):
        return self.http.request('DELETE', path)

    def _ok(self, status, path):
        if status not in (200, 201, 204):
            raise Failure('HTTP %s on %s' % (status, path))
        return status

    def agents(self):
        status, body = self.get('/api/agents')
        self._ok(status, '/api/agents')
        return body

    def requests(self, session_id):
        status, body = self.get('/api/requests?session=' +
                                urllib.parse.quote(session_id))
        self._ok(status, '/api/requests')
        return body

    def request(self, session_id, request_id):
        for row in self.requests(session_id):
            if int(row.get('id') or 0) == int(request_id):
                return row
        return None

    def plans(self, session_id):
        status, body = self.get('/api/session-plans?session=' +
                                urllib.parse.quote(session_id))
        self._ok(status, '/api/session-plans')
        return body

    def plan(self, session_id, plan_id):
        for row in self.plans(session_id):
            if int(row.get('id') or 0) == int(plan_id):
                return row
        return None

    def pending_calls(self, session_id):
        status, body = self.get('/api/integration-calls/pending?session_id=' +
                                urllib.parse.quote(session_id))
        self._ok(status, '/api/integration-calls/pending')
        return body

    def approve_request(self, request_id, approver, session_id):
        row = self.request(session_id, request_id)
        expect(row is not None, 'the request is not this session\'s')
        ok, why = approver.request(row)
        expect(ok, why)
        status, body = self.post('/api/approve/%d' % int(request_id))
        self._ok(status, '/api/approve')
        return body

    def deny_request(self, request_id, approver, session_id):
        row = self.request(session_id, request_id)
        expect(row is not None, 'the request is not this session\'s')
        ok, why = approver.request(row, deny=True)
        expect(ok, why)
        status, body = self.post('/api/deny/%d' % int(request_id))
        self._ok(status, '/api/deny')
        return body

    def approve_plan(self, plan_id, approver, session_id):
        row = self.plan(session_id, plan_id)
        expect(row is not None, 'the plan is not this session\'s')
        ok, why = approver.plan(row)
        expect(ok, why)
        status, body = self.post('/api/session-plans/%d/approve' % int(plan_id),
                                 {'ttl_minutes': 60})
        self._ok(status, '/api/session-plans/approve')
        return body

    def deny_plan(self, plan_id, approver, session_id):
        row = self.plan(session_id, plan_id)
        expect(row is not None, 'the plan is not this session\'s')
        status, body = self.post('/api/session-plans/%d/deny' % int(plan_id))
        self._ok(status, '/api/session-plans/deny')
        return body

    def deny_call(self, call_id, approver, session_id):
        row = None
        for item in self.pending_calls(session_id):
            if int(item.get('id') or 0) == int(call_id):
                row = item
                break
        expect(row is not None, 'the call is not this session\'s')
        ok, why = approver.call(row)
        expect(ok, why)
        status, body = self.post('/api/integration-calls/%d/deny' % int(call_id))
        self._ok(status, '/api/integration-calls/deny')
        return body

    def approve_window(self, window_id, approver, session_id):
        status, body = self.post('/api/window-requests/%d/approve' % int(window_id))
        self._ok(status, '/api/window-requests/approve')
        return body

    def delete_window(self, window_id):
        status, body = self.delete('/api/approved-windows/%d' % int(window_id))
        self._ok(status, '/api/approved-windows')
        return body

    def exclude(self, session_id):
        status, body = self.post('/api/requests/exclude',
                                 {'session_id': session_id})
        self._ok(status, '/api/requests/exclude')
        return body

    def reject_conclusion(self, conclusion_id):
        status, body = self.post('/api/conclusions/%d/reject' %
                                 int(conclusion_id), {'note': 'E2E cleanup'})
        self._ok(status, '/api/conclusions/reject')
        return body

    def installer_hash(self):
        status, body = self.get('/api/e2e/installer-hash')
        self._ok(status, '/api/e2e/installer-hash')
        return body

    def public_get(self, path):
        # The token is not sent to a route outside the approver allowlist.
        return self.public_http.request('GET', path)


class Approver:
    """The suite-side mirror of BY1/BY2. The server is still the boundary."""

    def __init__(self, agent_id, session_id, gateway, commands=COMMANDS):
        self.agent_id = int(agent_id)
        self.session_id = session_id
        self.gateway = gateway
        self.commands = frozenset(commands)

    def request(self, row, deny=False):
        if not row:
            return False, 'no row'
        if int(row.get('agent_id') or 0) != self.agent_id:
            return False, 'row agent is not the token agent'
        if str(row.get('session_id') or '') != self.session_id:
            return False, 'row session is not this session'
        if str(row.get('target_ip') or '') != self.gateway:
            return False, 'row host is not the test gateway'
        if not deny and str(row.get('command') or '') not in self.commands:
            return False, 'command is not in the token list'
        return True, ''

    def plan(self, row):
        if not row:
            return False, 'no plan'
        if int(row.get('agent_id') or 0) != self.agent_id:
            return False, 'plan agent is not the token agent'
        if str(row.get('session_id') or '') != self.session_id:
            return False, 'plan session is not this session'
        return True, ''

    def window(self, row):
        if not row:
            return False, 'no window'
        if int(row.get('agent_id') or 0) != self.agent_id:
            return False, 'window agent is not the token agent'
        if str(row.get('session_id') or '') != self.session_id:
            return False, 'window session is not this session'
        if str(row.get('target_ip') or '') != self.gateway:
            return False, 'window host is not the test gateway'
        if str(row.get('match_type') or 'exact') != 'exact':
            return False, 'window is not exact'
        if int(row.get('max_executions') or 0) != 1:
            return False, 'window is not single-use'
        if str(row.get('command') or '') not in self.commands:
            return False, 'window command is not in the token list'
        return True, ''

    def call(self, row):
        if not row:
            return False, 'no call'
        if int(row.get('agent_id') or 0) != self.agent_id:
            return False, 'call agent is not the token agent'
        if str(row.get('session_id') or '') != self.session_id:
            return False, 'call session is not this session'
        return True, ''


class McpAgent:
    """A thin, synchronous wrapper over the MCP streamable-HTTP client."""

    def __init__(self, base_url, token):
        self.url = base_url.rstrip('/') + '/mcp'
        self.token = token

    async def _with_session(self, fn):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        headers = {'Authorization': 'Bearer ' + self.token}
        async with streamablehttp_client(self.url, headers=headers,
                                         timeout=30) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await fn(session)

    @staticmethod
    def _text(result):
        chunks = []
        for item in getattr(result, 'content', []) or []:
            text = getattr(item, 'text', None)
            if text is not None:
                chunks.append(text)
        text = ''.join(chunks)
        try:
            return json.loads(text)
        except (TypeError, ValueError):
            return {'text': text}

    def call(self, tool, **arguments):
        async def go(session):
            return await session.call_tool(tool, arguments=arguments)
        try:
            result = asyncio.run(self._with_session(go))
        except Exception:
            raise Failure('the MCP server could not run %s' % tool)
        return self._text(result)

    def list_tools(self):
        async def go(session):
            return await session.list_tools()
        try:
            result = asyncio.run(self._with_session(go))
        except Exception:
            raise Failure('the MCP server could not list tools')
        return [tool.name for tool in getattr(result, 'tools', []) or []]


class WindowRunner:
    """Direct `eshu-gateway@` SSH for C11; it is the documented token path."""

    def __init__(self, cfg):
        self.cfg = cfg

    def run(self, token, command, timeout=60):
        target = '%s@%s' % (self.cfg.gateway_ssh_user, self.cfg.gateway)
        # The command is a suite constant; only the token needs quoting.
        remote = 'ESHU_WINDOW_TOKEN=%s %s' % (shlex.quote(token), command)
        argv = ['ssh', '-i', self.cfg.gateway_ssh_key,
                '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=accept-new',
                '-o', 'ConnectTimeout=10', target, remote]
        try:
            proc = subprocess.run(argv, capture_output=True, timeout=timeout,
                                  text=True)
        except (OSError, subprocess.TimeoutExpired):
            raise Failure('the direct gateway command could not run')
        return proc.returncode, proc.stdout, proc.stderr


class Waiter(threading.Thread):
    """Approve one command when it appears, through the same BY1/BY2 rules."""

    def __init__(self, operator, approver, session_id, command):
        super().__init__(daemon=True)
        self.operator = operator
        self.approver = approver
        self.session_id = session_id
        self.command = command
        self.stop_event = threading.Event()
        self.error = ''
        self.approved = 0
        self._seen = set()

    def stop(self):
        self.stop_event.set()

    def run(self):
        while not self.stop_event.is_set():
            try:
                rows = self.operator.requests(self.session_id)
                for row in rows:
                    if (str(row.get('command') or '') == self.command
                            and str(row.get('status') or '') == 'pending'
                            and row.get('id') not in self._seen):
                        ok, _why = self.approver.request(row)
                        if not ok:
                            # A foreign row is not ours to decide. Leave it
                            # for the operator; never widen the token.
                            continue
                        self.operator.approve_request(
                            row['id'], self.approver, self.session_id)
                        self._seen.add(row.get('id'))
                        self.approved += 1
            except Failure as exc:
                self.error = str(exc)
                return
            self.stop_event.wait(0.5)


@dataclass
class Result:
    case: str
    title: str
    status: str
    reason: str = ''
    ids: str = ''


@dataclass
class Case:
    id: str
    title: str
    fn: Callable


class Runner:
    def __init__(self, cfg):
        self.cfg = cfg
        self.operator = Operator(Http(cfg.base_url, cfg.approver_token))
        self.agent = McpAgent(cfg.base_url, cfg.agent_token)
        self.agent_http = Http(cfg.base_url, cfg.agent_token)
        self.window_runner = WindowRunner(cfg)
        self.session_id = ''
        self.agent_id = 0
        self.approver = None
        self.window_id = 0
        self.window_command = ''
        self.cases = self._cases()

    def _cases(self):
        return [
            Case('C1', 'allowlist-no-card', self.case_c1),
            Case('C2', 'jit-approve', self.case_c2),
            Case('C3', 'jit-deny', self.case_c3),
            Case('C4', 'pending-consumed-ran', self.case_c4),
            Case('C5', 'wait-true', self.case_c5),
            Case('C6', 'multiline-before-card', self.case_c6),
            Case('C7', 'plan-cover-then-exhaust', self.case_c7),
            Case('C8', 'plan-superseded', self.case_c8),
            Case('C9', 'probe-service', self.case_c9),
            Case('C10', 'probe-disk', self.case_c10),
            Case('C11', 'window-token', self.case_c11),
            Case('C12', 'dns-check', self.case_c12),
            Case('C13', 'ha-read', self.case_c13),
            Case('C15', 'allowance', self.case_c15),
            Case('C16', 'installer-hash', self.case_c16),
        ]

    # ── preflight ──────────────────────────────────────────────────────

    def preflight(self):
        agents = self.operator.agents()
        expect(len(agents) == 1, 'the approver token did not resolve to one agent')
        agent = agents[0]
        check_test_agent(agent, self.cfg)
        self.agent_id = int(agent.get('id') or 0)
        self.approver = Approver(self.agent_id, '', self.cfg.gateway)

        hash_read = self.operator.installer_hash()
        expect(hash_read.get('gateway_ip') == self.cfg.gateway,
               'the C16 read did not resolve the test gateway')

        started = self.agent.call('session_begin',
                                  goal='E2E:%s: smoke' % self.cfg.run_id)
        self.session_id = str(started.get('session_id') or '')
        expect(bool(self.session_id), 'session_begin returned no session id')
        expect(bool(started.get('ssh_prefix')), 'session_begin returned no prefix')
        self.approver = Approver(self.agent_id, self.session_id, self.cfg.gateway)

        status, detail = self.operator.get('/api/sessions/%s/detail' %
                                           urllib.parse.quote(self.session_id))
        expect(status == 200, 'the approver cannot read the session detail')
        expect(str((detail.get('summary') or {}).get('goal') or '')
               .startswith('E2E:'), 'the session goal is not an E2E goal')

    # ── cases ──────────────────────────────────────────────────────────

    def _rows(self):
        return self.operator.requests(self.session_id)

    def _pending(self):
        return [r for r in self._rows() if r.get('status') == 'pending']

    def _find_row(self, command, status=None):
        rows = [r for r in self._rows() if r.get('command') == command]
        if status:
            rows = [r for r in rows if r.get('status') == status]
        return rows[-1] if rows else None

    def _wait_request(self, request_id, want, timeout=60):
        deadline = time.time() + timeout
        last = ''
        while time.time() < deadline:
            state = self.agent.call('check_request', request_id=int(request_id),
                                    session_id=self.session_id)
            last = str(state.get('status') or '')
            if last == want:
                return state
            if last in ('denied', 'blocked', 'expired', 'abandoned'):
                raise Failure('request ended in %s, not %s' % (last, want))
            time.sleep(1)
        raise Failure('request did not reach %s (last state %s)' % (want, last))

    def _wait_plan(self, plan_id, want, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = self.agent.call('check_plan', plan_id=int(plan_id),
                                    session_id=self.session_id)
            if state.get('status') == want:
                return state
            time.sleep(0.5)
        raise Failure('the plan did not reach %s' % want)

    def _probe(self, plan_id, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            out = self.agent.call('probe_result', plan_id=int(plan_id),
                                  session_id=self.session_id)
            if out.get('status') == 'ran':
                return out
            if out.get('status') in ('refused', 'superseded'):
                raise Failure('the probe plan was %s' % out.get('status'))
            time.sleep(1)
        raise Failure('the probe did not finish')

    def _ssh(self, command, wait=False):
        return self.agent.call('ssh_exec', host=self.cfg.gateway,
                               command=command, session_id=self.session_id,
                               wait=bool(wait), timeout=60)

    def case_c1(self):
        out = self._ssh(ALLOWLIST_COMMAND)
        expect(out.get('status') == 'ran', 'the allowlisted command did not run')
        row = self._find_row(ALLOWLIST_COMMAND)
        expect(row is not None and row.get('status') in ('auto-approved',
                                                         'consumed'),
               'the allowlisted command has no auto-approved row')
        expect(not self._pending(), 'a pending card was left by the allowlist path')
        return Result('C1', 'allowlist-no-card', 'PASS',
                      ids='request=%s' % row.get('id'))

    def case_c2(self):
        out = self._ssh(JIT_COMMAND)
        rid = out.get('request_id')
        expect(out.get('status') == 'pending' and rid,
               'the JIT command was not left pending')
        self.operator.approve_request(rid, self.approver, self.session_id)
        self._wait_request(rid, 'consumed')
        again = self._ssh(JIT_COMMAND)
        expect(again.get('status') == 'ran', 'the approved JIT did not run')
        row = self.operator.request(self.session_id, rid)
        expect(row and row.get('status') == 'consumed',
               'the approved request did not end consumed')
        return Result('C2', 'jit-approve', 'PASS', ids='request=%s' % rid)

    def case_c3(self):
        out = self._ssh(JIT_DENY_COMMAND)
        rid = out.get('request_id')
        expect(out.get('status') == 'pending' and rid,
               'the deny case was not left pending')
        self.operator.deny_request(rid, self.approver, self.session_id)
        state = self.agent.call('check_request', request_id=int(rid),
                                session_id=self.session_id)
        expect(state.get('status') == 'denied', 'the request did not read denied')
        again = self._ssh(JIT_DENY_COMMAND)
        expect(again.get('status') == 'pending', 'a denial was cached')
        expect(int(again.get('request_id') or 0) != int(rid),
               'the denied request id was reused')
        return Result('C3', 'jit-deny', 'PASS', ids='request=%s' % rid)

    def case_c4(self):
        out = self._ssh(REPLAY_COMMAND)
        rid = out.get('request_id')
        expect(out.get('status') == 'pending' and rid,
               'the replay case was not left pending')
        state = self.agent.call('check_request', request_id=int(rid),
                                session_id=self.session_id)
        expect(state.get('status') == 'pending', 'the request did not start pending')
        self.operator.approve_request(rid, self.approver, self.session_id)
        self._wait_request(rid, 'consumed')
        again = self._ssh(REPLAY_COMMAND)
        expect(again.get('status') == 'ran', 'the consumed request did not re-run')
        expect(int(again.get('request_id') or 0) == int(rid),
               'the re-run created a second request')
        return Result('C4', 'pending-consumed-ran', 'PASS',
                      ids='request=%s' % rid)

    def case_c5(self):
        watcher = Waiter(self.operator, self.approver, self.session_id,
                         WAIT_COMMAND)
        watcher.start()
        try:
            out = self._ssh(WAIT_COMMAND, wait=True)
        finally:
            watcher.stop()
            watcher.join(timeout=5)
        expect(not watcher.error, 'the wait watcher refused a row: %s' %
               watcher.error)
        expect(out.get('status') == 'ran', 'ssh_exec(wait=True) did not run')
        rows = [r for r in self._rows() if r.get('command') == WAIT_COMMAND]
        expect(len(rows) == 1, 'wait=true created more than one request row')
        expect(rows[0].get('status') == 'consumed',
               'the wait=true row did not end consumed')
        return Result('C5', 'wait-true', 'PASS', ids='request=%s' % rows[0].get('id'))

    def case_c6(self):
        out = self._ssh(MULTILINE_COMMAND)
        expect(out.get('status') == 'refused', 'a multiline command was not refused')
        expect(out.get('reason') == 'multiline_command',
               'the multiline refusal had another reason')
        expect(not any('\n' in str(r.get('command') or '') for r in self._rows()),
               'a multiline command created a request row')
        return Result('C6', 'multiline-before-card', 'PASS')

    def case_c7(self):
        plan = self.agent.call('session_plan', session_id=self.session_id,
                               items=[{'host': self.cfg.gateway,
                                       'command': PLAN_COMMAND,
                                       'max_uses': 1}],
                               note='E2E plan')
        pid = plan.get('plan_id')
        expect(pid, 'session_plan returned no plan id')
        self.operator.approve_plan(pid, self.approver, self.session_id)
        self._wait_plan(pid, 'approved')
        first = self._ssh(PLAN_COMMAND)
        expect(first.get('status') == 'ran', 'the approved plan did not run once')
        second = self._ssh(PLAN_COMMAND)
        expect(second.get('status') == 'pending',
               'an exhausted plan did not fall back to a JIT card')
        state = self.agent.call('check_plan', plan_id=int(pid),
                                session_id=self.session_id)
        items = state.get('items') or []
        expect(bool(items) and int(items[0].get('remaining') or 0) == 0,
               'the plan still reports remaining uses')
        return Result('C7', 'plan-cover-then-exhaust', 'PASS',
                      ids='plan=%s request=%s' % (pid, second.get('request_id')))

    def case_c8(self):
        first = self.agent.call('session_plan', session_id=self.session_id,
                                items=[{'host': self.cfg.gateway,
                                        'command': PLAN_A, 'max_uses': 1}],
                                note='E2E plan A')
        second = self.agent.call('session_plan', session_id=self.session_id,
                                 items=[{'host': self.cfg.gateway,
                                         'command': PLAN_B, 'max_uses': 1}],
                                 note='E2E plan B')
        a_id, b_id = first.get('plan_id'), second.get('plan_id')
        expect(a_id and b_id, 'the plan proposals returned no ids')
        a = self.agent.call('check_plan', plan_id=int(a_id),
                            session_id=self.session_id)
        b = self.agent.call('check_plan', plan_id=int(b_id),
                            session_id=self.session_id)
        expect(a.get('status') == 'superseded', 'plan A was not superseded')
        expect(int(a.get('superseded_by') or 0) == int(b_id),
               'plan A names the wrong successor')
        expect(b.get('status') == 'proposed', 'plan B is not proposed')
        expect(not [r for r in self._rows()
                    if r.get('command') in (PLAN_A, PLAN_B)],
               'a plan proposal created a request row')
        return Result('C8', 'plan-superseded', 'PASS',
                      ids='plan=%s,%s' % (a_id, b_id))

    def case_c9(self):
        plan = self.agent.call('diagnose_service',
                               {'host': self.cfg.gateway, 'unit': 'ssh'},
                               session_id=self.session_id)
        pid = plan.get('plan_id')
        expect(pid and plan.get('status') == 'proposed',
               'diagnose_service did not propose a plan')
        self.operator.approve_plan(pid, self.approver, self.session_id)
        self._wait_plan(pid, 'approved')
        out = self._probe(pid)
        expect(out.get('probe') == 'diagnose_service', 'the probe name is wrong')
        digest = out.get('digest') or {}
        expect(isinstance(digest, dict) and bool(digest),
               'the service probe returned no digest')
        before = {r.get('id') for r in self._rows()}
        again = self._probe(pid)
        after = {r.get('id') for r in self._rows()}
        expect(again.get('digest') == digest, 'the second digest is not identical')
        expect(before == after, 'the cached probe created a request row')
        return Result('C9', 'probe-service', 'PASS', ids='plan=%s' % pid)

    def case_c10(self):
        plan = self.agent.call('disk_pressure',
                               {'host': self.cfg.gateway},
                               session_id=self.session_id)
        pid = plan.get('plan_id')
        expect(pid and plan.get('status') == 'proposed',
               'disk_pressure did not propose a plan')
        self.operator.approve_plan(pid, self.approver, self.session_id)
        self._wait_plan(pid, 'approved')
        out = self._probe(pid)
        digest = out.get('digest') or {}
        expect(out.get('probe') == 'disk_pressure', 'the probe name is wrong')
        expect(any(key in digest for key in
                   ('filesystems', 'inodes', 'log_dirs')),
               'the disk digest has no filesystem data')
        before = {r.get('id') for r in self._rows()}
        again = self._probe(pid)
        after = {r.get('id') for r in self._rows()}
        expect(again.get('digest') == digest, 'the second digest is not identical')
        expect(before == after, 'the cached probe created a request row')
        return Result('C10', 'probe-disk', 'PASS', ids='plan=%s' % pid)

    def case_c11(self):
        if not self.cfg.gateway_ssh_key:
            raise SkipCase('ESHU_E2E_GATEWAY_SSH_KEY is not set')
        now = int(time.time())
        status, body = self.agent_http.request('POST', '/api/window-requests', {
            'gateway_ip': self.cfg.gateway,
            'command': WINDOW_COMMAND,
            'window_start': now - 5,
            'expires_at': now + 600,
            'max_executions': 1,
            'match_type': 'exact',
            'label': 'E2E:%s' % self.cfg.run_id,
            'session_id': self.session_id,
        })
        expect(status == 200, 'the agent window request failed')
        self.window_id = int(body.get('id') or 0)
        expect(self.window_id, 'the window request returned no id')
        approved = self.operator.approve_window(
            self.window_id, self.approver, self.session_id)
        token = approved.get('token') or ''
        expect(bool(token), 'the approved window returned no token')
        code, _out, _err = self.window_runner.run(token, WINDOW_COMMAND)
        expect(code == 0, 'the first window run failed')
        code2, _out2, _err2 = self.window_runner.run(token, WINDOW_COMMAND)
        expect(code2 != 0, 'the exhausted window ran a second time')
        status, lookup = self.operator.public_get(
            '/api/window-by-token/%s' % urllib.parse.quote(token))
        expect(status == 200, 'the window lookup failed')
        expect(int(lookup.get('execution_count') or 0) == 1,
               'the window did not record exactly one execution')
        return Result('C11', 'window-token', 'PASS',
                      ids='window=%d' % self.window_id)

    def case_c12(self):
        if not self.cfg.dns_name:
            raise SkipCase('ESHU_E2E_DNS_NAME is not set')
        out = self.agent.call('dns_check', {'name': self.cfg.dns_name},
                              session_id=self.session_id)
        expect(out.get('status') == 'ran', 'dns_check did not run')
        digest = out.get('digest') or {}
        expect(isinstance(digest.get('record'), dict),
               'dns_check returned no record')
        return Result('C12', 'dns-check', 'PASS')

    def case_c13(self):
        tools = [t for t in self.agent.list_tools() if t.endswith('_list_entities')]
        if not tools:
            raise SkipCase('no HA list_entities tool is exposed')
        out = self.agent.call(tools[0], limit=1, session_id=self.session_id)
        if isinstance(out, list):
            expect(bool(out), 'the HA read returned nothing')
        else:
            expect(not out.get('error'), 'the HA read returned an error')
            expect(bool(out.get('entities') or out.get('result') or
                        out.get('data') or out.get('states')),
                   'the HA read returned nothing')
        return Result('C13', 'ha-read', 'PASS')

    def case_c15(self):
        if not (self.cfg.allowance_entity and self.cfg.allowance_other_entity):
            raise SkipCase('the standing allowance entity variables are not set')
        tools = [t for t in self.agent.list_tools() if t.endswith('_call_service')]
        if not tools:
            raise SkipCase('no HA call_service tool is exposed')
        before = self.operator.pending_calls(self.session_id)
        first = self.agent.call(tools[0], domain='light', service='turn_on',
                                data={'entity_id': self.cfg.allowance_entity},
                                session_id=self.session_id)
        expect(not first.get('error'), 'the standing allowance call errored')
        after = self.operator.pending_calls(self.session_id)
        expect(len(after) == len(before),
               'the standing allowance call created a pending card')
        second = self.agent.call(
            tools[0], domain='light', service='turn_on',
            data={'entity_id': self.cfg.allowance_other_entity},
            session_id=self.session_id)
        expect(not second.get('error') or second.get('status') == 'pending',
               'the non-matching call errored instead of asking')
        calls = self.operator.pending_calls(self.session_id)
        own = [c for c in calls
               if c.get('session_id') == self.session_id]
        expect(own, 'the non-matching call created no pending integration call')
        self.operator.deny_call(own[-1]['id'], self.approver, self.session_id)
        return Result('C15', 'allowance', 'PASS',
                      ids='call=%s' % own[-1].get('id'))

    def case_c16(self):
        out = self.operator.installer_hash()
        expect(out.get('matches') is True,
               'the gateway installer hash does not match Edge')
        expect(bool(out.get('edge_hash')), 'the Edge channel has no hash')
        return Result('C16', 'installer-hash', 'PASS')

    # ── run and cleanup ────────────────────────────────────────────────

    def _result_for(self, case):
        try:
            return case.fn()
        except SkipCase as exc:
            return Result(case.id, case.title, 'SKIP', reason=str(exc))
        except Failure as exc:
            return Result(case.id, case.title, 'FAIL', reason=self.redact(str(exc)))
        except Exception as exc:  # noqa: BLE001 — a case must not kill the run
            return Result(case.id, case.title, 'FAIL',
                          reason=self.redact('%s: %s' % (type(exc).__name__,
                                                         str(exc))))

    def redact(self, text):
        """Belt and braces: the table never carries a live value."""
        text = str(text or '')
        secrets_to_hide = [self.cfg.agent_token, self.cfg.approver_token,
                           self.cfg.gateway_ssh_key, self.cfg.gateway,
                           self.cfg.dns_name, self.cfg.allowance_entity,
                           self.cfg.allowance_other_entity]
        for value in secrets_to_hide:
            if value:
                text = text.replace(value, '<redacted>')
        for command in COMMANDS:
            text = text.replace(command, '<command>')
        text = re.sub(r'(?i)(token|password|key)\s*[=:]\s*[^\s,;]+',
                      r'\1=<redacted>', text)
        return text[:240]

    def cleanup(self):
        """Stop at the first BY refusal; never widen the credential."""
        errors = []
        try:
            for row in list(self._pending()):
                self.operator.deny_request(row['id'], self.approver,
                                           self.session_id)
            for plan in self.operator.plans(self.session_id):
                if plan.get('status') == 'proposed':
                    self.operator.deny_plan(plan['id'], self.approver,
                                            self.session_id)
            for call in self.operator.pending_calls(self.session_id):
                self.operator.deny_call(call['id'], self.approver,
                                        self.session_id)
        except Failure as exc:
            errors.append('deny: ' + self.redact(str(exc)))
            return errors
        try:
            out = self.agent.call('session_conclude', outcome='partial',
                                  summary='E2E smoke run', session_id=self.session_id)
            draft_id = out.get('id')
            if draft_id:
                self.operator.reject_conclusion(draft_id)
        except Failure as exc:
            errors.append('conclusion: ' + self.redact(str(exc)))
        try:
            self.operator.exclude(self.session_id)
        except Failure as exc:
            errors.append('exclude: ' + self.redact(str(exc)))
        if self.window_id:
            try:
                self.operator.delete_window(self.window_id)
            except Failure as exc:
                errors.append('window: ' + self.redact(str(exc)))
        try:
            if self._pending():
                errors.append('cleanup: pending rows remain')
        except Failure as exc:
            errors.append('cleanup check: ' + self.redact(str(exc)))
        return errors

    def run(self):
        try:
            self.preflight()
        except UnsafeConfig as exc:
            print('REFUSED: %s' % exc)
            return 2
        except Failure as exc:
            print('REFUSED: %s' % self.redact(str(exc)))
            return 2
        results = []
        for case in self.cases:
            if self.cfg.case and case.id != self.cfg.case and case.title != self.cfg.case:
                continue
            results.append(self._result_for(case))
            if results[-1].status == 'FAIL':
                break
        cleanup_errors = [] if self.cfg.keep else self.cleanup()
        self.report(results, cleanup_errors)
        return 1 if any(r.status == 'FAIL' for r in results) else 0

    def report(self, results, cleanup_errors):
        for row in results:
            line = '%s %s %s' % (row.status, row.case, row.title)
            if row.ids:
                line += ' ' + row.ids
            if row.reason:
                line += ' — ' + row.reason
            print(line)
        if cleanup_errors:
            for error in cleanup_errors:
                print('CLEANUP %s' % error)
        elif not self.cfg.keep:
            print('CLEANUP ok')


def _list_cases():
    runner = Runner.__new__(Runner)
    for case in Runner._cases(runner):
        print('%s  %s' % (case.id, case.title))


def main(argv=None):
    parser = argparse.ArgumentParser(description='E2E-1 smoke suite')
    parser.add_argument('--list', action='store_true', help='print cases only')
    parser.add_argument('--json', action='store_true', help='JSON rows')
    args = parser.parse_args(argv)
    if args.list:
        _list_cases()
        return 0
    try:
        cfg = Config.from_env()
    except UnsafeConfig as exc:
        print('REFUSED: %s' % exc)
        return 2
    if args.json:
        runner = Runner(cfg)
        # The human table is still the primary form; JSON is the same rows.
        results = []
        try:
            runner.preflight()
        except (UnsafeConfig, Failure) as exc:
            print(json.dumps({'refused': runner.redact(str(exc))}))
            return 2
        for case in runner.cases:
            if cfg.case and case.id != cfg.case and case.title != cfg.case:
                continue
            results.append(dataclasses.asdict(runner._result_for(case)))
            if results[-1]['status'] == 'FAIL':
                break
        cleanup = [] if cfg.keep else runner.cleanup()
        print(json.dumps({'cases': results, 'cleanup': cleanup}, sort_keys=True))
        return 1 if any(r['status'] == 'FAIL' for r in results) else 0
    return Runner(cfg).run()


if __name__ == '__main__':
    sys.exit(main())
