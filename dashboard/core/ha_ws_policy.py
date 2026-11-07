"""Read-command policy for the generic Home Assistant WebSocket tool.

`ws_read` is a read-only tool: it auto-runs without operator approval. The HA
WebSocket API is not read/write separated by transport, though — `call_service`,
the registry `create`/`update`/`remove` commands, `supervisor/*` and `backup/*`
all travel over the same socket. This module decides, from the command name
alone, whether a command is a read.

Two rules keep the decision safe:

* the deny rules are checked **first** and win over the allow patterns, so
  `config/auth/list` is refused even though it ends in `list`;
* an unknown command is **not** a read (fail closed). It can still be run
  through the approval-gated `ws_write` tool.

Every name added to `READ_COMMANDS` / `READ_LAST_SEGMENTS` is a security
decision: it lets that command run with no operator in the loop.
"""

# Reviewed commands whose name does not follow the read patterns below.
READ_COMMANDS = frozenset({
    'render_template',
    'get_states',
    'get_config',
    'get_services',
    # `zha/devices` lists Zigbee devices — a read with no read-style last
    # segment, so it is named explicitly (live agent traffic uses it).
    'zha/devices',
})

# Last path segment of a read command: `config/entity_registry/list`,
# `persistent_notification/get`, `recorder/statistics_during_period`, the
# `.config` read endpoints (`automation/config`), and "get one" commands.
READ_LAST_SEGMENTS = frozenset({
    'list',
    'get',
    'get_single',
    'info',
    'browse_media',
    'statistics_during_period',
    'config',
})

# Mutating commands and namespaces. Checked before the allow patterns.
DENY_COMMANDS = frozenset({'call_service', 'execute_script'})
DENY_PREFIXES = ('supervisor/', 'hassio/', 'auth/', 'backup/', 'repairs/',
                 'config/auth')
DENY_LAST_SEGMENTS = frozenset({
    'create', 'update', 'delete', 'remove', 'reload', 'restart', 'migrate',
    'install', 'uninstall', 'write',
})
DENY_LAST_PREFIXES = ('set', 'subscribe_', 'unsubscribe_')

MAX_COMMAND_LENGTH = 200


def is_read_command(command) -> bool:
    """True only for a reviewed Home Assistant read command."""
    if not isinstance(command, str):
        return False
    command = command.strip()
    if not command or len(command) > MAX_COMMAND_LENGTH:
        return False
    if any(ch in command for ch in ('\x00', '\\')):
        return False
    if command in DENY_COMMANDS:
        return False
    for prefix in DENY_PREFIXES:
        if command.startswith(prefix):
            return False
    last = command.rsplit('/', 1)[-1]
    if last in DENY_LAST_SEGMENTS:
        return False
    if last.startswith(DENY_LAST_PREFIXES):
        return False
    if command in READ_COMMANDS:
        return True
    return last in READ_LAST_SEGMENTS
