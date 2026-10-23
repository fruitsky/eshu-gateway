"""Server-side policy evaluation for per-agent MCP calls.

Two dimensions are enforced here:

* **Tool visibility/eligibility** — deny-by-default for agents configured with
  an allowlist. An unknown or unlisted tool is refused before any upstream
  request is attempted.
* **Argument constraints** — the interesting decision for Home Assistant lives
  in the `domain`/`service`/`data` payload of an otherwise-allowed tool. Deny
  rules always win over allow rules.

`run_tool` is the single chokepoint: every generated MCP tool (shared or
per-integration) funnels through it, so enforcement cannot be bypassed by
calling a tool that was merely hidden from `tools/list`.
"""
import fnmatch
import re

_SAFE_IDENT = re.compile(r'\W')


def safe_ident(name: str) -> str:
    ident = _SAFE_IDENT.sub('_', name or '')
    if not ident or ident[0].isdigit():
        ident = 'tool_' + ident
    return ident


def tool_key(integration_name: str, tool_name: str) -> str:
    """The canonical policy key, e.g. ``home_assistant_call_service``.

    The same key is used whether the tool was reached through the shared /mcp
    endpoint (namespaced) or an integration-specific /mcp/<ns> endpoint
    (un-namespaced), so one policy applies to both.
    """
    return f"{safe_ident(integration_name).lower()}_{tool_name}"


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value]
    return [str(value)]


def _matches_any(value, patterns):
    value = str(value or '')
    return any(fnmatch.fnmatchcase(value, str(p)) for p in _as_list(patterns))


def _lookup(mapping, namespaced, integration_name):
    """Policy lookup order: exact tool, integration wildcard, global wildcard."""
    if not isinstance(mapping, dict):
        return None
    if namespaced in mapping:
        return mapping[namespaced]
    ns = safe_ident(integration_name).lower()
    for key in (ns + '_*', ns + '.*', '*'):
        if key in mapping:
            return mapping[key]
    return None


def visible_tool_allowed(agent, effective_name):
    """Allowlist check for an already-namespaced MCP-visible tool name."""
    policy = (agent or {}).get('policy') or {}
    if (policy.get('tools_mode') or 'all') == 'all':
        return True
    return _matches_any(effective_name, policy.get('tools') or [])


def tool_allowed(agent, integration_name, tool_name):
    """Return (allowed, reason) for a tool under this agent's policy."""
    policy = (agent or {}).get('policy') or {}
    mode = policy.get('tools_mode') or 'all'
    if mode == 'all':
        return True, ''
    key = tool_key(integration_name, tool_name)
    patterns = policy.get('tools') or []
    if _matches_any(key, patterns):
        return True, ''
    return False, f"tool '{key}' is not in this agent's allowlist"


def _entity_ids(args):
    """Collect entity ids from the common HA service payload shapes."""
    out = []
    data = args.get('data') if isinstance(args, dict) else None
    for container in (data, args if isinstance(args, dict) else None):
        if not isinstance(container, dict):
            continue
        for key in ('entity_id', 'entity_ids'):
            if key in container:
                out.extend(_as_list(container.get(key)))
        target = container.get('target')
        if isinstance(target, dict):
            for key in ('entity_id', 'entity_ids'):
                if key in target:
                    out.extend(_as_list(target.get(key)))
    return [e for e in out if e]


def _path_variants(path):
    path = str(path or '')
    return {path, path.lstrip('/'), '/' + path.lstrip('/')}


def _prefix_match(path, prefixes):
    variants = _path_variants(path)
    return any(any(v.startswith(p) for v in variants) for p in _as_list(prefixes))


