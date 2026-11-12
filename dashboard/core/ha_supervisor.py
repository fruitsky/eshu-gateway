"""F9: read-only Home Assistant Supervisor views — one fixed endpoint table.

HA Core forwards Supervisor calls over the WebSocket API with
`supervisor/api` (`homeassistant/components/hassio/websocket_api.py`,
`@require_admin`, schema `endpoint, method, data?, params?, timeout?`). **HA
applies no endpoint allowlist**: any admin token can call every Supervisor
endpoint — host reboot, backup restore, add-on options. Eshu therefore owns
the allowlist, and it is this file: an agent never supplies an endpoint, and
no other module may build one by hand.

F9 ships read tools only. Every entry here is `GET`; F10 adds the action
endpoints as separate entries so an allowlist test can assert that the set of
endpoints any tool can send is exactly this table.

The response shapers are allowlists too: Supervisor payloads carry more than
the agent needs (`ha_addon_info` in particular returns `options`, which holds
add-on secrets), so a shaper picks named fields instead of deleting known-bad
ones.
"""
import json
import re
import urllib.parse

from core.ha_ws import ha_ws_exec
from core.integration_proxy import ProxyError
from core.secret_scrub import scrub_string

# Tool name -> (endpoint template, method, shaper). `{slug}` is validated
# before formatting, never interpolated raw.
SUPERVISOR_READS = {
    'ha_supervisor_info': ('/supervisor/info', 'get', 'supervisor'),
    'ha_core_info': ('/core/info', 'get', 'core'),
    'ha_os_info': ('/os/info', 'get', 'os'),
    'ha_host_info': ('/host/info', 'get', 'host'),
    'ha_addons': ('/addons', 'get', 'addons'),
    'ha_addon_info': ('/addons/{slug}/info', 'get', 'addon'),
    'ha_backups': ('/backups', 'get', 'backups'),
    'ha_resolution': ('/resolution/info', 'get', 'resolution'),
}

# F10: the mutating endpoints, kept separate so SUPERVISOR_READS stays "reads".
# A tool's entry is (endpoint template, method, shape). `{slug}` and `{action}`
# are validated before formatting; `WS_ACTIONS` covers the two update endpoints,
# which Supervisor exposes over the WS command bus rather than supervisor/api.
SUPERVISOR_ACTIONS = {
    'ha_addon_action': ('/addons/{slug}/{action}', 'post', 'action'),
    'ha_addon_update': ('hassio/update/addon', 'ws', 'action'),
    'ha_core_update': ('hassio/update/core', 'ws', 'action'),
    'ha_backup_create': ('/backups/new/full', 'post', 'action'),
    'ha_core_restart': ('/core/restart', 'post', 'action'),
    'ha_host_reboot': ('/host/reboot', 'post', 'action'),
}

# The two update endpoints are WebSocket command types, not supervisor/api
# endpoints. Their payload shape is decided by HA Core's `hassio/update.py`.
WS_ACTIONS = {
    'ha_addon_update': 'hassio/update/addon',
    'ha_core_update': 'hassio/update/core',
}

ACTION_VALUES = {'ha_addon_action': ('start', 'stop', 'restart')}
NAME_RE = re.compile(r'^[A-Za-z0-9_. -]{1,64}$')
FOLDER_VALUES = ('ssl', 'addons', 'config', 'share', 'local', 'media', 'backup')

# F10: endpoints no tool may ever send. The table test asserts that nothing in
# SUPERVISOR_READS / SUPERVISOR_ACTIONS / WS_ACTIONS starts with one of these.
# Restore, add-on reconfiguration, host power, the store, auth/security and the
# OS disk/boot tools stay operator-only (dashboard UI or the HA app itself).
FORBIDDEN = (
    '/backups/', '/addons/', '/supervisor/options', '/host/shutdown',
    '/store/repositories', '/auth', '/security', '/os/datadisk', '/os/boot-slot',
)

SLUG_RE = re.compile(r'^[a-z0-9_]{1,64}$')

