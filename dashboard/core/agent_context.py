"""Request-scoped caller identity for MCP tool execution.

The HTTP middleware authenticates the bearer token and stores the agent on the
request; the FastMCP wrapper then binds that agent to a ContextVar for the
duration of the tool call, so every layer (run_tool, handlers, proxy, audit)
can attribute the call without threading an extra parameter through each
generated function signature.
"""
import contextvars

_current_agent = contextvars.ContextVar('eshu_current_agent', default=None)


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
