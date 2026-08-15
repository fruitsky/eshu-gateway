"""Agent-token auth for the integration/MCP surface.

Agents (e.g. Hermes) authenticate to /mcp with a bearer agent token minted from
the Integrations UI. Mirrors the gateway-token pattern, but is a distinct,
revocable primitive scoped to integration access.
"""
from fastapi import HTTPException, Request

from db.agent_tokens import get_agent_by_token, touch_agent_token


def extract_agent_token(request: Request) -> str:
    auth = request.headers.get('Authorization', '')
    if auth.lower().startswith('bearer '):
        return auth[7:].strip()
    return request.headers.get('X-Agent-Token', '').strip()


def resolve_agent(request: Request):
    """Resolve a bearer token to its agent record, or raise 401."""
    token = extract_agent_token(request)
    agent = get_agent_by_token(token) if token else None
    if not agent or agent.get('revoked'):
        raise HTTPException(status_code=401, detail="Invalid or revoked agent token")
    return agent


def resolve_agent_optional(request: Request):
    """Resolve the agent or return None (used by middleware for early 401)."""
    token = extract_agent_token(request)
    agent = get_agent_by_token(token) if token else None
    if agent and not agent.get('revoked'):
        return agent
    return None