# M1: HA Core proxies `/api/hassio/…` to the Supervisor
# (`homeassistant/components/hassio/http.py`, one catch-all view) and
# `POST /api/services/hassio/…` runs the Supervisor's own services. Neither is
# in this table, so the generic REST tools must not reach them either.
REST_SUPERVISOR_PREFIXES = ('hassio', 'services/hassio')


def _normalise_rest_path(path) -> str:
    """Decode-until-stable, then strip the leading slash and an `api/` prefix.

    Returns None when the path is non-canonical (a dot segment, a backslash or
    a control character), which every caller refuses, and '' for an empty one.
    """
    text = str(path or '')
    # N1: unquote until stable. One pass would let `%252e%252e` become
    # `%2e%2e` and be decoded again by the proxy.
    while True:
        decoded = urllib.parse.unquote(text)
        if decoded == text:
            break
        text = decoded
    if '\\' in text or any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in text):
        return None
    text = text.strip()
    if any(segment in ('.', '..') for segment in text.split('/')):
        return None
    text = text.lower()
    while '//' in text:
        text = text.replace('//', '/')
    text = text.lstrip('/')
    # A base URL that ends in /api and a path that starts with api/ would
    # otherwise leave two prefixes; strip them all.
    while text.startswith('api/'):
        text = text[len('api/'):]
    return text.lstrip('/')


def is_supervisor_rest_path(path) -> bool:
    """Is this HA REST path the Supervisor proxy, its service domain, or a
    non-canonical spelling of *any* HA path?

    The same request has many spellings — percent-encoded, upper-case, with
    `//` or an `api/` prefix — so the path is normalised before the check.

    N1: a dot segment, a backslash or a control character is refused whatever
    the prefix is. `urllib` sends the bytes as given, but a reverse proxy in
    front of HA (nginx/NPM) resolves `..` and decodes `%2e`, so
    `api/states/../hassio/backups/x/restore` would otherwise reach the
    Supervisor. HA has no legitimate API path with those, and refusing beats
    normalising: the bytes sent must be the bytes checked.

    S-a: the first segment only has to *start* with `hassio`, which also
    covers the add-on ingress proxy (`hassio_ingress/...`).
    """
    text = _normalise_rest_path(path)
    if text is None:
        return True
    if not text:
        return False
    if text.split('/', 1)[0].startswith('hassio'):
        return True
    for prefix in REST_SUPERVISOR_PREFIXES:
        if text == prefix or text.startswith(prefix + '/'):
            return True
    return False


def is_supervisor_rest_request(base_url, path) -> bool:
    """Check the path the *request* will carry: the integration's own base
    path plus the tool's path (S-a). A base URL that ends in `/hassio` is as
    forbidden as a tool path that starts with it."""
    base_path = urllib.parse.urlparse(str(base_url or '')).path or ''
    return is_supervisor_rest_path((base_path.rstrip('/') + '/' +
                                    str(path or '')).strip('/'))


ADMIN_CONFIG_PREFIXES = ('config/automation', 'config/script', 'config/scene')


def is_admin_config_write(path) -> bool:
    """S-b: does this HA REST write edit an automation, script or scene?

    Such a document can define an action that calls any `hassio.*` service, so
    approving it grants Supervisor power even though the path is not
    FORBIDDEN. The approval card must say so.
    """
    text = _normalise_rest_path(path)
    return bool(text) and text.startswith(ADMIN_CONFIG_PREFIXES)


