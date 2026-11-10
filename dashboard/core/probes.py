"""F15: diagnostic probes.

A probe is **code, not agent text**: a fixed list of commands assembled from
typed, validated parameters. The agent chooses a probe and its typed
parameters; it never composes a command.

Execution goes through the F12 runner as ordinary requests, after **one**
grouped approval (an F3 plan the probe's own code proposes — P4). The digest
is parsed from the sanitised outputs and capped (P7). Nothing here reads
`/etc/eshu-capture`: the run's output comes back in the F12 envelope, so a
gateway on `exit-only` still yields a digest (P3).

Design note: F4's read lane deliberately refuses `systemctl show`,
`docker logs` and `pct status` (see `read_policy`'s probe-class tests), and
P2 requires `systemctl show` over parsing `systemctl status`, so a probe does
not rely on auto-run: every probe is one operator-approved plan. The
parameters are validated and shell-quoted, so no agent text reaches a shell;
that, not lane eligibility, is the guarantee here.
"""
import json
import re
import shlex
import time

from core.secret_scrub import scrub_string

# ── typed parameters (P1: the first character is never a dash) ──────────────

UNIT_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9@._-]{0,127}$')
DOCKER_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')
VMID_RE = re.compile(r'^[0-9]{3,9}$')
_LABEL = r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?'
FQDN_RE = re.compile(r'^%s(?:\.%s)+$' % (_LABEL, _LABEL))
IP_RE = re.compile(r'^[0-9A-Fa-f:.]{2,64}$')


class ProbeError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _unit(value):
    text = str(value or '').strip()
    if not UNIT_RE.match(text):
        raise ProbeError('probe_invalid_param',
                         'unit must match [A-Za-z0-9][A-Za-z0-9@._-]{0,127}')
    return text


def _container(value):
    text = str(value or '').strip()
    if VMID_RE.match(text):
        return 'pct', text          # a Proxmox vmid
    if DOCKER_RE.match(text):
        return 'docker', text
    raise ProbeError('probe_invalid_param',
                     'container must be a Docker name or a numeric vmid')


def _name(value):
    text = str(value or '').strip().lower().rstrip('.')
    if '..' in text or not FQDN_RE.match(text):
        raise ProbeError('probe_invalid_param',
                         'name must be an FQDN of RFC labels, no leading dash '
                         'and no empty label')
    return text


def _host(value):
    text = str(value or '').strip()
    if not IP_RE.match(text):
        raise ProbeError('probe_invalid_param', 'host must be a gateway IP')
    from db.gateways import get_gateway
    if not get_gateway(text):
        raise ProbeError('probe_unknown_host', '%s is not a registered gateway' % text)
    return text


PARAMS = {'host': _host, 'unit': _unit, 'container': _container, 'name': _name}


def validate(name, params):
    spec = PROBES.get(name)
    if not spec:
        raise ProbeError('probe_unknown', "No probe '%s'" % name)
    params = params or {}
    out = {}
    for key, kind in spec['params'].items():
        if key not in params:
            raise ProbeError('probe_missing_param', "'%s' is required" % key)
        out[key] = PARAMS[kind](params[key])
    extra = set(params) - set(spec['params'])
    if extra:
        raise ProbeError('probe_invalid_param',
                         'unknown parameter(s): ' + ', '.join(sorted(extra)))
    return out


# ── the probes (P2, P6: bounded, structured sources) ────────────────────────

def _q(value):
    return shlex.quote(str(value))


def commands(name, params):
    """The fixed command list for a validated probe call."""
    p = params
    if name == 'diagnose_service':
        unit = _q(p['unit'])
        return [
            # P2: a structured state, never prose from `systemctl status`.
            {'label': 'state', 'command': 'systemctl is-active --no-pager %s' % unit,
             'structured': True},
            {'label': 'journal',
             'command': 'journalctl -u %s -n 200 --no-pager -o short-iso' % unit},
            {'label': 'ports', 'command': 'ss -tlnp'},
        ]
    if name == 'diagnose_container':
        kind, value = p['container']
        value = _q(value)
        if kind == 'pct':
            return [{'label': 'list', 'command': 'pct list'},
                    {'label': 'config', 'command': 'pct config %s' % value}]
        return [{'label': 'ps', 'command': 'docker ps -a --no-trunc'},
                {'label': 'inspect', 'command': 'docker inspect %s' % value}]
    if name == 'disk_pressure':
        return [
            {'label': 'df', 'command': 'df -h'},
            {'label': 'inodes', 'command': 'df -i'},
            # P6: one filesystem, one level, bounded by the run's time limit.
            {'label': 'logs', 'command': 'du -x -d 1 /var/log'},
        ]
    if name == 'dns_check':
        # P8: Pi-hole only unless F14 maps a gateway; the agent never names it.
        return []
    raise ProbeError('probe_unknown', "No probe '%s'" % name)


