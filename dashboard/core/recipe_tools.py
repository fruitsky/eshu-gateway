"""F16: the recipe tool allowlist (R2/T3/V3/V6) and its argument rules.

A recipe may call only the tools named here, keyed by the integration's
**kind** and the tool name (V6). A tool in `RECIPE_UNDO` may be called only as
the undo of the tool that names it. Everything else is refused by default; the
`FORBIDDEN_RECIPE_TOOLS` set is the second check for the names that must never
appear even if a bad edit adds them.
"""
from core.recipe_params import RecipeParamError

# (kind, tool) -> spec. `args` is keyed by the tool parameter that carries the
# JSON body (or the flat parameter names). Value specs:
#   ('slot', types)        a {"param": ...} of one of those types
#   ('enum', values)       a literal from values, or a {"param": ...} enum
#   ('fqdn', 'list', n)    a list of up to n fqdn slots/literals
#   ('bool',)              a literal boolean
#   ('access_list',)       W2's fixed access-list object
#   ('result', types)      a {"result": path} slot (undo only)
RECIPE_TOOLS = {
    ('npm', 'create_proxy_host'): {
        'undo': ('npm', 'delete_proxy_host'),
        'body_param': 'body',
        'args': {
            'domain_names': ('fqdn', 'list', 16),
            'forward_scheme': ('enum', ('http', 'https')),
            'forward_host': ('slot', ('ipv4', 'fqdn')),
            'forward_port': ('slot', ('port',)),
            'ssl_forced': ('bool',),
            'block_exploits': ('bool',),
            'caching_enabled': ('bool',),
            'allow_websocket_upgrade': ('bool',),
            'enabled': ('bool',),
            'access_list': ('access_list',),
        },
        'required': ('domain_names', 'forward_scheme', 'forward_host',
                     'forward_port'),
        'forbid': ('advanced_config', 'locations', 'custom_config',
                   'custom_locations', 'access_list_id'),
    },
    ('npm', 'proxy_hosts'): {
        'read': True,
        'args': {
            'search': ('fqdn',),
            'limit': ('int',),
            'full': ('bool',),
        },
    },
    ('npm', 'proxy_host'): {
        'read': True,
        'args': {'id': ('int',)},
    },
    ('pihole', 'local_dns_records'): {
        'read': True,
        'args': {},
    },
}

# A tool that may appear only as an undo, never as a step or a check.
RECIPE_UNDO = {
    ('npm', 'delete_proxy_host'): {
        'args': {'id': ('result', ('int',))},
        'required': ('id',),
    },
}

# Second check: even if one of these is added to the allowlist above, it is
# refused. `recipe_*` is the no-recursion rule.
FORBIDDEN_RECIPE_TOOLS = frozenset({
    'ha_host_reboot', 'ha_core_restart', 'ha_backup_create', 'ha_addon_update',
    'ha_core_update', 'ssh_exec', 'write', 'ws_write', 'web_fetch',
    'call_service', 'enable_blocking', 'disable_blocking', 'update_proxy_host',
    'recipe_run', 'recipe_rollback', 'recipe_propose', 'recipe_list',
    'recipe_get', 'recipe_run_status',
})

# W2: `create_proxy_host` requires the access list explicitly, so "nobody
# said" can never mean "public".
ACCESS_LIST_TOOLS = frozenset({('npm', 'create_proxy_host')})


def _pairs():
    return set(RECIPE_TOOLS) | set(RECIPE_UNDO)


def classify(kind, tool):
    """'call', 'undo' or 'forbidden' for one (kind, tool) pair."""
    key = (str(kind or '').lower(), str(tool or ''))
    if tool in FORBIDDEN_RECIPE_TOOLS:
        return 'forbidden'
    if key in RECIPE_TOOLS:
        return 'call'
    if key in RECIPE_UNDO:
        return 'undo'
    return 'forbidden'


def spec_for(kind, tool):
    key = (str(kind or '').lower(), str(tool or ''))
    return RECIPE_TOOLS.get(key) or RECIPE_UNDO.get(key)


def _slot_ok(spec, value, declared):
    kind = spec[0]
    if kind == 'slot':
        return (isinstance(value, dict) and set(value) == {'param'}
                and isinstance(value['param'], str)
                and value['param'] in declared)
    if kind == 'enum':
        if isinstance(value, str) and value in spec[1]:
            return True
        return isinstance(value, dict) and set(value) == {'param'}
    if kind == 'bool':
        return isinstance(value, bool)
    if kind == 'int':
        if isinstance(value, bool):
            return False
        if isinstance(value, dict):
            return set(value) == {'param'}
        return isinstance(value, int)
    if kind in ('fqdn', 'hostname', 'ipv4', 'slug', 'port'):
        if isinstance(value, dict):
            return set(value) == {'param'}
        return isinstance(value, str)
    if kind == 'result':
        return isinstance(value, dict) and set(value) == {'result'}
    if kind == 'access_list':
        return isinstance(value, dict) and set(value) in ({'public'}, {'id', 'name'})
    return False


def validate_args(spec, args, declared, where='args'):
    """V3: only the listed keys, with the listed shapes."""
    args = args or {}
    if not isinstance(args, dict):
        raise RecipeParamError('invalid_step', '%s must be an object' % where)
    body_param = spec.get('body_param')
    body = args.get(body_param) if body_param else args
    if body_param and not isinstance(body, dict):
        raise RecipeParamError('invalid_step',
                               '%s.%s must be an object' % (where, body_param))
    for key in args:
        if body_param and key != body_param:
            # A flat key alongside `body` is never part of the tool's shape.
            raise RecipeParamError('recipe_arg_forbidden',
                                   '%s.%s is not a recipe argument' % (where, key))
    allowed = spec.get('args') or {}
    for key in body:
        if key in (spec.get('forbid') or ()):
            raise RecipeParamError('recipe_arg_forbidden',
                                   '%s is not allowed in a recipe' % key)
        if key not in allowed:
            raise RecipeParamError('recipe_arg_forbidden',
                                   '%s is not a recipe argument' % key)
        rule = allowed[key]
        value = body[key]
        if rule[0] == 'fqdn' and rule[1] == 'list':
            if not isinstance(value, list) or len(value) > rule[2]:
                raise RecipeParamError('invalid_step',
                                       '%s must be a list of at most %d' % (key, rule[2]))
            for item in value:
                if not (isinstance(item, dict) and set(item) == {'param'}) \
                        and not isinstance(item, str):
                    raise RecipeParamError('invalid_step',
                                           '%s must hold names or slots' % key)
            continue
        if not _slot_ok(rule, value, declared):
            raise RecipeParamError('invalid_step',
                                   '%s has the wrong shape for this tool' % key)
    for key in spec.get('required') or ():
        if key not in body:
            raise RecipeParamError('invalid_step', '%s is required' % key)
    return body