# The fields each shaper may pass through. Anything not named is dropped —
# most importantly `options` on an add-on.
_SHAPES = {
    'supervisor': ('version', 'channel', 'hostname', 'machine', 'arch',
                   'supported', 'healthy', 'timezone', 'logging',
                   'diagnostics', 'addons_repositories'),
    'core': ('version', 'version_latest', 'update_available', 'arch',
             'machine', 'ip_address', 'ssl', 'watchdog', 'audio_input',
             'audio_output', 'backups_exclude_database'),
    'os': ('version', 'version_latest', 'update_available', 'board', 'boot',
           'data_disk', 'data_disk_used', 'data_disk_total'),
    'host': ('hostname', 'operating_system', 'kernel', 'boot_id', 'dt_synchronized',
             'cpe', 'chassis', 'virtualization', 'disk_total', 'disk_used',
             'memory_total', 'memory_used', 'memory_free', 'swap_total',
             'swap_used', 'swap_free'),
    'addons': ('name', 'slug', 'description', 'advanced', 'stage', 'version',
               'version_latest', 'update_available', 'state', 'repository'),
    'addon': ('name', 'slug', 'description', 'advanced', 'stage', 'version',
              'version_latest', 'update_available', 'state', 'repository',
              'arch', 'ingress', 'ingress_url', 'ingress_entry', 'watchdog',
              'devices', 'network', 'hostname', 'url'),
    'backups': ('name', 'slug', 'date', 'type', 'size', 'location',
                'protected', 'supervisor_version', 'homeassistant'),
    'resolution': ('unsupported', 'unhealthy', 'issues'),
    'action': ('result', 'data', 'message'),
}

_ISSUE_FIELDS = ('type', 'context', 'reference', 'created', 'dismissed', 'uuid')


def endpoint_for(tool_name: str, args: dict = None):
    """The one endpoint a tool may send, or raise KeyError.

    The slug is validated before formatting so a tool argument can never
    reach another endpoint (`../`, `?`, `/`).
    """
    entry = SUPERVISOR_READS.get(tool_name) or SUPERVISOR_ACTIONS.get(tool_name)
    if not entry:
        raise KeyError(tool_name)
    template, method, _shape = entry
    endpoint = template
    if '{slug}' in template:
        slug = str((args or {}).get('slug') or '').strip()
        if not SLUG_RE.match(slug):
            raise ValueError(slug)
        endpoint = template.replace('{slug}', slug)
    if '{action}' in template:
        action = str((args or {}).get('action') or '').strip().lower()
        allowed = ACTION_VALUES.get(tool_name, ())
        if action not in allowed:
            raise ValueError(action or 'missing')
        endpoint = endpoint.replace('{action}', action)
    return endpoint, method


def _pick(row, fields):
    if not isinstance(row, dict):
        return row
    return {key: row[key] for key in fields if key in row}


def _payload(shape, data):
    """D12: the Supervisor wraps a response in `data`.

    A dict-shaped read (`/core/info`) has its fields at the top of the
    reply, but the list-shaped reads (`/addons`, `/backups`) put the list at
    `data['data'][<shape>]`. Shaping the wrapper returned `{}` for both.
    """
    if not isinstance(data, dict):
        return data
    inner = data.get('data')
    if isinstance(inner, dict):
        rows = inner.get(shape)
        if isinstance(rows, list):
            return rows
        return inner
    rows = data.get(shape)
    if shape in ('addons', 'backups') and isinstance(rows, list):
        return rows
    return data


def _shape(shape, data):
    """Whitelist fields. Lists are shaped per row and capped, so one huge
    Supervisor response cannot flood a model's context."""
    data = _payload(shape, data)
    fields = _SHAPES.get(shape, ())
    if isinstance(data, list):
        return [_pick(row, fields) for row in data[:200]]
    if shape == 'resolution' and isinstance(data, dict):
        out = {'unsupported': data.get('unsupported') or [],
               'unhealthy': data.get('unhealthy') or [],
               'issues': [_pick(issue, _ISSUE_FIELDS)
                          for issue in (data.get('issues') or [])[:50]]}
        return out
    if isinstance(data, dict):
        return _pick(data, fields)
    return data


def _admin_error(detail: str) -> str:
    return json.dumps({
        'error': 'ha_admin_required',
        'message': ("The HA token is not an admin token. Supervisor tools need "
                    "an admin user — set a dedicated admin long-lived token on "
                    "the HA integration, then try again."),
        'detail': scrub_string(detail or ''),
        'status_code': 403,
    })


