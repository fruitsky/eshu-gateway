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