PROBES = {
    'diagnose_service': {'params': {'host': 'host', 'unit': 'unit'},
                         'description': 'Service state, recent errors, ports.'},
    'diagnose_container': {'params': {'host': 'host', 'container': 'container'},
                           'description': 'Container or guest state.'},
    'disk_pressure': {'params': {'host': 'host'},
                      'description': 'Filesystem, inode and /var/log pressure.'},
    'dns_check': {'params': {'name': 'name'},
                  'description': 'The local DNS record for a name.'},
}

# ── digest parsing (P5 scrub first, P7 a byte cap with a drop order) ───────

MAX_JSON = 8000
MAX_ERRORS = 10
_COUNTY = re.compile(r'\b(?:0x)?[0-9a-fA-F]{2,}\b')
_NUMBER = re.compile(r'\b\d+\b')


def _normalise_message(line):
    """Strip the parts that make two occurrences of one error differ."""
    text = line.strip()
    text = _COUNTY.sub('<n>', text)
    text = _NUMBER.sub('<n>', text)
    return text


def parse_errors(text, now=None):
    """De-duplicate journal lines by their normalised message."""
    now = int(now or time.time())
    found = {}
    for line in (text or '').splitlines():
        line = line.strip()
        if not line:
            continue
        # Drop journalctl's own timestamp/prefix; keep the message.
        body = line
        if re.match(r'^\d{4}-\d\d-\d\dT', line):
            parts = line.split(None, 2)
            body = parts[2] if len(parts) > 2 else line
        key = _normalise_message(body)
        row = found.get(key)
        if not row:
            found[key] = {'msg': body[:300], 'count': 1, 'last': now}
        else:
            row['count'] += 1
    rows = sorted(found.values(), key=lambda r: (-r['count'], r['msg']))
    return rows[:MAX_ERRORS]


def parse_ss(text):
    ports = []
    for line in (text or '').splitlines():
        if 'LISTEN' not in line:
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        local = parts[3]
        addr, _, port = local.rpartition(':')
        if not port.isdigit():
            continue
        proc = ''
        if 'users:' in line:
            proc = line.split('users:', 1)[1].strip().strip('"')
            proc = proc.split('"')[1] if '"' in proc else proc[:40]
        ports.append({'addr': addr, 'port': int(port), 'process': proc[:40]})
    return ports


def _df_rows(text):
    rows, seen_header = [], False
    for line in (text or '').splitlines():
        if not seen_header:
            if line.startswith('Filesystem'):
                seen_header = True
            continue
        parts = line.split()
        if len(parts) >= 6 and parts[4].endswith('%'):
            rows.append({'mount': parts[5], 'size': parts[1], 'used': parts[2],
                         'avail': parts[3], 'use_pct': parts[4]})
        elif len(parts) >= 5 and parts[4].endswith('%'):
            rows.append({'mount': parts[5] if len(parts) > 5 else parts[4],
                         'size': parts[1], 'used': parts[2],
                         'avail': parts[3], 'use_pct': parts[4]})
    return rows


def _du_rows(text):
    rows = []
    for line in (text or '').splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[1].startswith('/'):
            rows.append({'path': parts[1].strip(), 'size': parts[0]})
    return rows


def _kv(text):
    out = {}
    for line in (text or '').splitlines():
        key, sep, value = line.partition('=')
        if sep:
            out[key.strip()] = value.strip()
    return out


