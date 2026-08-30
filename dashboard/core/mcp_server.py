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
    get_integrations,
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

# Per-integration MCP servers: ns -> FastMCP instance / its ASGI app / the set
# of tool names it exposes. Built once at startup so the mount paths are stable;
# `refresh_mcp_tools()` only adds/removes tools on the live instances.
_per_integration = {}
_per_integration_apps = {}
_per_integration_tools = {}

# Integration `mcp_mode` values: where an integration's tools are served.
#   'joined'     -> only on the shared /mcp (namespaced)        [default]
#   'standalone' -> only on its own /mcp/<ns> endpoint (un-namespaced)
#   'both'       -> on both surfaces
MCP_MODES = {'joined', 'standalone', 'both'}


def _on_shared(mcp_mode: str) -> bool:
    return mcp_mode != 'standalone'


def _on_standalone(mcp_mode: str) -> bool:
    return mcp_mode in ('standalone', 'both')


def _safe_ident(name: str) -> str:
    ident = re.sub(r'\W', '_', name)
    if not ident or ident[0].isdigit():
        ident = 'tool_' + ident
    return ident


def _make_instance(name: str) -> FastMCP:
    """A per-integration FastMCP server. Tools are un-namespaced (the endpoint
    already scopes to one integration), and read-only tools run immediately
    while mutating tools need operator approval (poll check_approval)."""
    return FastMCP(
        "Eshu:" + name,
        instructions=(
            f"Eshu gateway for the '{name}' homelab API. Read-only tools run "
            "immediately; mutating tools create a request a human operator must "
            "approve — poll check_approval(id) until it resolves."
        ),
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(_DEFAULT_ALLOWED_HOSTS),
        ),
    )


def _register_tools(inst, integration, namespaced: bool) -> list:
    """Register an integration's enabled tools on `inst`. Returns the list of
    MCP-visible tool names registered (for later removal)."""
    ns = _safe_ident(integration['name']).lower()
    registered = []
    for tool in get_enabled_tools(integration['id']):
        mcp_name = f"{ns}_{tool['name']}" if namespaced else tool['name']
        try:
            fn = _build_tool_fn(integration['name'], tool)
            inst.add_tool(fn, name=mcp_name, description=tool['description'])
            registered.append(mcp_name)
        except Exception as e:
            print(f"[mcp] failed to register tool {mcp_name}: {e}", flush=True)
    return registered


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
    # Phase 2: session_id / execution_id are structured, optional MCP fields that
    # group this call with related SSH commands in the dashboard. They are popped
    # in run_tool and never forwarded to the upstream API.
    effective_params.append({'name': 'session_id', 'type': 'string',
                             'description': 'Optional conversation/session id to group this call with related SSH commands.', 'required': False})
    effective_params.append({'name': 'execution_id', 'type': 'string',
                             'description': 'Optional per-run execution id (e.g. which subagent ran this).', 'required': False})
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

    # Required params must precede optional ones in a Python `def` signature
    # (a required param after a defaulted one is a SyntaxError). Reorder by
    # that rule — safe because callers pass by keyword and FastMCP's input
    # schema is keyed by name, so ordering never affects invocation.
    required = [s for s in sig_parts if '=' not in s]
    optional = [s for s in sig_parts if '=' in s]
    sig_parts = required + optional

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

    for integration in get_integrations():
        if not integration.get('enabled'):
            continue
        ns = _safe_ident(integration['name']).lower()
        mcp_mode = integration.get('mcp_mode') or 'joined'
        # Global /mcp surface keeps the namespaced names (backward compat).
        # `standalone` integrations are excluded from the shared surface — they
        # live only on their own /mcp/<ns> endpoint.
        if _on_shared(mcp_mode):
            for mcp_name in _register_tools(mcp, integration, namespaced=True):
                _registered_names.add(mcp_name)
        # Per-integration instances only get in-place tool updates here — the
        # instance/app/mount themselves are created once at startup.
        inst = _per_integration.get(ns)
        if inst is not None:
            _refresh_single_instance(ns, integration)


def build_per_integration_mcp() -> dict:
    """Create a per-integration FastMCP instance + ASGI app for every enabled
    integration. Called once at startup; returns {ns: app} so main.py can mount
    each at /mcp/<ns> before the catch-all /mcp mount. The mounts are static —
    later tool changes update the live instances in place via refresh_mcp_tools."""
    global _per_integration, _per_integration_apps, _per_integration_tools
    _per_integration = {}
    _per_integration_apps = {}
    _per_integration_tools = {}
    for integration in get_integrations():
        if not integration.get('enabled'):
            continue
        mcp_mode = integration.get('mcp_mode') or 'joined'
        if not _on_standalone(mcp_mode):
            continue
        ns = _safe_ident(integration['name']).lower()
        try:
            inst = _make_instance(integration['name'])
            _per_integration_tools[ns] = set(_register_tools(inst, integration, namespaced=False))
            # approval polling is cross-cutting — present on every per-integration server
            inst.add_tool(check_approval_fn(), name="check_approval",
                          description=CHECK_APPROVAL_DESC)
            _per_integration[ns] = inst
            _per_integration_apps[ns] = inst.streamable_http_app()
        except Exception as e:
            print(f"[mcp] failed to build per-integration server for {ns}: {e}", flush=True)
    return dict(_per_integration_apps)


def _refresh_single_instance(ns: str, integration: dict):
    """Re-register one per-integration instance's tools in place (no instance
    recreation, so the mounted app and its session stay valid)."""
    inst = _per_integration.get(ns)
    if inst is None:
        return
    old = _per_integration_tools.get(ns, set())
    for name in old:
        try:
            inst.remove_tool(name)
        except Exception:
            pass
    fresh = set(_register_tools(inst, integration, namespaced=False))
    inst.add_tool(check_approval_fn(), name="check_approval", description=CHECK_APPROVAL_DESC)
    fresh.add("check_approval")
    _per_integration_tools[ns] = fresh


def session_managers():
    """All FastMCP session managers (global + per-integration) whose lifespan
    must be driven explicitly, because a mounted sub-app's lifespan is not
    propagated by Starlette. Each instance's app is built at startup, so its
    session manager is already created."""
    managers = [mcp.session_manager]
    for inst in _per_integration.values():
        managers.append(inst.session_manager)
    return managers


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
    hosts = _expand_hosts(get_mcp_allowed_hosts())
    mcp.settings.transport_security.allowed_hosts = hosts
    for inst in _per_integration.values():
        inst.settings.transport_security.allowed_hosts = hosts


CHECK_APPROVAL_DESC = (
    "Poll the status of a pending (mutating) integration call. Returns the "
    "call's result once approved, a denial message if denied, or a pending "
    "note. Pass the id returned by a mutating tool when it created the request."
)


def check_approval_fn():
    """The approval-polling tool, registered on the global server and on every
    per-integration server (approval is cross-cutting)."""
    def _check_approval(call_id: int) -> str:
        call = get_pending_call(call_id)
        if not call:
            return '{"error": "not found", "id": %d}' % call_id
        if call['status'] == 'approved':
            return call.get('result') or ''
        if call['status'] == 'denied':
            return '{"status": "denied", "message": "Operator denied the request"}'
        return '{"status": "pending", "message": "Still awaiting operator approval"}'
    return _check_approval


mcp.add_tool(check_approval_fn(), name="check_approval", description=CHECK_APPROVAL_DESC)
