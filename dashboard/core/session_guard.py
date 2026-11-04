"""Session enforcement policy, shared by MCP and the gateway policy response.

`require_sessions` is a feature flag with the usual Off/Dev/Prod scope:
  * Off  — no enforcement (the historical behaviour).
  * Dev  — MCP is strict everywhere, the gateway gate is strict only on
           gateways whose mode is `dev`.
  * Prod — strict on every MCP call and every gateway.

The MCP side can be exact because the dashboard owns the `sessions` table. The
gateway side can only verify the signed token (it has no DB access), which is
why `session_begin` signs the id.
"""
from db.misc import get_feature_flags
from db.sessions import get_session


def require_sessions_enabled() -> bool:
    flag = get_feature_flags().get('require_sessions') or {}
    return bool(flag.get('enabled'))


def require_sessions_for_gateway(gateway_mode: str) -> bool:
    flag = get_feature_flags().get('require_sessions') or {}
    if not flag.get('enabled'):
        return False
    scope = (flag.get('scope') or 'dev').lower()
    return scope == 'prod' or (gateway_mode or '').lower() == 'dev'


def session_problem(session_id: str) -> str:
    """Return an actionable message when the id is not a minted, named session."""
    sid = (session_id or '').strip()[:64]
    if not sid:
        return ('No Session id. Call session_begin(goal="<task name>", '
                'session_id="<your chat id>") first, then pass its session_id.')
    row = get_session(sid)
    if not row or not (row.get('goal') or '').strip():
        return ('Session "%s" is not minted with a name. Call '
                'session_begin(goal="<task name>", session_id="%s") first.'
                % (sid, sid))
    return ''