def build_digest(name, outputs, context=None):
    """Turn raw command outputs into the probe's digest dict (scrubbed)."""
    context = context or {}
    clean = {k: scrub_string(v or '') for k, v in (outputs or {}).items()}
    digest = {'probe': name, 'params': context.get('params') or {},
              'truncated': False}
    if name == 'diagnose_service':
        state = (clean.get('state') or '').strip().splitlines()
        digest['state'] = (state[0].strip() if state else 'unknown') or 'unknown'
        digest['errors'] = parse_errors(clean.get('journal'))
        digest['ports'] = parse_ss(clean.get('ports'))
        digest['error_total'] = sum(e['count'] for e in digest['errors'])
    elif name == 'diagnose_container':
        digest['ps'] = [line for line in (clean.get('ps') or '').splitlines()
                        if line.strip()][:50]
        props = _kv(clean.get('inspect'))
        digest['running'] = props.get('State.Running') or props.get('Running') or ''
        digest['status'] = props.get('State.Status') or ''
        digest['config'] = [line for line in (clean.get('config') or '').splitlines()
                            if line.strip()][:40]
    elif name == 'disk_pressure':
        digest['filesystems'] = _df_rows(clean.get('df'))
        digest['inodes'] = _df_rows(clean.get('inodes'))
        digest['log_dirs'] = _du_rows(clean.get('logs'))
    elif name == 'dns_check':
        digest['record'] = context.get('record') or None
        digest['gateway'] = None
        digest['gateway_reason'] = ('no gateway is mapped to this name; ask the '
                                    'Pi-hole record only')
    else:
        raise ProbeError('probe_unknown', "No probe '%s'" % name)
    if context.get('recent_changes') is not None:
        digest['recent_changes'] = context['recent_changes']
    return cap_digest(digest)


def cap_digest(digest):
    """P7: a byte cap with a documented drop order (errors, then ports)."""
    def size(d):
        # The default separators: the cap is on what a caller actually gets,
        # not on a more compact rendering.
        return len(json.dumps(d, sort_keys=True))

    out = json.loads(json.dumps(digest))
    if size(out) <= MAX_JSON:
        return out
    out['truncated'] = True
    for key in ('errors',):
        if len(out.get(key) or []) > MAX_ERRORS:
            out[key] = out[key][:MAX_ERRORS]
    if size(out) <= MAX_JSON:
        return out
    for key in ('ports', 'log_dirs', 'ps', 'config'):
        while out.get(key) and size(out) > MAX_JSON:
            out[key].pop()
    while out.get('errors') and size(out) > MAX_JSON:
        out['errors'].pop()
    if size(out) > MAX_JSON:
        out.pop('filesystems', None)
        out.pop('inodes', None)
    return out


# ── execution: one grouped plan, then the digest (P3, P4) ──────────────────

def _recent_changes(host):
    """Eshu's own approved writes on that host in the last 24 h."""
    try:
        from db.requests import get_all_requests
        since = int(time.time()) - 86400
        return [{'id': r['id'], 'command': (r.get('command') or '')[:120],
                 'at': r.get('created_at')}
                for r in get_all_requests()
                if r.get('target_ip') == host and r.get('created_at', 0) >= since
                and r.get('status') in ('approved', 'consumed')
                and (r.get('reason') or '').startswith('jit')][:5]
    except Exception:
        return []


def plan_probe(name, params, session_id):
    """Propose the probe's commands as ONE plan; nothing runs yet (P4)."""
    from db.session_plans import PlanError, create_plan
    from db.misc import set_meta_json
    try:
        p = validate(name, params)
    except ProbeError as exc:
        return {'error': exc.code, 'message': exc.message}
    cmds = commands(name, p)
    sid = (session_id or '').strip()[:64]
    if not sid:
        return {'error': 'probe_no_session',
                'message': 'probes run through a session; pass session_id'}
    if name == 'dns_check':
        # Pi-hole only (P8): no SSH step, so no plan.
        return _run_dns(p)
    try:
        plan = create_plan(sid, [{'host': p['host'], 'command': c['command'],
                                  'max_uses': 1} for c in cmds],
                           note='probe:%s' % name, origin='probe')
    except PlanError as exc:
        return {'error': exc.code, 'message': exc.message}
    set_meta_json('probe_plan_%d' % plan['plan_id'],
                  {'probe': name, 'params': p,
                   'commands': [c['command'] for c in cmds],
                   'labels': [c['label'] for c in cmds],
                   'created_at': int(time.time())})
    return {'status': 'pending', 'plan_id': plan['plan_id'],
            'probe': name, 'session_id': sid,
            'items': plan['items'], 'hosts': plan['hosts'],
            'next_step': ('Wait for the operator to approve plan #%d, then call '
                          'probe_result(%d).' % (plan['plan_id'], plan['plan_id']))}


