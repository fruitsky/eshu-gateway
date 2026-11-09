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

# F10 adds mutating endpoints here (kept separate so this table stays "reads").
SUPERVISOR_ACTIONS = {}

SLUG_RE = re.compile(r'^[a-z0-9_]{1,64}$')

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
    return endpoint, method


def _pick(row, fields):
    if not isinstance(row, dict):
        return row
    return {key: row[key] for key in fields if key in row}


def _shape(shape, data):
    """Whitelist fields. Lists are shaped per row and capped, so one huge
    Supervisor response cannot flood a model's context."""
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
