"""Eshu MCP server — exposes enabled integration tools to MCP clients (Hermes).

Tools are registered dynamically from the `integration_tools` table so the MCP
surface stays in sync with the operator's catalog. Read-only tools forward
immediately; mutating tools create a pending call and require operator approval.
"""
import re

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from db.integrations import (
    get_enabled_tools,
    get_integration_by_id,
    get_pending_call,
)

_TYPE_MAP = {
    'string': 'str',
    'integer': 'int',
    'number': 'float',
    'boolean': 'bool',
    'json': 'dict',
}

AGENT_LABEL = 'mcp'

# DNS-rebinding protection is auto-enabled by FastMCP with a loopback-only
# allowlist when host defaults to 127.0.0.1. We keep it on (defense in depth)
# but expand the allowlist with the operator-configured hosts so the dashboard
# is reachable at its real hostname(s)/IP(s) through a reverse proxy.
_DEFAULT_ALLOWED_HOSTS = ['127.0.0.1:*', 'localhost:*', '[::1]:*']

mcp = FastMCP(
    "Eshu",
    instructions=(
        "Eshu gateway for your homelab APIs. Read-only tools run immediately; "
        "mutating tools (start/stop/reboot/etc.) create a request that a human "
        "operator must approve — poll check_approval(id) until it resolves."
    ),
    # Mounted into the dashboard app at /mcp; use "/" so the effective endpoint
    # is http://<dashboard>:8000/mcp rather than /mcp/mcp.
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(_DEFAULT_ALLOWED_HOSTS),
    ),
)

_registered_names = set()


def _safe_ident(name: str) -> str:
    ident = re.sub(r'\W', '_', name)
    if not ident or ident[0].isdigit():
        ident = 'tool_' + ident
    return ident


def _build_tool_fn(integration_name: str, tool: dict):
    """Generate a function with a typed signature matching the tool's params,
    so FastMCP exposes an accurate input schema to the model."""
    fn_name = _safe_ident(tool['name']) + '_' + str(tool['id'])
    catalog_params = list(tool.get('params') or [])
    fields = tool.get('fields') or []
    search_field = tool.get('search_field') or ''
    filter_fields = tool.get('filter_fields') or []
    mutating = not tool.get('read_only')

    # Effective param list: catalog params + synthetic params that shape the
    # response client-side (`full`, `search`, `limit`, and one exact-match
    # param per `filter_fields` entry). They're kept out of the forwarded
    # params below so they never reach the upstream API.
    effective_params = list(catalog_params)
    if fields and not mutating:
        effective_params.append({'name': 'full', 'type': 'boolean',
                                 'description': 'Return the full, unprojected object.', 'required': False})
    if search_field and not mutating:
        effective_params.append({'name': 'search', 'type': 'string',
                                 'description': f'Substring filter on {search_field}.', 'required': False})
        effective_params.append({'name': 'limit', 'type': 'integer',
                                 'description': 'Max results (default 50).', 'required': False, 'default': 50})
    if filter_fields and not mutating:
        for f in filter_fields:
            effective_params.append({'name': f, 'type': 'string',
                                     'description': f'Exact match on the {f} field.', 'required': False})

    sig_parts = []
    arg_entries = []
    for p in effective_params:
        name = _safe_ident(p['name'])
        ptype = _TYPE_MAP.get(p.get('type', 'string'), 'str')
        if p.get('required'):
            sig_parts.append(f"{name}: {ptype}")
        elif p.get('default') is not None:
            sig_parts.append(f"{name}: {ptype} = {p['default']!r}")
        else:
            sig_parts.append(f"{name}: {ptype} = None")
        arg_entries.append((p['name'], name))

    if mutating:
        # keyword-only so a required `reason` can follow optional params
        # (e.g. call_service's optional `data`) without a Python SyntaxError.
        sig_parts.append("*, reason: str")
    sig = ', '.join(sig_parts)

    # key = original param name (matches the args dict run_tool expects),
    # value = the function parameter variable carrying the actual argument.
    # e.g. {'node': node, 'vmid': vmid} — NOT the param-name string.
    args_literal = '{' + ', '.join(f"{orig!r}: {var}" for orig, var in arg_entries) + '}'

    src = [f"def {fn_name}({sig}):"]
    src.append("    from core.tool_runner import run_tool as _rt")
    if mutating:
        src.append(f"    return _rt({integration_name!r}, {tool['name']!r}, {args_literal}, reason)")
    else:
        src.append(f"    return _rt({integration_name!r}, {tool['name']!r}, {args_literal})")

    ns = {}
    exec('\n'.join(src), ns)
    return ns[fn_name]


def refresh_mcp_tools():
    """Re-register the enabled tools from the DB. Called at startup and after
    any catalog change so the MCP surface stays in sync without a restart."""
    global _registered_names
    for name in list(_registered_names):
        try:
            mcp.remove_tool(name)
        except Exception:
            pass
    _registered_names = set()

    for tool in get_enabled_tools():
        integration = get_integration_by_id(tool['integration_id'])
        if not integration or not integration.get('enabled'):
            continue
        # Namespace the MCP-visible tool name by the integration's name (a
        # clean lowercase slug), so tools from different integrations can't
        # collide even when several run the same software (e.g. pihole2_summary
        # vs pihole3_summary) and ownership is obvious: proxmox_list_nodes, ...
        ns = _safe_ident(integration['name']).lower()
        mcp_name = f"{ns}_{tool['name']}"
        try:
            fn = _build_tool_fn(integration['name'], tool)
            mcp.add_tool(fn, name=mcp_name, description=tool['description'])
            _registered_names.add(mcp_name)
        except Exception as e:
            print(f"[mcp] failed to register tool {mcp_name}: {e}", flush=True)


def _expand_hosts(hosts: str) -> list:
    """Expand a comma-separated host list into exact + port-wildcard entries.

    The MCP DNS-rebinding check matches a bare host (e.g. a proxy Host header
    without a port) by exact equality and a `host:*` entry by `host:` prefix,
    so both forms are needed to cover access with and without a port."""
    out = list(_DEFAULT_ALLOWED_HOSTS)
    for h in (hosts or '').split(','):
        h = h.strip()
        if not h:
            continue
        out.append(h)
        out.append(h + ':*')
    seen = set()
    deduped = []
    for h in out:
        if h not in seen:
            seen.add(h)
            deduped.append(h)
    return deduped


def refresh_mcp_allowed_hosts():
    """Apply the configured allowed hosts to the MCP transport's DNS-rebinding
    allowlist. The transport middleware holds a reference to this same settings
    object, so mutating it takes effect on live requests — no restart needed."""
    from db.misc import get_mcp_allowed_hosts
    mcp.settings.transport_security.allowed_hosts = _expand_hosts(get_mcp_allowed_hosts())


@mcp.tool()
def check_approval(call_id: int) -> str:
    """Poll the status of a pending (mutating) integration call. Returns the
    call's result once approved, a denial message if denied, or a pending note.
    Pass the id returned by a mutating tool when it created the request."""
    call = get_pending_call(call_id)
    if not call:
        return '{"error": "not found", "id": %d}' % call_id
    if call['status'] == 'approved':
        return call.get('result') or ''
    if call['status'] == 'denied':
        return '{"status": "denied", "message": "Operator denied the request"}'
    return '{"status": "pending", "message": "Still awaiting operator approval"}'