def _run_dns(p):
    """The Pi-hole record for a name; nothing on a gateway (P8)."""
    record = None
    reason = ''
    try:
        from db.integrations import get_integrations
        from core.integration_proxy import execute_integration_call
        for integration in get_integrations(include_secret=False):
            if (integration.get('kind') or '').lower() not in ('pihole', 'pi-hole'):
                continue
            tool = {'name': 'local_dns_records', 'enabled': 1, 'method': 'GET',
                    'path_template': 'admin/api.php?customdns', 'params': [],
                    'transport': 'http', 'read_only': 1}
            res = execute_integration_call(integration, tool, {})
            body = res.get('body') or '{}'
            data = json.loads(body) if body.strip().startswith('{') else {}
            hosts = data.get('hosts') or data.get('data') or []
            for row in hosts if isinstance(hosts, list) else []:
                if str(row.get('domain') or '').lower() == p['name']:
                    record = {'domain': row.get('domain'),
                              'ip': row.get('ip') or row.get('ipv4')}
                    break
            break
    except Exception as exc:  # noqa: BLE001
        reason = scrub_string(str(exc))[:200]
    digest = build_digest('dns_check', {},
                          {'params': p, 'record': record,
                           'recent_changes': []})
    if reason:
        digest['error'] = reason
    return {'status': 'ran', 'probe': 'dns_check', 'digest': digest}


def probe_result(plan_id, session_id=''):
    """Run an approved probe plan and return its digest."""
    from db.misc import get_meta_json, set_meta_json
    from db.session_plans import get_plan
    meta = get_meta_json('probe_plan_%d' % int(plan_id), None)
    if not meta:
        return {'error': 'probe_not_found',
                'message': 'No probe plan #%s' % plan_id}
    done = get_meta_json('probe_result_%d' % int(plan_id), None)
    if done:
        return {'status': 'ran', 'plan_id': int(plan_id),
                'probe': meta.get('probe'), 'digest': done}
    plan = get_plan(int(plan_id)) or {}
    if plan.get('session_id') != (session_id or '').strip()[:64]:
        return {'error': 'probe_not_found', 'message': 'No probe plan #%s' % plan_id}
    if plan.get('status') == 'proposed':
        return {'status': 'pending', 'plan_id': int(plan_id),
                'probe': meta.get('probe'),
                'next_step': 'Waiting for the operator to approve the plan.'}
    if plan.get('status') != 'approved':
        return {'status': 'refused', 'plan_id': int(plan_id),
                'probe': meta.get('probe'),
                'reason': 'plan is %s' % plan.get('status')}
    outputs, problems = {}, []
    for label, command in zip(meta.get('labels') or [], meta.get('commands') or []):
        res = _run_one(meta['params'].get('host'), command,
                       session_id=session_id)
        if res.get('status') != 'ran':
            problems.append(res.get('reason') or res.get('error') or 'refused')
            continue
        outputs[label] = res.get('output') or ''
    if not outputs:
        return {'status': 'refused', 'plan_id': int(plan_id),
                'probe': meta.get('probe'),
                'reason': problems[0] if problems else 'nothing ran'}
    digest = build_digest(meta['probe'], outputs,
                          {'params': meta['params'],
                           'recent_changes': _recent_changes(meta['params'].get('host'))})
    if problems:
        digest['incomplete'] = problems[:3]
    set_meta_json('probe_result_%d' % int(plan_id), digest)
    return {'status': 'ran', 'plan_id': int(plan_id),
            'probe': meta['probe'], 'digest': digest}


def _run_one(host, command, session_id=''):
    from core.ssh_exec import ssh_exec
    try:
        res = ssh_exec(host, command, session_id=session_id, wait=False)
    except Exception as exc:  # noqa: BLE001
        return {'status': 'refused', 'error': scrub_string(str(exc))[:200]}
    if res.get('status') == 'ran':
        return {'status': 'ran', 'output': res.get('output') or ''}
    return {'status': res.get('status') or 'refused',
            'reason': res.get('reason') or res.get('error') or ''}
