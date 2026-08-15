"""Eshu MCP server — exposes enabled integration tools to MCP clients (Hermes).

Tools are registered dynamically from the `integration_tools` table so the MCP
surface stays in sync with the operator's catalog. Read-only tools forward
immediately; mutating tools create a pending call and require operator approval.
"""
import re

from mcp.server.fastmcp import FastMCP

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
}

AGENT_LABEL = 'mcp'

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
    params = tool.get('params') or []
    sig_parts = []
    param_names = []
    for p in params:
        name = _safe_ident(p['name'])
        ptype = _TYPE_MAP.get(p.get('type', 'string'), 'str')
        if p.get('required'):
            sig_parts.append(f"{name}: {ptype}")
        else:
            sig_parts.append(f"{name}: {ptype} = None")
        param_names.append(name)

    mutating = not tool.get('read_only')
    if mutating:
        sig_parts.append("reason: str")
    sig = ', '.join(sig_parts)

    args_literal = repr({n: n for n in param_names})
    params_literal = repr(params)

    src = [f"def {fn_name}({sig}):"]
    src.append("    import json as _json")
    src.append("    from db.integrations import get_integration as _gi, create_pending_call as _cpc")
    src.append("    from core.integration_proxy import execute_integration_call as _exec, ProxyError as _PE")
    src.append(f"    _args = {args_literal}")
    src.append(f"    _integration = _gi({integration_name!r})")
    if mutating:
        src.append(f"    _call_id = _cpc(_integration['name'], {tool['name']!r}, _args, reason)")
        src.append("    from core.notify import send_notify as _sn")
        notify_line = ("    _sn('jit', 'API Approval Required', "
                       "'`%s` on %s: ' + (reason[:80]))" % (tool['name'], integration_name))
        src.append(notify_line)
        src.append("    return _json.dumps({'status': 'pending', 'id': _call_id, "
                    "'message': 'Awaiting operator approval. Call check_approval(" + str(tool['id']) + ") to poll.'})")
    else:
        src.append(f"    _tool = {{'name': {tool['name']!r}, 'enabled': True, 'method': {tool['method']!r}, "
                   f"'path_template': {tool['path_template']!r}, 'params': {params_literal}}}")
        src.append("    try:")
        src.append(f"        _res = _exec(_integration, _tool, _args, agent={AGENT_LABEL!r})")
        src.append("    except _PE as e:")
        src.append("        return _json.dumps({'error': e.message, 'status_code': e.status_code})")
        src.append("    if _res.get('error'):")
        src.append("        return _json.dumps({'error': _res['error'], 'status_code': _res.get('status_code')})")
        src.append("    return _res['body']")

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
        try:
            fn = _build_tool_fn(integration['name'], tool)
            mcp.add_tool(fn, name=tool['name'], description=tool['description'])
            _registered_names.add(tool['name'])
        except Exception as e:
            print(f"[mcp] failed to register tool {tool['name']}: {e}", flush=True)


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
