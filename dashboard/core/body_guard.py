"""Reject duplicate JSON keys on API bodies (security hardening chunk 1f).

Gateways build their JSON bodies by string concatenation. An injected ESHU_*
prefix value can close its string and add a duplicate ``status`` or
``encoded_command``; after parsing, every value looks valid, so the duplicate
key itself is the only signature of the injection. Legitimate gateways and the
UI never send duplicate keys, even nested.
"""
import json

from fastapi import HTTPException, Request
from fastapi.routing import APIRoute

from db.audit import record_audit_event
from db.gateways import get_gateway_by_token

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH"})
_JSON_TYPE = "application/json"


def _audit_ip(request: Request) -> str:
    """Best-effort authenticated gateway IP; never trust unverified payload IPs."""
    token = request.headers.get("X-Gateway-Token", "").strip()
    if token and token != "None":
        gateway = get_gateway_by_token(token)
        if gateway:
            return gateway.get("ip", "")
    return request.client.host if request.client else ""


def _collect_duplicate_keys(pairs, duplicates):
    seen = set()
    for key, _ in pairs:
        if key in seen:
            duplicates.append(key)
        seen.add(key)
    return dict(pairs)


async def reject_duplicate_json_keys(request: Request) -> None:
    """Raise 400 (and audit the key names) when a JSON /api body repeats a key.

    Non-JSON, empty and malformed bodies are left to the endpoint's existing
    validation so their status codes do not change.
    """
    if request.method not in _WRITE_METHODS:
        return
    if not request.url.path.startswith("/api/"):
        return
    content_type = request.headers.get("content-type", "")
    if content_type.split(";", 1)[0].strip().lower() != _JSON_TYPE:
        return

    # Reading here is safe: Starlette caches the body on the Request, so the
    # endpoint (and FastAPI's own body parsing) can read it again.
    body = await request.body()
    if not body.strip():
        return

    duplicates = []
    try:
        json.loads(
            body,
            object_pairs_hook=lambda pairs: _collect_duplicate_keys(
                pairs, duplicates),
        )
    except (ValueError, UnicodeDecodeError):
        return  # FastAPI's normal validation reports malformed JSON (422)

    if not duplicates:
        return

    # Key names only — never the values, which may be credentials.
    keys = sorted(set(duplicates))
    record_audit_event(
        "gateway_body_rejected", _audit_ip(request),
        details="duplicate JSON keys: " + ", ".join(keys),
    )
    raise HTTPException(
        status_code=400,
        detail="Duplicate JSON keys are not allowed: " + ", ".join(keys),
    )


class NoDuplicateJSONKeysRoute(APIRoute):
    """APIRoute that refuses duplicate-key JSON bodies before the handler runs."""

    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            await reject_duplicate_json_keys(request)
            return await original(request)

        return handler
