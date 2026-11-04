"""Request-scoped caller identity and session for MCP tool execution.

The HTTP middleware authenticates the bearer token and stores the agent on the
request; the FastMCP wrapper then binds that agent to a ContextVar for the
duration of the tool call, so every layer (run_tool, handlers, proxy, audit)
can attribute the call without threading an extra parameter through each
generated function signature.

The same wrapper binds the MCP transport session, so a tool call without an
explicit `session_id` still lands in a real session instead of a blank one.
"""
import contextvars

_current_agent = contextvars.ContextVar('eshu_current_agent', default=None)
# The session a tool call belongs to when the caller did not pass one: the MCP
# transport session, bound by EshuFastMCP.call_tool. Never persisted here.
_current_session = contextvars.ContextVar('eshu_current_session', default='')


def set_current_agent(agent):
    return _current_agent.set(agent)


def reset_current_agent(token):
    try:
        _current_agent.reset(token)
    except (LookupError, ValueError):
        pass


def get_current_agent():
    return _current_agent.get()


def current_agent_name(default=''):
    agent = get_current_agent()
    if not agent:
        return default
    return agent.get('name') or default


def set_current_session(session_id):
    return _current_session.set(session_id or '')


def reset_current_session(token):
    try:
        _current_session.reset(token)
    except (LookupError, ValueError):
        pass


def current_session_id(default=''):
    return _current_session.get() or default


# Where the session id on an audited row came from: 'caller' (an explicit
# session_id argument), 'inherited' (the id session_begin bound to this
# connection) or 'transport' (the mcp-… fallback). Coverage counts only
# 'caller', so an inherited id never looks like the agent complied.
_current_session_source = contextvars.ContextVar(
    'eshu_current_session_source', default='')


def set_current_session_source(source):
    return _current_session_source.set(source or '')


def reset_current_session_source(token):
    try:
        _current_session_source.reset(token)
    except (LookupError, ValueError):
        pass


def current_session_source(default=''):
    return _current_session_source.get() or default


# The last caller-supplied session id seen on each MCP transport connection.
# `session_summary()` with no argument should describe the work the agent just
# did, not the empty transport bucket. Bounded: a dead connection's entry is
# dropped once the map is large.
_last_session_by_transport = {}
_LAST_SESSION_CAP = 512


def remember_session_for_transport(session_id, transport=None):
    transport = transport or _current_session.get()
    sid = (session_id or '')[:64]
    if not transport or not sid or sid == transport:
        return
    _last_session_by_transport[transport] = sid
    if len(_last_session_by_transport) > _LAST_SESSION_CAP:
        _last_session_by_transport.pop(next(iter(_last_session_by_transport)), None)


def last_session_for_transport(transport=None):
    transport = transport or _current_session.get()
    return _last_session_by_transport.get(transport, '') if transport else ''


def reset_session_memory():
    """Forget every connection's remembered session (tests, fresh start)."""
    _last_session_by_transport.clear()