def run_supervisor_read(integration: dict, tool_name: str, args: dict,
                        ctx: dict) -> str:
    """Call one fixed Supervisor endpoint and return a scrubbed, shaped JSON
    string. Used by the `ha_supervisor_read` handler for every read tool."""
    try:
        endpoint, method = endpoint_for(tool_name, args)
    except KeyError:
        return json.dumps({'error': 'ha_supervisor_unknown',
                           'message': "No Supervisor endpoint for '%s'" % tool_name,
                           'status_code': 400})
    except ValueError as exc:
        return json.dumps({'error': 'ha_supervisor_bad_slug',
                           'message': 'Invalid add-on slug: %r' % (str(exc),),
                           'status_code': 400})
    shape = (SUPERVISOR_READS.get(tool_name)
             or SUPERVISOR_ACTIONS.get(tool_name))[2]
    payload = {'endpoint': endpoint, 'method': method}
    try:
        data = ha_ws_exec(
            integration, 'supervisor/api', payload,
            agent=ctx.get('agent', ''), tool_name=tool_name,
            session_id=ctx.get('session_id', ''),
            execution_id=ctx.get('execution_id', ''),
            reason=ctx.get('reason', ''),
            approval=ctx.get('approval', ''),
            decided_at=ctx.get('decided_at', 0),
            decided_by=ctx.get('decided_by', ''))
    except ProxyError as exc:
        message = str(exc.message or '')
        if exc.status_code in (401, 403) or 'admin' in message.lower() \
                or 'unauthor' in message.lower():
            return _admin_error(message)
        return json.dumps({'error': 'ha_supervisor_error',
                           'message': scrub_string(message)[:400],
                           'status_code': exc.status_code or 502})
    return scrub_string(json.dumps(_shape(shape, data)))


def _action_payload(tool_name: str, args: dict) -> dict:
    """The supervisor/api payload (or WS payload) for one action, validated."""
    args = args or {}
    if tool_name == 'ha_addon_action':
        return {}
    if tool_name == 'ha_backup_create':
        name = str(args.get('name') or '').strip()
        if not NAME_RE.match(name):
            raise ValueError('name')
        addons = args.get('partial_addons') or []
        folders = args.get('partial_folders') or []
        if addons:
            if not isinstance(addons, list) or any(
                    not SLUG_RE.match(str(a).strip()) for a in addons):
                raise ValueError('partial_addons')
            clean_addons = [str(a).strip() for a in addons]
            clean_folders = [str(f).strip() for f in folders
                             if str(f).strip() in FOLDER_VALUES]
            return {'name': name, 'addons': clean_addons,
                    'folders': clean_folders}
        if folders:
            clean = [str(f).strip() for f in folders if str(f).strip() in FOLDER_VALUES]
            if not clean:
                raise ValueError('partial_folders')
            return {'name': name, 'addons': [], 'folders': clean}
        return {'name': name}
    if tool_name in ('ha_addon_update', 'ha_core_update'):
        backup = args.get('backup', True)
        if not isinstance(backup, bool):
            raise ValueError('backup')
        if tool_name == 'ha_core_update':
            return {'backup': backup}
        slug = str(args.get('slug') or '').strip()
        if not SLUG_RE.match(slug):
            raise ValueError('slug')
        return {'addon': slug, 'backup': backup}
    return {}