def _evaluate_ha_call_service(rules, args):
    domain = str(args.get('domain') or '').strip().lower()
    service = str(args.get('service') or '').strip().lower()
    if not domain or not service:
        return False, 'domain and service are required'

    domain_deny = [d.lower() for d in _as_list(rules.get('domain_deny'))]
    domain_allow = [d.lower() for d in _as_list(rules.get('domain_allow'))]
    service_deny = [s.lower() for s in _as_list(rules.get('service_deny'))]
    service_allow = [s.lower() for s in _as_list(rules.get('service_allow'))]

    # Deny rules beat allow rules, including when a domain appears in both.
    if domain in domain_deny:
        return False, f"domain '{domain}' is denied by agent policy"
    if service in service_deny:
        return False, f"service '{service}' is denied by agent policy"
    if domain_allow and domain not in domain_allow:
        return False, f"domain '{domain}' is not in this agent's allowed domains"
    if service_allow and service not in service_allow:
        return False, f"service '{service}' is not in this agent's allowed services"

    entities = _entity_ids(args)
    entity_deny = _as_list(rules.get('entity_deny'))
    entity_allow = _as_list(rules.get('entity_allow'))
    if entity_deny and any(_matches_any(e, entity_deny) for e in entities):
        return False, 'target entity is denied by agent policy'
    if entity_allow:
        if not entities:
            return False, 'entity_allow is configured but the call has no entity_id'
        if not all(_matches_any(e, entity_allow) for e in entities):
            return False, 'target entity is not in this agent\'s allowed entities'
    return True, ''


def _evaluate_http(rules, args, tool=None):
    tool = tool or {}
    method = str(args.get('method') or tool.get('method') or 'GET').upper()
    path = str(args.get('path') or tool.get('path_template') or '')
    method_allow = [m.upper() for m in _as_list(rules.get('method_allow'))]
    if method_allow and method not in method_allow:
        return False, f"method '{method}' is not allowed by agent policy"
    if args.get('root') and not rules.get('root_allow'):
        return False, 'root-relative paths are not allowed by agent policy'
    path_deny = rules.get('path_deny_prefixes')
    if path_deny and _prefix_match(path, path_deny):
        return False, f"path '{path}' is denied by agent policy"
    path_allow = rules.get('path_allow_prefixes')
    if path_allow and not _prefix_match(path, path_allow):
        return False, f"path '{path}' is not in this agent's allowed paths"
    return True, ''


def _evaluate_ws(rules, args, tool=None):
    tool = tool or {}
    command = str(args.get('command') or tool.get('path_template') or '')
    command_deny = rules.get('command_deny_prefixes')
    if command_deny and _prefix_match(command, command_deny):
        return False, f"WebSocket command '{command}' is denied by agent policy"
    command_exact = rules.get('command_deny')
    if command_exact and command in _as_list(command_exact):
        return False, f"WebSocket command '{command}' is denied by agent policy"
    command_allow = rules.get('command_allow')
    if command_allow and command not in _as_list(command_allow):
        return False, f"WebSocket command '{command}' is not allowed by agent policy"
    command_prefix = rules.get('command_allow_prefixes')
    if command_prefix and not _prefix_match(command, command_prefix):
        return False, f"WebSocket command '{command}' is not in this agent's allowed commands"
    return True, ''


def evaluate_call(agent, integration_name, tool_name, args, tool=None,
                  integration=None):
    """Return (allowed, reason) for a full call: tool then argument rules."""
    allowed, reason = tool_allowed(agent, integration_name, tool_name)
    if not allowed:
        return allowed, reason

    policy = (agent or {}).get('policy') or {}
    key = tool_key(integration_name, tool_name)
    rules = _lookup(policy.get('arg_policy'), key, integration_name)
    if not rules:
        return True, ''

    args = args or {}
    kind = ((integration or {}).get('kind') or '').lower()
    transport = ((tool or {}).get('transport') or 'http').lower()
    is_generic = bool((tool or {}).get('generic'))

    if kind == 'ha' and tool_name == 'call_service':
        return _evaluate_ha_call_service(rules, args)
    if transport == 'ws':
        return _evaluate_ws(rules, args, tool)
    if is_generic or 'method' in args or 'path' in args:
        return _evaluate_http(rules, args, tool)
    return True, ''


def approval_mode(agent, integration_name, tool_name):
    """Return 'inherit' | 'gated' | 'ungated'. Unknown values fail closed."""
    policy = (agent or {}).get('policy') or {}
    key = tool_key(integration_name, tool_name)
    mode = _lookup(policy.get('approval'), key, integration_name)
    if mode in ('inherit', 'gated', 'ungated'):
        return mode
    return 'gated' if mode is not None else 'inherit'


def rate_limit_for(agent):
    policy = (agent or {}).get('policy') or {}
    try:
        return max(0, int(policy.get('rate_limit_per_min') or 0))
    except (TypeError, ValueError):
        return 0