def run_supervisor_action(integration: dict, tool_name: str, args: dict,
                          ctx: dict) -> str:
    """F10: run one fixed Supervisor action. Never builds an endpoint from
    agent text — the table decides, and the arguments only fill validated
    placeholders (G/F9 R2 rule)."""
    if tool_name not in SUPERVISOR_ACTIONS:
        return json.dumps({'error': 'ha_supervisor_unknown',
                           'message': "No Supervisor action for '%s'" % tool_name,
                           'status_code': 400})
    try:
        endpoint, method = endpoint_for(tool_name, args)
        payload = _action_payload(tool_name, args)
        # A partial backup is a different endpoint, decided by the validated
        # payload, not by anything the agent wrote into a path.
        if tool_name == 'ha_backup_create' and payload.get('addons'):
            endpoint = '/backups/new/partial'
        elif tool_name == 'ha_backup_create' and payload.get('folders'):
            endpoint = '/backups/new/partial'
    except ValueError as exc:
        return json.dumps({'error': 'ha_supervisor_bad_argument',
                           'message': 'Invalid Supervisor action argument: %r' % (str(exc),),
                           'status_code': 400})
    # A backup, an add-on update or a Core update can run for minutes; the
    # other actions answer in seconds. HA's supervisor/api has its own per-call
    # timeout (10 s by default), so both sides are raised together.
    long_running = tool_name in ('ha_addon_update', 'ha_core_update',
                                 'ha_backup_create')
    read_timeout = 620 if long_running else 60
    if method != 'ws':
        payload = {'endpoint': endpoint, 'method': method,
                   'data': payload,
                   'timeout': 600 if tool_name == 'ha_backup_create' else 120}
    try:
        if method == 'ws':
            command = WS_ACTIONS[tool_name]
            data = ha_ws_exec(
                integration, command, payload,
                agent=ctx.get('agent', ''), tool_name=tool_name,
                session_id=ctx.get('session_id', ''),
                execution_id=ctx.get('execution_id', ''),
                reason=ctx.get('reason', ''),
                approval=ctx.get('approval', ''),
                decided_at=ctx.get('decided_at', 0),
                decided_by=ctx.get('decided_by', ''),
                read_timeout=read_timeout)
        else:
            data = ha_ws_exec(
                integration, 'supervisor/api', payload,
                agent=ctx.get('agent', ''), tool_name=tool_name,
                session_id=ctx.get('session_id', ''),
                execution_id=ctx.get('execution_id', ''),
                reason=ctx.get('reason', ''),
                approval=ctx.get('approval', ''),
                decided_at=ctx.get('decided_at', 0),
                decided_by=ctx.get('decided_by', ''),
                read_timeout=read_timeout)
    except ProxyError as exc:
        message = str(exc.message or '')
        if exc.status_code in (401, 403) or 'admin' in message.lower() \
                or 'unauthor' in message.lower():
            return _admin_error(message)
        return json.dumps({'error': 'ha_supervisor_action_failed',
                           'message': scrub_string(message)[:400],
                           'status_code': exc.status_code or 502})
    out = {'ok': True, 'action': tool_name,
           'endpoint': endpoint if method != 'ws' else WS_ACTIONS[tool_name]}
    if isinstance(data, dict):
        if data.get('result') not in (None, '', 'ok'):
            out['result'] = data.get('result')
        if isinstance(data.get('data'), dict):
            out['data'] = _shape('action', data.get('data'))
    return scrub_string(json.dumps(out))


def run_whoami(integration: dict, ctx: dict) -> str:
    """`auth/current_user` over the HA WebSocket: which user the token is."""
    try:
        data = ha_ws_exec(
            integration, 'auth/current_user', {},
            agent=ctx.get('agent', ''), tool_name='ha_whoami',
            session_id=ctx.get('session_id', ''),
            execution_id=ctx.get('execution_id', ''),
            reason=ctx.get('reason', ''),
            approval=ctx.get('approval', ''),
            decided_at=ctx.get('decided_at', 0),
            decided_by=ctx.get('decided_by', ''))
    except ProxyError as exc:
        return json.dumps({'error': 'ha_whoami_error',
                           'message': scrub_string(str(exc.message or ''))[:400],
                           'status_code': exc.status_code or 502})
    if not isinstance(data, dict):
        data = {}
    return scrub_string(json.dumps({
        'name': data.get('name', ''),
        'is_admin': bool(data.get('is_admin')),
        'is_owner': bool(data.get('is_owner')),
    }))
