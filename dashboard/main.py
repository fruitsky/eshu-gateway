import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import urllib.request

from typing import Optional
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import JSONResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from database import (
    init_db, create_request, update_request_status, 
    update_ticket_consumed_by_ip, get_all_requests, get_pending_request_by_cmd,
    get_request_status, get_request_command, count_denied, get_ticket_by_request_id, delete_old_requests,
    register_gateway, get_gateways, update_gateway_last_seen,
    update_gateway_policy_version, update_gateway_policy_sync,
    update_gateway_last_updated, update_gateway_windows_count, update_gateway_heartbeat, deregister_gateway,
    set_trigger_uninstall, check_trigger_uninstall, clear_trigger_uninstall,
    set_uninstall_progress, get_uninstall_progress, clear_uninstall_progress,
    get_policies, update_policy, get_policy_version, increment_policy_version,
    get_policy_updated_at, set_policy_updated_at,
    get_db,
    get_note, update_note, record_policy_change, get_policy_changes, get_policy_change,
    seed_core_blocklist_if_needed,
    get_ssh_keys, save_ssh_keys,
    generate_enrollment_token, validate_enrollment_token,
    get_trigger_update_version, set_trigger_update_version,
    get_deployed_golden_hash, set_deployed_golden_hash,
    get_dev_push_initiated, set_dev_push_initiated, clear_dev_push_initiated,
    dismiss_policy_gap,
    get_mcp_allowed_hosts, set_mcp_allowed_hosts,
    get_trigger_rollback, set_trigger_rollback, clear_trigger_rollback,
    set_trigger_freeze, get_trigger_freeze, clear_trigger_freeze,
    record_audit_event, get_audit_log,
    get_password_hash, set_password_hash,
    search_requests, search_audit_log,
    get_gateway_token, set_gateway_token, get_gateway_by_token,
    get_feature_flags, set_feature_flag, set_feature_flag_scope,
    get_gateway_mode, set_gateway_mode, get_dev_gateways,
    get_gateway_zero_trust, set_gateway_zero_trust,
    set_override, clear_override, get_override_active,
    create_approved_window, get_approved_windows, get_active_approved_windows,
    delete_approved_window, toggle_approved_window,
    increment_window_execution, get_recent_jit_approved,
    get_trigger_dev_update, set_trigger_dev_update, clear_trigger_dev_update,
    update_approved_window,
    create_window_request, get_window_request, get_window_request_by_key,
    get_approved_window_by_id, get_approved_window_by_key,
    approve_window_request, get_pending_window_requests,
    get_notify_config, set_notify_config,
    get_dev_tools_enabled, set_dev_tools_enabled,
    record_window_execution, get_window_executions,
    create_fleet_command, get_fleet_commands, get_fleet_command,
    approve_fleet_command, upsert_fleet_result,
    get_fleet_result, get_fleet_results, get_injectable_fleet_cmd,
    delete_fleet_command,
    create_integration, get_integrations, get_integration, get_integration_by_id,
    update_integration, delete_integration,
    create_tool, get_tools, get_tool, set_tool_enabled, delete_tool,
    record_integration_call, get_integration_calls,
    create_pending_call, get_pending_calls, get_pending_call, set_pending_call_status,
    create_agent_token, get_agent_tokens, revoke_agent_token, delete_agent_token,
)

from schemas import (
    LoginPayload, SetPasswordPayload, GatewayPayload, RegisterPayload,
    PolicyPayload, NotePayload, SSHKeysPayload, HeartbeatPayload,
    UninstallProgressPayload, FeatureFlagTogglePayload, GatewayModePayload,
    ApprovedWindowPayload, WindowUpdatePayload, WindowRequestPayload,
    NotifyConfigPayload,
)

from core.session import (
    SESSION_KEY, SESSION_TTL, _make_session_token, _verify_session_token,
    _check_session, _check_session_optional, _is_password_protected,
)
from core.rate_limit import _check_rate_limit
from core.notify import send_notify
from core.cmd_blocklist import hard_block_match, blocklist_substring_match, CORE_COMMAND_PATTERNS, HARD_PATTERNS
from core.policy_eval import evaluate_policy_verdict
from core.cmd_risk import get_cmd_risk, get_dry_run_suggestion
from core.cmd_profiles import get_anomaly, refresh_profiles, _profiles_loop
from core.gateway_watch import (
    _check_gateway_transitions, _gateway_watch_loop,
    _stale_gateway_cleanup_loop, _fleet_cleanup_loop,
    _disconnected_gateways, _offline_alerted, OFFLINE_THRESHOLD,
)
from core.utils import DASHBOARD_VERSION, decode_cmd, _resolve_gateway_token, _hash_password, _verify_password
from core.integration_auth import resolve_agent, resolve_agent_optional, extract_agent_token
from core.integration_proxy import execute_integration_call
from core.mcp_server import mcp as eshu_mcp, refresh_mcp_tools, refresh_mcp_allowed_hosts
from core.proxmox_seed import seed_proxmox_tools


def _get_gateway_hostname(ip: str) -> str:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT hostname FROM gateways WHERE ip = ?', (ip,))
    row = cursor.fetchone()
    conn.close()
    return row['hostname'] if row else ip

def _file_hash(path: str) -> str:
    """Return first 12 chars of SHA256 hex digest, or None if file missing."""
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:12]


def _is_dev_mode() -> bool:
    head_path = os.path.join(os.path.dirname(
        os.path.abspath(__file__)), '..', '.git', 'HEAD')
    try:
        with open(head_path) as f:
            ref = f.read().strip()
        if ref.startswith('ref: refs/heads/'):
            return ref.split('/')[-1] != 'master'
    except Exception:
        pass
    return False


app = FastAPI(title="Eshu Gateway Dashboard v15.3")

# Track last audit-log timestamp per IP+version to suppress duplicate registrations
# Key: "ip:version", Value: timestamp of last logged enrollment
_last_enroll_log = {}
_ENROLL_DEDUP_WINDOW = 5  # seconds
_app = app  # keep reference for router includes


@app.middleware("http")
async def mcp_agent_auth_middleware(request: Request, call_next):
    """Gate the /mcp surface behind a bearer agent token (mirrors the gateway
    token pattern). Dashboard UI and gateway endpoints are unaffected."""
    if request.url.path.startswith("/mcp"):
        agent = resolve_agent_optional(request)
        if agent is None:
            return JSONResponse(status_code=401, content={"detail": "Invalid or missing agent token"})
    return await call_next(request)


@app.middleware("http")
async def proxy_scheme_middleware(request: Request, call_next):
    """Honor X-Forwarded-Proto so the dashboard generates correct https://
    URLs (e.g. the /mcp trailing-slash redirect) when running behind a
    TLS-terminating reverse proxy like NPM. Without this, a client following
    the redirect sees a scheme change and drops the Authorization header."""
    proto = request.headers.get("X-Forwarded-Proto", "")
    if proto in ("http", "https"):
        request.scope["scheme"] = proto
    return await call_next(request)

@app.on_event("startup")
def on_startup():
    # Migrate old Hermes database if present (MUST run before init_db, which creates eshu.db)
    _migrate_legacy_db()
    init_db()
    # One-time seed: ship the command-safety core patterns into the blocklist
    # (idempotent; existing installs gain the same defaults as fresh ones).
    seed_core_blocklist_if_needed(CORE_COMMAND_PATTERNS)
    from core.cmd_descs import load_whatis_db
    load_whatis_db()
    pw = get_password_hash()
    if not pw:
        print("\n" + "=" * 60)
        print("  🔐  No dashboard password is set.")
        print("     Complete setup in the dashboard on first launch, or run:")
        print("     python3 dashboard/set_password.py")
        print("=" * 60 + "\n")
    else:
        print(f"Eshu Gateway Dashboard {DASHBOARD_VERSION} started (password-protected)")
    # Start background stale gateway cleanup
    cleanup_thread = threading.Thread(target=_stale_gateway_cleanup_loop, daemon=True)
    cleanup_thread.start()
    # Start background fleet results retention cleanup
    fleet_cleanup_thread = threading.Thread(target=_fleet_cleanup_loop, daemon=True)
    fleet_cleanup_thread.start()
    # Initialize deployed golden hash on first run
    golden_path = os.path.join(static_dir, "eshu-gateway-install.sh")
    if os.path.exists(golden_path) and not get_deployed_golden_hash():
        gh = _file_hash(golden_path)
        if gh:
            set_deployed_golden_hash(gh)
    # Start background gateway transition watcher (offline detection)
    watch_thread = threading.Thread(target=_gateway_watch_loop, daemon=True)
    watch_thread.start()
    # Start background gap scanner (learning suggestions)
    from core.learning import refresh_gaps, _gaps_loop
    refresh_gaps()
    gaps_thread = threading.Thread(target=_gaps_loop, daemon=True)
    gaps_thread.start()
    # Start background command-profile scanner (behavioural anomaly flags)
    refresh_profiles()
    profiles_thread = threading.Thread(target=_profiles_loop, daemon=True)
    profiles_thread.start()
    # Register the enabled integration tools as MCP tools
    refresh_mcp_tools()
    # Apply the configured MCP allowed-hosts (DNS-rebinding allowlist)
    refresh_mcp_allowed_hosts()


_mcp_lifespan_ctx = None


@app.on_event("startup")
async def mcp_startup():
    """Enter the MCP streamable-HTTP session manager's lifespan. Starlette does
    not propagate a mounted sub-app's lifespan, so we drive it explicitly."""
    global _mcp_lifespan_ctx
    _mcp_lifespan_ctx = eshu_mcp.session_manager.run()
    await _mcp_lifespan_ctx.__aenter__()


@app.on_event("shutdown")
async def mcp_shutdown():
    global _mcp_lifespan_ctx
    if _mcp_lifespan_ctx is not None:
        await _mcp_lifespan_ctx.__aexit__(None, None, None)
        _mcp_lifespan_ctx = None


def _migrate_legacy_db():
    """One-time migration from Hermes to Eshu database."""
    import shutil
    db_dir = os.path.dirname(os.path.abspath(__file__))
    new_db = os.path.join(db_dir, "eshu.db")
    old_db = os.path.join(db_dir, "hermes_jit.db")
    
    # If new DB already exists, nothing to do
    if os.path.exists(new_db):
        return
    
    # If old DB exists, copy it
    if os.path.exists(old_db):
        print("  🔄  Migrating legacy hermes_jit.db → eshu.db ...")
        shutil.copy2(old_db, new_db)
        
        # Rename meta keys internally
        import sqlite3
        conn = sqlite3.connect(new_db)
        cursor = conn.cursor()
        cursor.execute("UPDATE meta SET key = 'eshu_ssh_key' WHERE key = 'hermes_ssh_key'")
        conn.commit()
        conn.close()
        print("  ✅  Database migration complete.")

# ── Auth API ────────────────────────────────────────────────────────────

@app.get("/api/auth/status")
def auth_status(request: Request):
    """Returns whether password is set and if the current request is authenticated."""
    pw_set = _is_password_protected()
    authed = _check_session_optional(request)
    return {
        "password_set": pw_set,
        "authenticated": authed,
    }


@app.post("/api/auth/login")
def auth_login(payload: LoginPayload, response: Response, request: Request):
    """Authenticate with the dashboard password. Sets session cookie on success."""
    if not _is_password_protected():
        return {"status": "ok", "message": "No password configured — access granted"}
    password = payload.password.strip()
    if not password:
        raise HTTPException(status_code=401, detail="Password required")
    stored = get_password_hash()
    if not _verify_password(password, stored):
        raise HTTPException(status_code=401, detail="Invalid password")
    token = _make_session_token()
    # Only set secure flag if behind HTTPS (works for plain HTTP homelabs too)
    is_https = request.headers.get('X-Forwarded-Proto', request.url.scheme) == 'https'
    response.set_cookie(
        key='eshu_session',
        value=token,
        max_age=SESSION_TTL,
        httponly=True,
        samesite='lax',
        secure=is_https,
        path='/',
    )
    return {"status": "ok"}


@app.post("/api/auth/logout")
def auth_logout(response: Response):
    """Clear the session cookie."""
    response.delete_cookie(key='eshu_session', path='/')
    return {"status": "ok"}


@app.post("/api/auth/set-password")
def set_password(payload: SetPasswordPayload, request: Request):
    """Set the dashboard password. Requires auth if password already exists.
    On first-run (no password set), this works without authentication."""
    # Only require auth if a password already exists (changing an existing one)
    if _is_password_protected():
        _check_session(request)
    new_password = payload.password.strip()
    if not new_password or len(new_password) < 4:
        raise HTTPException(status_code=400, detail="Password must be at least 4 characters")
    hash_value = _hash_password(new_password)
    set_password_hash(hash_value)
    record_audit_event("password_changed", details="Dashboard password updated")
    return {"status": "ok", "message": "Password updated successfully"}


# ── Gateway Endpoints (NO AUTH — gateways must reach these freely) ──────

@app.get("/api/version")
def get_version(request: Request):
    # Gateway-token callers (AI agents) are intentionally blocked. Gateways
    # (poller, logger, installer) fetch the version WITHOUT a token header,
    # and the dashboard UI uses the session cookie — neither sends X-Gateway-Token.
    token = request.headers.get("X-Gateway-Token", "").strip()
    if token and token != "None":
        raise HTTPException(status_code=401, detail="Not available to gateway-token callers")
    return {"version": DASHBOARD_VERSION, "dev_mode": _is_dev_mode()}

@app.get("/api/cmd-descs")
def get_cmd_descriptions():
    from core.cmd_descs import CMD_DESCRIPTIONS, _WHATSIS_CACHE
    return {"static": CMD_DESCRIPTIONS, "whatis": _WHATSIS_CACHE}

class GatewayPayload(BaseModel):
    target_ip: str
    encoded_command: str
    status: str = "pending"
    reason: str = ""
    token: str = ""

class RegisterPayload(BaseModel):
    ip: str
    hostname: str
    version: str = "v6.0"

class PolicyPayload(BaseModel):
    type: str
    content: str

class PolicyPreviewPayload(BaseModel):
    exact_whitelist: str = ""
    regex_whitelist: str = ""
    regex_blacklist: str = ""
    days: int = 30

class NotePayload(BaseModel):
    content: str

class SSHKeysPayload(BaseModel):
    eshu_key: str

def decode_cmd(encoded: str) -> str:
    try:
        return base64.b64decode(encoded).decode('utf-8')
    except Exception:
        return encoded

@app.post("/api/register")
def register(payload: RegisterPayload, request: Request):
    _check_rate_limit(payload.ip)
    # Detect enrollment vs version update vs heartbeat
    current_gws = {g['ip']: g for g in get_gateways()}
    existing = current_gws.get(payload.ip)
    
    # Deduplicate: suppress audit log if same IP+version logged within the dedup window
    dedup_key = f"{payload.ip}:{payload.version}"
    now = int(time.time())
    last = _last_enroll_log.get(dedup_key, 0)
    if now - last > _ENROLL_DEDUP_WINDOW:
        if existing is None:
            record_audit_event("enrolled", payload.ip, payload.hostname, f"Version {payload.version}")
        elif existing.get('version') != payload.version:
            record_audit_event("version_updated", payload.ip, payload.hostname, f"{existing.get('version')} → {payload.version}")
        else:
            record_audit_event("enrolled", payload.ip, payload.hostname, f"Version {payload.version} (re-registered)")
        _last_enroll_log[dedup_key] = now
    
    if payload.version == DASHBOARD_VERSION:
        update_gateway_last_updated(payload.ip)
    register_gateway(payload.ip, payload.hostname, payload.version)
    # Clear any stale uninstall trigger — re-enrollment cancels pending uninstalls
    clear_trigger_uninstall(payload.ip)
    
    # v15+: Generate or return existing gateway API token.
    # A stored literal 'None' (the v15.0 DEFAULT None migration bug) must be
    # treated as "no token" — otherwise register returns 'None' as a real token,
    # the installer rejects it, and the gateway self-heal floods every poll cycle.
    existing_token = get_gateway_token(payload.ip)
    if not existing_token or existing_token == 'None':
        existing_token = secrets.token_hex(32)
        set_gateway_token(payload.ip, existing_token)
    
    return {"status": "ok", "gateway_token": existing_token}

@app.post("/api/request")
def receive_request(payload: GatewayPayload, request: Request):
    # Resolve canonical IP from gateway token (v15+ auth)
    token_ip, _ = _resolve_gateway_token(request)
    target_ip = token_ip if token_ip else payload.target_ip
    # If token present, validate self-reported IP matches token's canonical IP
    if token_ip and token_ip != payload.target_ip:
        raise HTTPException(status_code=401, detail="Gateway token does not match self-reported target_ip")

    _check_rate_limit(target_ip)
    cmd = decode_cmd(payload.encoded_command)

    # Check override mode — auto-approve if active
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT override_until, override_reason FROM gateways WHERE ip = ?', (target_ip,))
    row = cursor.fetchone()
    conn.close()
    now = int(time.time())
    # Override auto-approves JIT — but never on a Zero-Trust gateway (ZT wins:
    # every command must go through operator approval there).
    if row and row['override_until'] and row['override_until'] > now and not get_gateway_zero_trust(target_ip):
        req_id = create_request(target_ip, cmd, status="approved", reason="override")
        update_gateway_last_seen(target_ip)
        record_audit_event('jit_override_approved', target_ip, details=f'Override auto-approved JIT #{req_id}: {cmd[:80]} (reason: {row["override_reason"][:60]})')
        return {"status": "ok", "id": f"{req_id:06d}", "override": True, "message": "Auto-approved via Override Mode"}

    req_id = create_request(target_ip, cmd, status="pending")
    update_gateway_last_seen(target_ip)
    send_notify('jit', 'JIT Approval Required', f'`{cmd[:80]}` on {target_ip}')
    record_audit_event('jit_created', target_ip, details=f'JIT #{req_id}: {cmd[:80]}')
    return {"status": "ok", "id": f"{req_id:06d}"}

@app.post("/api/log")
def receive_log(payload: GatewayPayload, request: Request):
    # Resolve canonical IP from gateway token (v15+ auth)
    token_ip, _ = _resolve_gateway_token(request)
    target_ip = token_ip if token_ip else payload.target_ip
    if token_ip and token_ip != payload.target_ip:
        raise HTTPException(status_code=401, detail="Gateway token does not match self-reported target_ip")

    cmd = decode_cmd(payload.encoded_command)
    create_request(target_ip, cmd, status=payload.status, ttl=0, reason=payload.reason)
    if payload.status == 'window-rejected' and payload.token:
        wins = get_approved_windows()
        target = next((w for w in wins if w['token'] == payload.token), None)
        if target:
            record_window_execution(target['id'], payload.token, target_ip, cmd, 0, payload.reason)
    if payload.status == 'blocked':
        send_notify('blocked', '🛑 Command Blocked', f'`{cmd[:80]}` on {target_ip}')
    update_gateway_last_seen(target_ip)
    return {"status": "ok"}


class HeartbeatPayload(BaseModel):
    ip: str
    hostname: str
    poller_ok: int = 0
    gateway_ok: int = 0
    can_reach: int = 0

@app.post("/api/gateway-heartbeat")
def receive_heartbeat(payload: HeartbeatPayload, request: Request):
    """Called by eshu-logger.service every 30s to report gateway health."""
    _check_rate_limit(payload.ip)
    update_gateway_last_seen(payload.ip)
    update_gateway_heartbeat(payload.ip, payload.hostname,
                             payload.poller_ok, payload.gateway_ok, payload.can_reach)
    return {"status": "ok"}


@app.get("/api/poll/{target_ip}")
def poll_ticket(target_ip: str, request: Request, wc: int = None):
    # Resolve canonical IP from gateway token (v15+ auth)
    token_ip, _ = _resolve_gateway_token(request)
    resolved_ip = token_ip if token_ip else target_ip
    if token_ip and token_ip != target_ip:
        raise HTTPException(status_code=401, detail="Gateway token does not match target_ip in URL")

    _check_rate_limit(resolved_ip)
    update_gateway_last_seen(resolved_ip)
    if wc is not None:
        update_gateway_windows_count(resolved_ip, wc)
    ticket = update_ticket_consumed_by_ip(resolved_ip)
    if ticket:
        record_audit_event('jit_consumed', resolved_ip, details=f'Ticket consumed by poller for {resolved_ip}')
    gw_mode = get_gateway_mode(resolved_ip)
    # Fetch last-reported gateway version for the poll response
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT version FROM gateways WHERE ip = ?', (resolved_ip,))
    row = cursor.fetchone()
    conn.close()
    gw_version = row["version"] if row else DASHBOARD_VERSION
    return {"ticket": ticket, "mode": gw_mode, "version": gw_version}

@app.get("/api/policy/{target_ip}")
def fetch_policy(target_ip: str, request: Request):
    # Resolve canonical IP from gateway token (v15+ auth)
    token_ip, _ = _resolve_gateway_token(request)
    resolved_ip = token_ip if token_ip else target_ip
    if token_ip and token_ip != target_ip:
        raise HTTPException(status_code=401, detail="Gateway token does not match target_ip in URL")

    update_gateway_last_seen(resolved_ip)
    update_gateway_policy_sync(resolved_ip)
    pv = get_policy_version()
    update_gateway_policy_version(resolved_ip, pv)
    policies = get_policies()
    policies['policy_version'] = pv
    policies['policy_updated_at'] = get_policy_updated_at()
    policies['dashboard_version'] = DASHBOARD_VERSION
    policies['trigger_update_version'] = get_trigger_update_version()
    policies['trigger_rollback'] = get_trigger_rollback()
    policies['trigger_uninstall'] = check_trigger_uninstall(resolved_ip) is not None
    policies['trigger_freeze'] = get_trigger_freeze() is not None
    policies['zero_trust'] = 1 if get_gateway_zero_trust(resolved_ip) else 0

    # Fleet Run — inject pending command into the selected gateways' policy response
    fleet_cmd = get_injectable_fleet_cmd(resolved_ip)
    if fleet_cmd:
        policies['pending_fleet_cmd'] = fleet_cmd['command']
        policies['pending_fleet_cmd_id'] = fleet_cmd['id']
        policies['pending_fleet_cmd_timeout'] = fleet_cmd['timeout']

    # ── Dev Mode + Feature Flags ──────────────────────────────────
    gw_mode = get_gateway_mode(resolved_ip)
    policies['mode'] = gw_mode

    # Approved Windows are core/always-on — delivered by data, not a feature flag
    policies['approved_windows'] = get_active_approved_windows(resolved_ip)

    # Dev-mode gateways get the dev installer URL for updates
    if gw_mode == 'dev':
        policies['dev_installer_url'] = '/static/dev/eshu-gateway-install.sh'
        policies['agent_docs_url'] = '/api/docs/agent-windows'
        policies['agent_manual_url'] = '/api/docs/agent-manual'

    # Dev update trigger — gateways in dev mode pull the latest dev script
    if gw_mode == 'dev':
        td = get_trigger_dev_update()
        if td:
            policies['trigger_dev_update'] = td

    return policies

@app.get("/api/request_status/{req_id}")
def check_request_status(req_id: int):
    status = get_request_status(req_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Request not found")
    return {"id": req_id, "status": status}

@app.get("/api/ticket/{req_id}")
def claim_ticket_by_id(req_id: int, request: Request):
    """Direct ticket claim endpoint - gateway fetches approved ticket by request ID.
    Bypasses the per-IP poller pipeline for immediate JIT approval delivery.
    v15+: Validates gateway token if present."""
    _resolve_gateway_token(request)  # Optional validation (legacy gateways pass through)
    ticket = get_ticket_by_request_id(req_id)
    if ticket:
        record_audit_event('jit_consumed', details=f'JIT #{req_id} ticket claimed via /api/ticket')
        return {"ticket": ticket['ticket']}
    return {"ticket": None}

# ── Dashboard Endpoints (AUTH PROTECTED — sensitive operations) ─────────

@app.get("/api/requests")
def list_requests(search: str = None):
    if search:
        reqs = search_requests(search)
    else:
        reqs = get_all_requests()
    now = int(time.time())
    for r in reqs:
        if r['status'] in ['pending', 'approved']:
            ttl = r['expires_at'] - now
            r['ttl'] = ttl if ttl > 0 else 0
        else:
            r['ttl'] = 0
        # Risk hint — pending-only so history stays clean
        r['risk'] = get_cmd_risk(r['command']) if r['status'] == 'pending' else None
        # Behavioural anomaly — pending-only ("first time on this gateway")
        r['anomaly'] = get_anomaly(r['target_ip'], r['command']) if r['status'] == 'pending' else None
    return reqs
@app.post("/api/approve/{req_id}")
def approve_request(req_id: int, request: Request):
    _check_session(request)
    update_request_status(req_id, "approved")
    record_audit_event('jit_approved', details=f'JIT #{req_id} approved')
    return {"status": "ok"}


@app.post("/api/deny/{req_id}")
def deny_request(req_id: int, request: Request):
    _check_session(request)
    command = get_request_command(req_id)
    update_request_status(req_id, "denied")
    record_audit_event('jit_denied', details=f'JIT #{req_id} denied')
    deny_count = count_denied(command) if command else 0
    return {"status": "ok", "deny_count": deny_count, "command": command}

@app.delete("/api/requests")
def purge_requests(older_than: str = "1h", request: Request = None):
    _check_session(request)
    now = int(time.time())
    offsets = {
        "30m": 30 * 60,
        "1h": 60 * 60,
        "1d": 24 * 60 * 60,
        "2d": 2 * 24 * 60 * 60,
        "7d": 7 * 24 * 60 * 60,
        "all": now,
    }
    offset = offsets.get(older_than, 60 * 60)
    before_ts = now - offset if older_than != "all" else now
    deleted = delete_old_requests(before_ts)
    return {"status": "ok", "deleted": deleted}

@app.get("/api/policies/test")
def test_policy(command: str):
    policies = get_policies()
    v = evaluate_policy_verdict(
        command,
        policies.get('exact_whitelist', ''),
        policies.get('regex_whitelist', ''),
        policies.get('regex_blacklist', ''),
    )

    if v['tier'] == 'fatal':
        return {
            "command": command,
            "matched": True,
            "action": "blocked",
            "tier": "fatal",
            "reason": v['reason'],
            "details": [{"type": "hard_blocklist", "pattern": v['matched_pattern'], "match": True}],
            "risk": get_cmd_risk(command),
            "dry_run": get_dry_run_suggestion(command),
        }

    result = {
        "command": command,
        "matched": v['action'] != 'jit',
        "action": v['action'],
        "details": [],
        "risk": get_cmd_risk(command),
        "dry_run": get_dry_run_suggestion(command),
    }
    if v['action'] == 'blocked':
        result["details"].append({"type": "regex_blacklist", "pattern": v['matched_pattern'], "match": True})
    elif v['action'] == 'auto_approved':
        if v['detail_type'] == 'exact_whitelist':
            result["details"].append({"type": "exact_whitelist", "matched_line": command, "match": True})
        else:
            result["details"].append({"type": "regex_whitelist", "pattern": v['matched_pattern'], "match": True})
    else:
        result["details"].append({"message": "No policy matched. Command would require JIT approval."})
    return result

@app.get("/api/policies/check")
def check_policy_membership(command: str, request: Request):
    """Check if a command is already in any policy list. Returns membership booleans."""
    _check_session(request)
    import re
    policies = get_policies()
    exact_lines = [l for l in policies.get('exact_whitelist', '').split('\n') if l.strip()]
    regex_white_lines = [l for l in policies.get('regex_whitelist', '').split('\n') if l.strip()]
    regex_black_lines = [l for l in policies.get('regex_blacklist', '').split('\n') if l.strip()]
    
    result = {
        "command": command,
        "in_exact_whitelist": command in exact_lines,
        "in_regex_whitelist": False,
        "in_regex_blacklist": False,
    }
    
    for pattern in regex_white_lines:
        try:
            if re.search(pattern, command):
                result["in_regex_whitelist"] = True
                break
        except re.error:
            pass
    
    for pattern in regex_black_lines:
        if blocklist_substring_match(pattern, command):
            result["in_regex_blacklist"] = True
            break
    
    return result

@app.get("/api/gateways")
def list_gateways(request: Request):
    _check_session(request)
    gateways = get_gateways()
    pv = get_policy_version()
    pua = get_policy_updated_at()
    now = int(time.time())
    _check_gateway_transitions(now)
    for g in gateways:
        g['current_policy_version'] = pv
        g['policy_updated_at'] = pua
        g['policy_synced'] = g.get('policy_version', 0) >= pv
        g['has_token'] = bool(g.get('api_token'))
        override_until = g.get('override_until', 0) or 0
        g['override_remaining'] = max(0, override_until - now)
    return gateways

class OverridePayload(BaseModel):
    minutes: int = 30
    reason: str = ""

@app.post("/api/gateways/{ip}/override")
def start_override(ip: str, payload: OverridePayload, request: Request):
    _check_session(request)
    if not ip or not payload.reason.strip():
        raise HTTPException(status_code=400, detail="Reason is required")
    if payload.minutes < 1 or payload.minutes > 1440:
        raise HTTPException(status_code=400, detail="Minutes must be between 1 and 1440")
    if get_gateway_zero_trust(ip):
        raise HTTPException(status_code=400, detail="Cannot start Override Mode on a Zero-Trust gateway — they are mutually exclusive.")
    override_until = int(time.time()) + (payload.minutes * 60)
    set_override(ip, override_until, payload.reason)
    hostname = _get_gateway_hostname(ip)
    record_audit_event("override_started", gateway_ip=ip, hostname=hostname,
                       details=f"Override for {payload.minutes}m: {payload.reason[:100]}")
    return {"status": "ok", "override_until": override_until}

@app.delete("/api/gateways/{ip}/override")
def stop_override(ip: str, request: Request):
    _check_session(request)
    clear_override(ip)
    hostname = _get_gateway_hostname(ip)
    record_audit_event("override_cancelled", gateway_ip=ip, hostname=hostname,
                       details="Override cancelled early")
    return {"status": "ok"}

# --- Emergency Freeze (global circuit breaker) ---
@app.post("/api/freeze")
def trigger_freeze(request: Request):
    """Freeze the entire fleet — every gateway rejects all commands until unfrozen."""
    _check_session(request)
    ts = set_trigger_freeze()
    record_audit_event("freeze_started", details=f"Fleet frozen at {ts}. All gateways will reject commands within the next poll cycle.")
    return {"status": "ok", "triggered_at": ts}

@app.post("/api/unfreeze")
def clear_freeze(request: Request):
    """Unfreeze the entire fleet — gateways resume normal policy enforcement."""
    _check_session(request)
    clear_trigger_freeze()
    record_audit_event("freeze_ended", details="Fleet unfrozen. Gateways resume normal operation on the next poll cycle.")
    return {"status": "ok"}

@app.get("/api/freeze/status")
def freeze_status(request: Request):
    _check_session(request)
    value = get_trigger_freeze()
    return {"frozen": value is not None, "triggered_at": int(value) if value else None}

# --- Fleet Run (Ansible-lite) ---
class FleetCommandPayload(BaseModel):
    command: str
    target_ips: list = []
    reason: str = ""
    timeout: int = 180
    override: bool = False

class FleetResultPayload(BaseModel):
    gateway_ip: str
    status: str
    exit_code: int = None
    output: str = ""

@app.post("/api/fleet/commands")
def submit_fleet_command(payload: FleetCommandPayload, request: Request):
    """Compose a fleet command. Operator-only (session). Validates safety and
    dispatches immediately — the operator's Dispatch button is the approval."""
    _check_session(request)
    _check_rate_limit(request.client.host if request.client else "127.0.0.1")

    command = payload.command.strip()
    if not command:
        raise HTTPException(status_code=400, detail="Command is required")
    if payload.timeout < 1 or payload.timeout > 3600:
        raise HTTPException(status_code=400, detail="Timeout must be between 1 and 3600 seconds")

    targets = list(dict.fromkeys(t for t in (payload.target_ips or []) if t))
    if not targets:
        raise HTTPException(status_code=400, detail="At least one target gateway is required")
    known_ips = {g['ip'] for g in get_gateways()}
    unknown = [t for t in targets if t not in known_ips]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown target gateway(s): {', '.join(unknown)}")

    if get_trigger_freeze() is not None:
        raise HTTPException(status_code=409, detail="Fleet is FROZEN — refusing to dispatch until unfrozen")

    blocked = hard_block_match(command)
    if blocked:
        raise HTTPException(status_code=400, detail=f"Command hits the hardcoded catastrophic blocklist (pattern: {blocked}) — cannot be dispatched")

    policies = get_policies()
    black_lines = [l for l in policies.get('regex_blacklist', '').split('\n') if l.strip()]
    blacklisted = None
    for pattern in black_lines:
        if blocklist_substring_match(pattern, command):
            blacklisted = pattern
            break
    if blacklisted and not payload.override:
        raise HTTPException(status_code=400, detail=f"Command matches the policy blocklist (pattern: {blacklisted}). Set override=true to dispatch anyway.")

    cmd_id = create_fleet_command(command, targets, "operator", payload.reason.strip(), payload.timeout)
    record_audit_event("fleet_created", details=f"Fleet #{cmd_id}: {command[:80]} → {', '.join(targets)}")
    approve_fleet_command(cmd_id)
    record_audit_event("fleet_dispatched", details=f"Fleet #{cmd_id}: {command[:80]} → {len(targets)} gateway(s)" +
                       (" (blacklist override)" if blacklisted else ""))
    # Surface in the main Dashboard history (one row per target gateway)
    for ip in targets:
        create_request(ip, command, status='fleet-run', ttl=0, reason=payload.reason.strip())
    return {"status": "ok", "id": cmd_id, "dispatched": True, "gateway_count": len(targets)}

@app.get("/api/fleet/commands")
def list_fleet_commands(request: Request):
    _check_session(request)
    return get_fleet_commands()

@app.get("/api/fleet/commands/{cmd_id}/output/{gateway_ip}")
def get_fleet_output(cmd_id: int, gateway_ip: str, request: Request):
    """Return the full stored output for one gateway's fleet result.
    Fetched on demand by the UI when an output box is expanded."""
    _check_session(request)
    result = get_fleet_result(cmd_id, gateway_ip)
    if not result:
        raise HTTPException(status_code=404, detail="Fleet result not found")
    return {"output": result.get('output') or ''}

@app.delete("/api/fleet/commands/{cmd_id}")
def clear_fleet_cmd(cmd_id: int, request: Request):
    """Delete a fleet command + results — clears a stuck command (e.g. one
    dispatched to a gateway on an old poller) so its per-gateway queue unblocks."""
    _check_session(request)
    cmd = get_fleet_command(cmd_id)
    if not cmd:
        raise HTTPException(status_code=404, detail="Fleet command not found")
    removed = delete_fleet_command(cmd_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Fleet command not found")
    record_audit_event("fleet_cleared", details=f"Fleet #{cmd_id}: {cmd['command'][:80]} cleared (status was {cmd['status']})")
    return {"status": "ok", "id": cmd_id}

@app.delete("/api/fleet/commands/{cmd_id}/result/{gateway_ip}")
def clear_fleet_result(cmd_id: int, gateway_ip: str, request: Request):
    """Clear ONE gateway's stuck result (mark it 'skipped') — the command and
    the other gateways' results are untouched. Unblocks that gateway's queue
    without losing the successful results."""
    _check_session(request)
    result = get_fleet_result(cmd_id, gateway_ip)
    if not result:
        raise HTTPException(status_code=404, detail="Fleet result not found")
    if result['status'] != 'queued':
        raise HTTPException(status_code=409, detail="Only a queued (stuck) result can be cleared")
    upsert_fleet_result(cmd_id, gateway_ip, 'skipped', None, 'Cleared by operator — gateway never ran it')
    record_audit_event("fleet_result_cleared", gateway_ip=gateway_ip,
                       details=f"Fleet #{cmd_id} result for {gateway_ip} marked skipped")
    return {"status": "ok", "id": cmd_id, "gateway_ip": gateway_ip}

@app.post("/api/fleet/commands/{cmd_id}/result")
def submit_fleet_result(cmd_id: int, payload: FleetResultPayload, request: Request):
    """Called by the gateway poller's fleet runner after execution completes."""
    token_ip, _ = _resolve_gateway_token(request)
    if not token_ip or token_ip != payload.gateway_ip:
        raise HTTPException(status_code=401, detail="Gateway token does not match result gateway")
    _check_rate_limit(payload.gateway_ip)
    result = get_fleet_result(cmd_id, payload.gateway_ip)
    if not result:
        raise HTTPException(status_code=404, detail="No queued result for this fleet command on this gateway")
    if payload.status not in ('running', 'success', 'failed', 'timeout'):
        raise HTTPException(status_code=400, detail=f"Invalid result status: {payload.status}")
    output = (payload.output or '')[:1048580]
    upsert_fleet_result(cmd_id, payload.gateway_ip, payload.status, payload.exit_code, output)
    record_audit_event("fleet_result", gateway_ip=payload.gateway_ip,
                       details=f"Fleet #{cmd_id} on {payload.gateway_ip}: {payload.status}" +
                               (f" (exit {payload.exit_code})" if payload.exit_code is not None else ""))
    return {"status": "ok"}

@app.get("/api/policies")
def list_policies():
    policies = get_policies()
    policies["policy_version"] = get_policy_version()
    policies["policy_updated_at"] = get_policy_updated_at()
    # Core registry: which blocklist entries are "shipped core" (for the UI
    # shield badge + warn-on-remove) and which patterns are non-editable.
    policies["core_patterns"] = CORE_COMMAND_PATTERNS
    policies["hard_patterns"] = HARD_PATTERNS
    return policies

@app.post("/api/policies/restore-core")
def restore_core_blocklist(request: Request):
    """Re-add any missing shipped core patterns to the blocklist (a deliberate,
    audited action). Removals persist until this is called."""
    _check_session(request)
    current = (get_policies().get('regex_blacklist') or '').split('\n')
    current = [l for l in current if l.strip()]
    existing = {l.strip() for l in current}
    missing = [p for p in CORE_COMMAND_PATTERNS if p not in existing]
    if missing:
        old = (get_policies().get('regex_blacklist') or '')
        new = '\n'.join(current + missing)
        update_policy('regex_blacklist', new)
        record_policy_change('regex_blacklist', old, new)
        increment_policy_version()
        set_policy_updated_at(int(time.time()))
    return {"status": "ok", "restored": len(missing)}

@app.post("/api/policies/preview")
def preview_policy_impact(payload: PolicyPreviewPayload, request: Request):
    """What-if: replay the last N days of distinct commands through a draft
    policy and report how many would flip vs. the current committed policy."""
    _check_session(request)
    days = max(1, min(int(payload.days or 30), 365))
    cutoff = int(time.time()) - days * 86400

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT DISTINCT command FROM requests WHERE created_at >= ?', (cutoff,))
    commands = [row['command'] for row in cursor.fetchall() if (row['command'] or '').strip()]
    conn.close()
    commands = commands[:2000]  # safety cap

    cur = get_policies()
    cur_exact, cur_rw, cur_rb = cur.get('exact_whitelist', ''), cur.get('regex_whitelist', ''), cur.get('regex_blacklist', '')

    flips = []
    newly_blocked = newly_allowed = newly_auto = newly_jit = fatal_count = 0
    for cmd in commands:
        before = evaluate_policy_verdict(cmd, cur_exact, cur_rw, cur_rb)
        after = evaluate_policy_verdict(cmd, payload.exact_whitelist, payload.regex_whitelist, payload.regex_blacklist)
        if after['tier'] == 'fatal':
            fatal_count += 1
            continue  # hard block — never affected by policy, never flips
        if before['action'] == after['action']:
            continue
        if after['action'] == 'blocked':
            newly_blocked += 1
        else:
            newly_allowed += 1
            if after['action'] == 'auto_approved':
                newly_auto += 1
            else:
                newly_jit += 1
        flips.append({
            "command": cmd,
            "before": before['action'],
            "after": after['action'],
            "reason": after['reason'],
        })

    return {
        "total": len(commands),
        "window_days": days,
        "fatal_count": fatal_count,
        "changed": len(flips),
        "newly_blocked": newly_blocked,
        "newly_allowed": newly_allowed,
        "newly_auto": newly_auto,
        "newly_jit": newly_jit,
        "flips": flips[:50],
    }

@app.get("/api/policy_changes")
def list_policy_changes():
    return get_policy_changes()

@app.post("/api/policies")
def save_policy(payload: PolicyPayload, request: Request):
    _check_session(request)
    old_policies = get_policies()
    update_policy(payload.type, payload.content)
    new_policies = get_policies()
    record_policy_change(payload.type, old_policies.get(payload.type, ""), new_policies.get(payload.type, ""))
    return {"status": "ok"}

@app.post("/api/policies/commit")
def commit_policies(request: Request):
    _check_session(request)
    new_version = increment_policy_version()
    set_policy_updated_at(int(time.time()))
    record_audit_event("policy_committed", details=f"Policy version v{new_version} pushed to gateways")
    return {"status": "ok", "policy_version": new_version}

@app.post("/api/policies/rollback/{change_id}")
def rollback_policy_change(change_id: int, request: Request):
    _check_session(request)
    change = get_policy_change(change_id)
    if not change:
        raise HTTPException(status_code=404, detail="Policy change not found")
    p_type = change['policy_type']
    old_content = change['old_content']
    # Restore the policy to the state before this change
    old_policies = get_policies()
    update_policy(p_type, old_content)
    new_policies = get_policies()
    record_policy_change(p_type, old_policies.get(p_type, ""), new_policies.get(p_type, ""))
    increment_policy_version()
    set_policy_updated_at(int(time.time()))
    record_audit_event("policy_rolled_back", details=f"Policy {p_type} rolled back to prior version")
    return {"status": "ok", "policy_type": p_type, "policy_version": get_policy_version()}

@app.post("/api/policies/trigger-update")
def trigger_gateway_update(request: Request):
    _check_session(request)
    trigger_id = str(int(time.time()))
    gateways = get_gateways()
    clear_trigger_rollback()
    record_audit_event("update_triggered", details=f"Gateway update to {DASHBOARD_VERSION} triggered for {len(gateways)} gateway(s).")
    set_trigger_update_version(trigger_id)
    return {"status": "ok", "version": DASHBOARD_VERSION, "gateway_count": len(gateways)}

class DismissGapPayload(BaseModel):
    command: str

@app.post("/api/policies/dismiss-gap")
def dismiss_policy_gap_endpoint(payload: DismissGapPayload, request: Request):
    _check_session(request)
    if not payload.command.strip():
        raise HTTPException(status_code=400, detail="Command is required")
    dismiss_policy_gap(payload.command.strip())
    return {"status": "ok"}

@app.get("/api/learning/gaps")
def get_learning_gaps(request: Request):
    _check_session(request)
    from core.learning import get_cached_gaps
    return get_cached_gaps()

@app.post("/api/learning/gaps/refresh")
def refresh_learning_gaps(request: Request):
    _check_session(request)
    from core.learning import refresh_gaps
    return refresh_gaps()

@app.post("/api/learning/gaps/mark-seen")
def mark_learning_gaps_seen(request: Request):
    _check_session(request)
    from core.learning import mark_all_seen
    mark_all_seen()
    return {"status": "ok"}

@app.post("/api/dev/seed")
def seed_edge(request: Request):
    """Copy current golden installer to dev (Edge) directory."""
    _check_session(request)
    src = os.path.join(static_dir, "eshu-gateway-install.sh")
    dst = os.path.join(static_dir, "dev", "eshu-gateway-install.sh")
    if not os.path.exists(src):
        raise HTTPException(status_code=404, detail="No golden installer found.")
    import shutil
    # Clear stale dev-update trigger and dev-push flag when starting a new Edge
    dh = get_deployed_golden_hash()
    gh = _file_hash(src)
    td = get_trigger_dev_update()
    if td and dh and gh != dh:
        clear_trigger_dev_update()
        clear_dev_push_initiated()
        record_audit_event("edge_seeded", details=f"Edge seeded from golden ({DASHBOARD_VERSION}) — previous dev cycle cleared")
    else:
        clear_dev_push_initiated()
        record_audit_event("edge_seeded", details=f"Edge seeded from golden ({DASHBOARD_VERSION})")
    shutil.copy(src, dst)
    return {"status": "ok", "version": DASHBOARD_VERSION}


@app.post("/api/dev/promote")
def promote_edge(request: Request):
    """Copy Edge installer to golden, backup previous golden, deploy fleet."""
    _check_session(request)
    golden_path = os.path.join(static_dir, "eshu-gateway-install.sh")
    edge_path = os.path.join(static_dir, "dev", "eshu-gateway-install.sh")
    if not os.path.exists(edge_path):
        raise HTTPException(status_code=404, detail="No Edge installer found. Seed Edge first.")
    import shutil
    # Backup current golden
    bak_path = os.path.join(static_dir, "eshu-gateway-install.sh.bak")
    if os.path.exists(golden_path):
        shutil.copy(golden_path, bak_path)
    # Promote Edge to Golden
    shutil.copy(edge_path, golden_path)
    # Also update source file so it can be committed
    src_path = os.path.join(os.path.dirname(__file__), "eshu-gateway-install.sh")
    shutil.copy(edge_path, src_path)
    # Track deployed golden hash
    clear_dev_push_initiated()
    clear_trigger_dev_update()
    gh = _file_hash(golden_path)
    if gh:
        set_deployed_golden_hash(gh)
    # Deploy fleet
    trigger_id = str(int(time.time()))
    clear_trigger_rollback()
    set_trigger_update_version(trigger_id)
    gateways = get_gateways()
    record_audit_event("promote_to_golden", details=f"Deployed {DASHBOARD_VERSION} to fleet — {len(gateways)} gateway(s)")
    return {"status": "ok", "version": DASHBOARD_VERSION, "gateway_count": len(gateways)}


@app.post("/api/dev/rollback")
def rollback_golden(request: Request):
    """Restore previous golden installer from backup and deploy fleet."""
    _check_session(request)
    golden_path = os.path.join(static_dir, "eshu-gateway-install.sh")
    bak_path = os.path.join(static_dir, "eshu-gateway-install.sh.bak")
    if not os.path.exists(bak_path):
        raise HTTPException(status_code=404, detail="No golden backup available. Promote to golden first.")
    import shutil
    shutil.copy(bak_path, golden_path)
    # Also update source
    src_path = os.path.join(os.path.dirname(__file__), "eshu-gateway-install.sh")
    shutil.copy(bak_path, src_path)
    clear_dev_push_initiated()
    clear_trigger_dev_update()
    gh = _file_hash(golden_path)
    if gh:
        set_deployed_golden_hash(gh)
    trigger_id = str(int(time.time()))
    clear_trigger_rollback()
    set_trigger_update_version(trigger_id)
    gateways = get_gateways()
    record_audit_event("golden_rollback", details=f"Golden rolled back to previous installer for {len(gateways)} gateway(s)")
    return {"status": "ok", "version": DASHBOARD_VERSION, "gateway_count": len(gateways)}


@app.get("/api/dev/status")
def dev_status(request: Request):
    """Return Build/Edge/Fleet pipeline deployment state (hash + trigger based)."""
    _check_session(request)
    golden_path = os.path.join(static_dir, "eshu-gateway-install.sh")
    edge_path = os.path.join(static_dir, "dev", "eshu-gateway-install.sh")
    source_path = os.path.join(os.path.dirname(__file__), "eshu-gateway-install.sh")
    bak_path = os.path.join(static_dir, "eshu-gateway-install.sh.bak")

    golden_hash = _file_hash(golden_path)
    edge_hash   = _file_hash(edge_path)
    source_hash = _file_hash(source_path)
    dh = get_deployed_golden_hash()
    td = get_trigger_dev_update()

    if golden_hash != edge_hash:
        pipeline_state = "needs_seed"
    elif td and (dh is None or golden_hash != dh):
        pipeline_state = "dev_in_progress"
    elif get_dev_push_initiated() and dh and golden_hash != dh:
        pipeline_state = "ready_for_promote"
    elif dh and golden_hash != dh:
        pipeline_state = "ready_for_dev"
    else:
        pipeline_state = "clear"

    gateways = get_gateways()
    dev_gws = get_dev_gateways()

    return {
        "dashboard_version": DASHBOARD_VERSION,
        "edge_exists": edge_hash is not None,
        "backup_exists": os.path.exists(bak_path),
        "dev_gateway_count": len(dev_gws),
        "dev_gateways": [{"ip": d["ip"], "hostname": d.get("hostname", "")} for d in dev_gws],
        "gateway_count": len(gateways),
        "pipeline_state": pipeline_state,
        "golden_hash": golden_hash,
        "edge_hash": edge_hash,
        "edge_matches_golden": golden_hash == edge_hash,
        "source_hash": source_hash,
        "deployed_hash": dh,
    }


@app.get("/api/policies/rollback-status")
def get_rollback_status(request: Request):
    """Return info about available golden backup for the UI."""
    _check_session(request)
    bak_path = os.path.join(static_dir, "eshu-gateway-install.sh.bak")
    return {
        "dashboard_version": DASHBOARD_VERSION,
        "backup_available": os.path.exists(bak_path),
        "trigger_rollback": get_trigger_rollback(),
    }


@app.get("/api/gateway-script-rollback", response_class=PlainTextResponse)
def serve_rollback_script():
    """Serve the golden installer for gateway rollback."""
    golden_path = os.path.join(static_dir, "eshu-gateway-install.sh")
    if not os.path.exists(golden_path):
        raise HTTPException(status_code=404, detail="No golden installer available.")
    with open(golden_path, "r", encoding="utf-8") as f:
        content = f.read()
    return PlainTextResponse(content=content, media_type="text/plain; charset=utf-8")

# --- Gateway Uninstall ---
class UninstallProgressPayload(BaseModel):
    ip: str
    step: str
    message: str = ""

@app.post("/api/uninstall-progress")
def uninstall_progress(payload: UninstallProgressPayload):
    """Receive progress updates from the gateway uninstall script.
    Called by the transient eshu-uninstall systemd service as it works through cleanup steps."""
    set_uninstall_progress(payload.ip, payload.step, payload.message)
    if payload.step == "complete":
        clear_trigger_uninstall(payload.ip)
        clear_uninstall_progress(payload.ip)
        deregister_gateway(payload.ip)
        target = next((g for g in get_gateways() if g['ip'] == payload.ip), None)
        hostname = target.get('hostname', 'unknown') if target else 'unknown'
        record_audit_event("uninstalled", payload.ip, hostname, "Gateway uninstalled via remote trigger")
        if payload.ip in _disconnected_gateways:
            _disconnected_gateways.discard(payload.ip)
    return {"status": "ok"}

@app.post("/api/uninstall-started/{ip}")
def uninstall_started(ip: str):
    """Called by the poller after launching the transient uninstall service.
    Clears the trigger so the restarted poller doesn't re-spawn duplicate uninstalls."""
    clear_trigger_uninstall(ip)
    return {"status": "ok"}

@app.get("/api/uninstall-progress/{ip}")
def get_uninstall_progress_for_ip(ip: str):
    """Fetch current uninstall progress for a gateway. Used by the dashboard UI."""
    progress = get_uninstall_progress(ip)
    if progress:
        return {"ip": ip, "progress": progress}
    return {"ip": ip, "progress": None}

@app.post("/api/gateways/{ip}/uninstall")
def trigger_gateway_uninstall(ip: str, request: Request):
    """Set the uninstall trigger for a specific gateway. The poller will pick it up on next cycle."""
    _check_session(request)
    gateways = get_gateways()
    target = next((g for g in gateways if g['ip'] == ip), None)
    if not target:
        raise HTTPException(status_code=404, detail="Gateway not found")
    ts = set_trigger_uninstall(ip)
    record_audit_event("uninstall_triggered", ip, target.get('hostname'),
                       f"Uninstall trigger set at {ts}. Gateway will self-deregister on completion.")
    return {"status": "ok", "ip": ip, "hostname": target.get('hostname'),
            "message": "Uninstall triggered. Gateway will remove itself on next poll cycle."}

@app.delete("/api/gateways/{ip}")
def remove_gateway(ip: str, request: Request):
    """Deregister a gateway. Called by the gateway itself after successful uninstall.
    Also available to authenticated dashboard operators for force removal."""
    gateways = get_gateways()
    target = next((g for g in gateways if g['ip'] == ip), None)
    if not target:
        raise HTTPException(status_code=404, detail="Gateway not found")
    # Gateway self-deregistration: validate X-Gateway-Token matches the target IP.
    # Dashboard force-removal: requires session auth.
    if not _check_session_optional(request):
        token_ip, _ = _resolve_gateway_token(request)
        if not token_ip or token_ip != ip:
            # Try session auth as last resort (will raise 401 if not authed)
            _check_session(request)
    hostname = target.get('hostname', 'unknown')
    deleted = deregister_gateway(ip)
    if deleted:
        clear_trigger_uninstall(ip)
        if ip in _disconnected_gateways:
            _disconnected_gateways.discard(ip)
        record_audit_event("uninstalled", ip, hostname, "Gateway uninstalled and deregistered")
        return {"status": "ok", "ip": ip, "hostname": hostname}
    raise HTTPException(status_code=404, detail="Gateway not found")

# --- Gateway Script Download ---
@app.get("/api/gateway-script", response_class=PlainTextResponse)
def serve_gateway_script():
    """Serve the raw gateway installer script with explicit UTF-8 encoding.
    Bypasses static file serving to avoid encoding corruption during HTTP transfer."""
    install_src = os.path.join(os.path.dirname(__file__), "eshu-gateway-install.sh")
    if os.path.exists(install_src):
        with open(install_src, "r", encoding="utf-8") as f:
            content = f.read()
        return PlainTextResponse(content=content, media_type="text/plain; charset=utf-8")
    raise HTTPException(status_code=404, detail="Installer script not found")

@app.get("/api/docs/agent-windows", response_class=PlainTextResponse)
def serve_agent_window_docs():
    """Serve the AI agent manual (alias of /api/docs/agent-manual). Kept for
    backward compatibility — the manual is a single file covering gateway
    interaction and Approved Windows."""
    docs_src = os.path.join(os.path.dirname(__file__), "..", "docs", "AGENT_MANUAL.md")
    if os.path.exists(docs_src):
        with open(docs_src, "r", encoding="utf-8") as f:
            content = f.read()
        return PlainTextResponse(content=content, media_type="text/markdown; charset=utf-8")
    raise HTTPException(status_code=404, detail="Agent manual not found")


@app.get("/api/docs/agent-manual", response_class=PlainTextResponse)
def serve_agent_manual():
    """Serve the AI agent manual for gateway interaction and Approved Windows.
    Public endpoint so agents can fetch instructions from the dashboard."""
    docs_src = os.path.join(os.path.dirname(__file__), "..", "docs", "AGENT_MANUAL.md")
    if os.path.exists(docs_src):
        with open(docs_src, "r", encoding="utf-8") as f:
            content = f.read()
        return PlainTextResponse(content=content, media_type="text/markdown; charset=utf-8")
    raise HTTPException(status_code=404, detail="Agent manual not found")

# --- Audit Log ---
@app.get("/api/audit_log")
def fetch_audit_log(search: str = None):
    if search:
        logs = search_audit_log(search)
    else:
        logs = get_audit_log(200)
    return logs

# --- Enrollment ---
@app.get("/api/enroll/keys")
def fetch_enroll_keys(request: Request):
    _check_session(request)
    return get_ssh_keys()

@app.put("/api/enroll/keys")
def save_enroll_keys(payload: SSHKeysPayload, request: Request):
    _check_session(request)
    save_ssh_keys(payload.eshu_key)
    return {"status": "ok"}

@app.post("/api/enroll/generate")
def generate_token(request: Request):
    _check_session(request)
    token = generate_enrollment_token()
    return {"token": token}

@app.get("/api/enroll/token-status")
def check_token_status(token: str):
    """Check whether an enrollment token has been used (consumed by a gateway)."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT used, expires_at FROM enrollment_tokens WHERE token = ?", (token,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return {"valid": False, "used": False, "expires_at": 0}
    return {"valid": True, "used": bool(row['used']), "expires_at": row['expires_at']}


@app.get("/api/enroll")
def serve_enrollment_script(token: str, request: Request):
    valid, expires_at = validate_enrollment_token(token)
    if not valid:
        return PlainTextResponse(
            content="echo '❌ Invalid or expired enrollment token. Generate a new one in the dashboard.'; exit 1\n",
            media_type="text/plain"
        )
    
    keys = get_ssh_keys()
    eshu_key = keys.get('eshu_ssh_key', '')

    if not eshu_key:
        return PlainTextResponse(
            content="echo '❌ SSH key not configured in dashboard. Please set it first.'; exit 1\n",
            media_type="text/plain"
        )
    
    # Determine dashboard base URL from the request
    host = request.headers.get('host', 'localhost')
    scheme = request.headers.get('X-Forwarded-Proto', 'https' if request.url.scheme == 'https' else 'http')
    if scheme not in ('http', 'https'):
        scheme = 'http'
    base_url = f"{scheme}://{host}"
    
    # Build script line by line to avoid curly brace conflicts
    lines = [
        "#!/bin/bash",
        "set -euo pipefail",
        f"echo '🚀 Eshu Gateway {DASHBOARD_VERSION} — Dashboard-Led Enrollment'",
        "echo ''",
        f"DASHBOARD_URL='{base_url}'",
        f"GATEWAY_KEY='{eshu_key}'",
        f"SCRIPT_URL='{base_url}/static/eshu-gateway-install.sh'",
        "",
        "echo 'Downloading Eshu Gateway installer...'",
        "curl -sL -o /tmp/eshu-install.sh \"$SCRIPT_URL\" 2>/dev/null || {",
        "    echo 'ERROR: Failed to download installer from '$SCRIPT_URL",
        "    exit 1",
        "}",
        "",
        "if [ ! -s /tmp/eshu-install.sh ]; then",
        "    echo 'ERROR: Could not download installer. Please check the dashboard URL.'",
        "    exit 1",
        "fi",
        "",
        "chmod +x /tmp/eshu-install.sh",
        "echo 'Keys configured. Running installer...'",
        "# Privilege-aware launch: root runs the installer directly (TrueNAS SCALE",
        "# shell is root and has no sudo); otherwise elevate via sudo when present;",
        "# otherwise give a clear message instead of 'sudo: command not found'.",
        "if [ \"$(id -u)\" -eq 0 ]; then",
        "  bash /tmp/eshu-install.sh --reinstall \"$GATEWAY_KEY\" \"$DASHBOARD_URL\"",
        "elif command -v sudo >/dev/null 2>&1; then",
        "  sudo bash /tmp/eshu-install.sh --reinstall \"$GATEWAY_KEY\" \"$DASHBOARD_URL\"",
        "else",
        "  echo ''",
        "  echo '❌ Eshu Gateway requires root or sudo to install — neither is available on this host.'",
        "  echo '   Home Assistant OS and other rootless/immutable systems are not supported.'",
        "  echo ''",
        "  exit 1",
        "fi",
        "INSTALL_RESULT=$?",
        "",
        "rm -f /tmp/eshu-install.sh",
        "",
        "HOSTNAME=$(hostname)",
        "TARGET_IP=$(hostname -I | awk '{print $1}')",
        "if [ $INSTALL_RESULT -eq 0 ]; then",
        "  echo ''",
        f"  echo 'Eshu Gateway {DASHBOARD_VERSION} enrolled successfully on '$HOSTNAME' ('$TARGET_IP')'",
        "  echo 'The gateway is now polling the dashboard and will auto-update.'",
        "else",
        "  echo ''",
        "  echo 'ERROR: Gateway enrollment failed with exit code '$INSTALL_RESULT",
        "  echo 'Check the output above for details.'",
        "fi"
    ]
    script = "\n".join(lines) + "\n"
    return PlainTextResponse(content=script, media_type="text/plain")

# ── Statistics ──────────────────────────────────────────────────────────

@app.get("/api/statistics")
def get_statistics(days: int = 14, gateway_ip: str = None, gateway_ips: str = None, extended: bool = False):
    """Return per-gateway and daily command statistics for the dashboard chart.
    If gateway_ips is provided (comma-separated), filter all queries to those IPs.
    If extended=true, include hourly heatmap, automation trend, window stats, and gateway health."""
    from db.core import get_db as _get_db
    conn = _get_db()
    cursor = conn.cursor()

    # Resolve IP filter list
    ip_list = None
    if gateway_ips:
        ip_list = [ip.strip() for ip in gateway_ips.split(',') if ip.strip()]
    elif gateway_ip:
        ip_list = [gateway_ip.strip()]
    if ip_list:
        ip_placeholders = ','.join(['?' for _ in ip_list])
        ip_filter = f' AND r.target_ip IN ({ip_placeholders})'
        ip_params = tuple(ip_list)
    else:
        ip_filter = ''
        ip_params = ()

    cutoff = int(time.time()) - (days * 86400)

    # Per-gateway aggregation
    cursor.execute(f'''
        SELECT r.target_ip AS ip, g.hostname, g.mode,
               COUNT(*) AS total,
               SUM(CASE WHEN r.status IN ('auto-approved','consumed','window-approved') THEN 1 ELSE 0 END) AS auto_approved,
               SUM(CASE WHEN r.status = 'blocked' THEN 1 ELSE 0 END) AS blocked,
               SUM(CASE WHEN r.status = 'denied' THEN 1 ELSE 0 END) AS denied
        FROM requests r
        LEFT JOIN gateways g ON r.target_ip = g.ip
        WHERE r.created_at >= ?{ip_filter} AND r.status != 'window-rejected'
        GROUP BY r.target_ip
        ORDER BY total DESC
    ''', (cutoff,) + ip_params)
    per_gateway = [dict(row) for row in cursor.fetchall()]

    # Daily aggregation (all gateways)
    cursor.execute(f'''
        SELECT DATE(created_at, 'unixepoch') AS date,
               COUNT(*) AS total,
               SUM(CASE WHEN status IN ('auto-approved','consumed','window-approved') THEN 1 ELSE 0 END) AS auto_approved,
               SUM(CASE WHEN status IN ('approved','consumed') THEN 1 ELSE 0 END) AS jit_approved,
               SUM(CASE WHEN status = 'blocked' THEN 1 ELSE 0 END) AS blocked,
               SUM(CASE WHEN status = 'denied' THEN 1 ELSE 0 END) AS denied
        FROM requests
        WHERE created_at >= ?{ip_filter} AND status != 'window-rejected'
        GROUP BY date
        ORDER BY date ASC
    ''', (cutoff,) + ip_params)
    daily = [dict(row) for row in cursor.fetchall()]

    # Fill gaps for days with zero activity
    seen_dates = {d['date'] for d in daily}
    now = int(time.time())
    filled = []
    for i in range(days):
        d = time.strftime('%Y-%m-%d', time.localtime(now - (days - 1 - i) * 86400))
        if d in seen_dates:
            row = next(r for r in daily if r['date'] == d)
            filled.append(row)
        else:
            filled.append({"date": d, "total": 0, "auto_approved": 0, "jit_approved": 0, "blocked": 0, "denied": 0})

    # Top commands — split on shell chaining operators
    cursor.execute(f'SELECT command FROM requests WHERE created_at >= ?{ip_filter}', (cutoff,) + ip_params)
    all_cmds = [r[0] for r in cursor.fetchall() if r[0]]
    import re as _re
    cmd_counts = {}
    total_cmds = 0
    for c in all_cmds:
        parts = _re.split(r'\s*(?:&&|\|\||;|\|)\s*', c)
        for p in parts:
            p = p.strip()
            if not p or len(p) < 2:
                continue
            total_cmds += 1
            cmd_counts[p] = cmd_counts.get(p, 0) + 1
    top_commands = sorted([{"command": k, "count": v, "pct": round(v / total_cmds * 100, 1) if total_cmds > 0 else 0} for k, v in cmd_counts.items()], key=lambda x: x['count'], reverse=True)[:10]

    # Enrich top commands with descriptions if extended
    if extended:
        from core.cmd_descs import describe_command
        for tc in top_commands:
            tc['description'] = describe_command(tc['command'])

    result = {"daily": filled, "per_gateway": per_gateway, "top_commands": top_commands}

    if extended:
        # Hourly heatmap — 24-element array
        cursor.execute(f'''
            SELECT CAST(strftime('%H', created_at, 'unixepoch') AS INTEGER) AS hour,
                   COUNT(*) AS count
            FROM requests
            WHERE created_at >= ?{ip_filter} AND status != 'window-rejected'
            GROUP BY hour
            ORDER BY hour
        ''', (cutoff,) + ip_params)
        hourly_data = {r['hour']: r['count'] for r in cursor.fetchall()}
        result['hourly_heatmap'] = [hourly_data.get(h, 0) for h in range(24)]

        # Automation trend — add approved/executed counts to daily data
        cursor.execute(f'''
            SELECT DATE(created_at, 'unixepoch') AS date,
                   SUM(CASE WHEN status IN ('auto-approved','window-approved') THEN 1 ELSE 0 END) AS auto_approved,
                   SUM(CASE WHEN status IN ('approved','consumed') THEN 1 ELSE 0 END) AS jit_approved
            FROM requests
            WHERE created_at >= ?{ip_filter} AND status NOT IN ('window-rejected','denied','blocked')
            GROUP BY date
            ORDER BY date ASC
        ''', (cutoff,) + ip_params)
        trend_map = {}
        for r in cursor.fetchall():
            trend_map[r['date']] = {'auto_approved': r['auto_approved'], 'jit_approved': r['jit_approved']}
        result['automation_trend'] = []
        for day in filled:
            d = day['date']
            total = day['total']
            td = trend_map.get(d, {'auto_approved': 0, 'jit_approved': 0})
            result['automation_trend'].append({
                'date': d,
                'auto_approved': td['auto_approved'],
                'jit_approved': td['jit_approved'],
                'automation_pct': round((td['auto_approved'] + td['jit_approved']) / total * 100, 1) if total > 0 else 0
            })

        # Windows summary
        cursor.execute('''
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN enabled = 1 THEN 1 ELSE 0 END) AS active,
                   SUM(CASE WHEN origin = 'ai' THEN 1 ELSE 0 END) AS ai_created,
                   SUM(CASE WHEN execution_count = 0 THEN 1 ELSE 0 END) AS unused
            FROM approved_windows
        ''')
        win_row = cursor.fetchone()
        result['windows_summary'] = {
            'total': win_row['total'],
            'active': win_row['active'] or 0,
            'ai_created': win_row['ai_created'] or 0,
            'unused': win_row['unused'] or 0
        }

        # Gateway health
        cursor.execute('''
            SELECT version, COUNT(*) AS count
            FROM gateways
            GROUP BY version
            ORDER BY count DESC
        ''')
        version_dist = [dict(r) for r in cursor.fetchall()]
        cursor.execute('SELECT COUNT(*) AS total FROM gateways')
        total_gws = cursor.fetchone()['total']
        cursor.execute('SELECT COUNT(*) AS cnt FROM gateways WHERE api_token IS NOT NULL AND api_token != \'\'')
        token_count = cursor.fetchone()['cnt']
        cursor.execute('SELECT COUNT(*) AS cnt FROM gateways WHERE ? - last_seen < 120', (now,))
        online_count = cursor.fetchone()['cnt']
        result['gateway_health'] = {
            'version_distribution': version_dist,
            'total_gateways': total_gws,
            'online_gateways': online_count,
            'token_coverage': round(token_count / total_gws * 100, 1) if total_gws > 0 else 0
        }

        # Top denied commands
        cursor.execute(f'''
            SELECT command, COUNT(*) AS count
            FROM requests
            WHERE status = 'denied' AND created_at >= ?{ip_filter}
            GROUP BY command
            ORDER BY count DESC
            LIMIT 10
        ''', (cutoff,) + ip_params)
        result['top_denied'] = [dict(r) for r in cursor.fetchall()]

        # Command categories
        from core.cmd_categories import categorize_command
        cat_counts = {}
        for c in all_cmds:
            cat = categorize_command(c)
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
        total_categorized = sum(cat_counts.values()) or 1
        result['category_counts'] = sorted(
            [{"category": k, "count": v, "pct": round(v / total_categorized * 100, 1)}
             for k, v in cat_counts.items()],
            key=lambda x: x['count'], reverse=True
        )

    conn.close()

    return result


@app.get("/api/statistics/export")
def export_statistics(days: int = 14, format: str = "csv"):
    """Export daily statistics as CSV or JSON for external analysis."""
    import csv as _csv, io as _io
    cutoff = int(time.time()) - (days * 86400)
    from db.core import get_db as _get_db
    conn = _get_db()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT DATE(created_at, 'unixepoch') AS date,
               COUNT(*) AS total,
               SUM(CASE WHEN status IN ('auto-approved','consumed','window-approved') THEN 1 ELSE 0 END) AS auto_approved,
               SUM(CASE WHEN status IN ('approved','consumed') THEN 1 ELSE 0 END) AS jit_approved,
               SUM(CASE WHEN status = 'blocked' THEN 1 ELSE 0 END) AS blocked,
               SUM(CASE WHEN status = 'denied' THEN 1 ELSE 0 END) AS denied
        FROM requests
        WHERE created_at >= ? AND status != 'window-rejected'
        GROUP BY date ORDER BY date ASC
    ''', (cutoff,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    if format == "json":
        return rows
    buf = _io.StringIO()
    w = _csv.writer(buf)
    w.writerow(['date', 'total', 'auto_approved', 'jit_approved', 'blocked', 'denied'])
    for r in rows:
        w.writerow([r['date'], r['total'], r['auto_approved'], r['jit_approved'], r['blocked'], r['denied']])
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=eshu-statistics-{days}d.csv"})


# --- Notes ---
@app.get("/api/notes")
def fetch_note():
    return {"content": get_note()}

@app.post("/api/notes")
def save_note(payload: NotePayload, request: Request):
    _check_session(request)
    update_note(payload.content)
    return {"status": "ok"}

# ── Feature Flags ──────────────────────────────────────────────────────

class FeatureFlagTogglePayload(BaseModel):
    enabled: bool

@app.get("/api/feature-flags")
def get_flags(request: Request):
    _check_session(request)
    return get_feature_flags()

@app.post("/api/feature-flags/{flag_name}/toggle")
def toggle_flag(flag_name: str, payload: FeatureFlagTogglePayload, request: Request):
    _check_session(request)
    set_feature_flag(flag_name, payload.enabled)
    record_audit_event("feature_flag_toggled", details=f"Flag '{flag_name}' set to {'enabled' if payload.enabled else 'disabled'}")
    return {"status": "ok", "flag_name": flag_name, "enabled": payload.enabled}

class FeatureFlagStatePayload(BaseModel):
    state: str  # 'off', 'dev', 'prod'

@app.post("/api/feature-flags/{flag_name}/state")
def set_feature_state(flag_name: str, payload: FeatureFlagStatePayload, request: Request):
    _check_session(request)
    if payload.state == 'off':
        set_feature_flag(flag_name, False)
        record_audit_event("feature_flag_toggled", details=f"Flag '{flag_name}' set to off")
    elif payload.state == 'dev':
        set_feature_flag(flag_name, True)
        set_feature_flag_scope(flag_name, 'dev')
        record_audit_event("feature_flag_toggled", details=f"Flag '{flag_name}' set to dev")
    elif payload.state == 'prod':
        set_feature_flag(flag_name, True)
        set_feature_flag_scope(flag_name, 'prod')
        record_audit_event("feature_flag_toggled", details=f"Flag '{flag_name}' set to prod")
    else:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"Invalid state: {payload.state}")
    return {"status": "ok", "flag_name": flag_name, "state": payload.state}

# ── Gateway Dev Mode ──────────────────────────────────────────────────

class GatewayModePayload(BaseModel):
    mode: str  # 'prod' or 'dev'

@app.put("/api/gateways/{ip}/mode")
def set_gw_mode(ip: str, payload: GatewayModePayload, request: Request):
    _check_session(request)
    if payload.mode not in ('prod', 'dev'):
        raise HTTPException(status_code=400, detail="Mode must be 'prod' or 'dev'")
    set_gateway_mode(ip, payload.mode)
    record_audit_event("gateway_mode_changed", ip, details=f"Mode changed to '{payload.mode}'")
    return {"status": "ok", "ip": ip, "mode": payload.mode}

class ZeroTrustPayload(BaseModel):
    enabled: bool = True

@app.post("/api/gateways/{ip}/zero-trust")
def set_gw_zero_trust(ip: str, payload: ZeroTrustPayload, request: Request):
    """Toggle Zero-Trust mode for a gateway: allowlisted commands no longer
    auto-run — everything routes to JIT for operator approval. Mutually
    exclusive with Override Mode (which auto-approves)."""
    _check_session(request)
    if payload.enabled and get_override_active(ip):
        raise HTTPException(status_code=400, detail="Cannot enable Zero-Trust while Override Mode is active — they are mutually exclusive. Cancel the override first.")
    set_gateway_zero_trust(ip, payload.enabled)
    record_audit_event("zero_trust_" + ("enabled" if payload.enabled else "disabled"), ip,
                       details=f"Zero-Trust mode {'enabled' if payload.enabled else 'disabled'} for gateway")
    return {"status": "ok", "ip": ip, "zero_trust": payload.enabled}

@app.get("/api/dev-gateways")
def list_dev_gateways(request: Request):
    _check_session(request)
    return [{"ip": g["ip"], "hostname": g.get("hostname", "")} for g in get_dev_gateways()]

@app.post("/api/dev-gateways/push")
def push_dev_gateways(request: Request):
    """Set the dev update trigger so dev-mode gateways pull the latest installer
    on their next policy poll cycle. Only affects gateways in dev mode."""
    _check_session(request)
    trigger_id = str(int(time.time()))
    set_trigger_dev_update(trigger_id)
    set_dev_push_initiated()
    dev_gws = get_dev_gateways()
    names = [d.get('hostname') or d['ip'] for d in dev_gws]
    record_audit_event("dev_update_pushed", details=f"Dev update trigger {trigger_id} pushed to {len(dev_gws)} dev gateway(s): {', '.join(names)}")
    return {"status": "ok", "trigger_id": trigger_id, "dev_gateway_count": len(dev_gws),
            "dev_gateway_names": names, "stale_gateways": []}

# ── Approved Windows ──────────────────────────────────────────────────

class ApprovedWindowPayload(BaseModel):
    target_ip: str
    command: str
    window_start: int = 0
    window_end: int = 0
    max_executions: int = 1
    label: str = ''
    days_of_week: int = 0
    execution_time: int = 0
    expires_at: Optional[int] = None
    match_type: str = 'exact'

class WindowUpdatePayload(BaseModel):
    command: str = None
    label: str = None
    max_executions: int = None
    days_of_week: int = None
    execution_time: int = None
    expires_at: Optional[int] = None
    match_type: str = None
    window_start: int = None
    window_end: int = None

@app.post("/api/approved-windows")
def create_window(payload: ApprovedWindowPayload, request: Request):
    _check_session(request)
    # Single-use windows must have a start time; immediate one-offs use JIT
    dow = payload.days_of_week or 0
    et = payload.execution_time or 0
    if dow == 0 and et == 0 and not payload.window_start:
        raise HTTPException(status_code=400, detail="Single-use windows require a start time (window_start). For an immediate one-off command, use JIT approval instead.")
    result = create_approved_window(
        payload.target_ip, payload.command,
        payload.window_start, payload.window_end,
        payload.max_executions, payload.label,
        payload.days_of_week, payload.execution_time,
        payload.expires_at, payload.match_type
    )
    record_audit_event("window_created", payload.target_ip,
                       details=f"Window '{payload.label or 'unnamed'}' token={result.get('token','')} cmd={payload.command[:80]}")
    return result

@app.put("/api/approved-windows/{window_id}")
def edit_window(window_id: int, payload: WindowUpdatePayload, request: Request):
    _check_session(request)
    wins = get_approved_windows()
    target = next((w for w in wins if w['id'] == window_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Window not found")
    updates = {k: v for k, v in payload.dict().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    # Merge with target row and enforce: single-use windows require a start time
    merged = dict(target)
    merged.update(updates)
    dow_e = int(merged.get('days_of_week') or 0)
    et_e = int(merged.get('execution_time') or 0)
    ws_e = int(merged.get('window_start') or 0)
    if dow_e == 0 and et_e == 0 and ws_e == 0:
        raise HTTPException(status_code=400, detail="Single-use windows require a start time (window_start). For an immediate one-off command, use JIT approval instead.")
    ok = update_approved_window(window_id, **updates)
    if not ok:
        raise HTTPException(status_code=404, detail="Update failed")
    record_audit_event("window_modified", target.get('target_ip'),
                       details=f"Window #{window_id} updated: {', '.join(updates.keys())}")
    return {"status": "ok", "id": window_id}

@app.get("/api/approved-windows")
def list_windows(request: Request, ip: str = None):
    """List approved windows. Open to agents (read-only) — full status, but the
    token and retrieval_key fields are omitted for unauthenticated callers
    (session-authed UI still sees them). Tokens are retrievable by the opaque
    retrieval_key via /api/window-requests/{key}."""
    _check_rate_limit(request.client.host if request.client else "127.0.0.1")
    wins = get_approved_windows(ip)
    if not _check_session_optional(request):
        for w in wins:
            w.pop('retrieval_key', None)
            w.pop('token', None)
    return wins

@app.delete("/api/approved-windows/{window_id}")
def delete_window(window_id: int, request: Request):
    _check_session(request)
    wins = get_approved_windows()
    target = next((w for w in wins if w['id'] == window_id), None)
    tk = target.get('token', '?') if target else '?'
    gw = target.get('target_ip', '?') if target else '?'
    delete_approved_window(window_id)
    record_audit_event("window_deleted", gw, details=f"Window #{window_id} token={tk} deleted")
    return {"status": "ok"}

@app.post("/api/approved-windows/{window_id}/toggle")
def toggle_window(window_id: int, request: Request):
    _check_session(request)
    wins = get_approved_windows()
    target = next((w for w in wins if w['id'] == window_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="Window not found")
    if target.get('status') in ('pending_review', 'denied'):
        raise HTTPException(status_code=400, detail="Cannot toggle a window in 'pending_review' or 'denied' status")
    new_state = not target['enabled']
    toggle_approved_window(window_id, new_state)
    record_audit_event("window_toggled", target.get('target_ip'),
                       details=f"Window #{window_id} {'enabled' if new_state else 'disabled'}")
    return {"status": "ok", "id": window_id, "enabled": new_state}

@app.post("/api/approved-windows/execute/{token}")
def window_execute(token: str, request: Request):
    """Called by the gateway to increment execution count when a window token is used.
    Returns 200 if the token is valid and within its window, 404 otherwise.
    Logs the claim as a request row for visibility in the main queue."""
    token_ip, _ = _resolve_gateway_token(request)
    if not token_ip:
        raise HTTPException(status_code=401, detail="Gateway token required for window execution")
    wins = get_approved_windows()
    target = next((w for w in wins if w['token'] == token), None)
    if not target:
        raise HTTPException(status_code=404, detail="Invalid or expired window token")
    if token_ip != target['target_ip']:
        raise HTTPException(status_code=403, detail="Gateway IP does not match window target")
    ok = increment_window_execution(token)
    if not ok:
        # Log the rejection server-side (token exists but window disabled/exhausted/expired)
        if target:
            create_request(target['target_ip'], target['command'], status='window-rejected', ttl=0, reason='exhausted or disabled')
            record_window_execution(target['id'], token, target['target_ip'], target['command'], 0, 'exhausted or disabled')
        raise HTTPException(status_code=404, detail="Invalid or expired window token")
    # Log as a request row for queue visibility
    if target:
        create_request(target['target_ip'], target['command'], status='window-approved', ttl=0)
        record_audit_event("window_claimed", target['target_ip'],
                           details=f"Token {token} claimed for '{target['command'][:60]}'")
        record_window_execution(target['id'], token, target['target_ip'], target['command'])
    return {"status": "ok", "token": token}

@app.get("/api/approved-windows/recent-jit")
def recent_jit(request: Request, hours: int = 6, ip: str = None):
    _check_session(request)
    """Get recently JIT-approved commands for the window creation wizard.
    Optional ?ip= filter to show only commands for a specific gateway."""
    return get_recent_jit_approved(hours, ip=ip)


@app.get("/api/window-by-token/{token}")
def lookup_window_by_token(token: str, request: Request):
    """Public — token is the auth. Returns full window parameters for
    operator-created windows so the AI agent can configure cron/scheduling
    without having to be told the parameters separately."""
    client_ip = request.client.host if request.client else "127.0.0.1"
    _check_rate_limit(client_ip)
    wins = get_approved_windows()
    target = next((w for w in wins if w['token'] == token), None)
    if not target:
        raise HTTPException(status_code=404, detail="Invalid window token")
    return {
        "token": token,
        "command": target['command'],
        "target_ip": target['target_ip'],
        "match_type": target.get('match_type', 'exact'),
        "window_start": target.get('window_start', 0),
        "window_end": target.get('window_end', 0),
        "days_of_week": target.get('days_of_week', 0),
        "execution_time": target.get('execution_time', 0),
        "expires_at": target.get('expires_at', None),
        "max_executions": target.get('max_executions', 1),
        "execution_count": target.get('execution_count', 0),
        "enabled": bool(target.get('enabled', 1)),
        "label": target.get('label', ''),
        "status": target.get('status', 'active'),
        "created_at": target.get('created_at', 0),
    }


# ── AI-Initiated Window Requests ────────────────────────────────────────

class WindowRequestPayload(BaseModel):
    gateway_ip: str
    command: str
    days_of_week: int = 0
    execution_time: int = 0
    expires_at: Optional[int] = None
    match_type: str = "exact"
    max_executions: int = 0
    label: str = ""
    window_start: int = 0

@app.post("/api/window-requests")
def submit_window_request(payload: WindowRequestPayload, request: Request):
    # Resolve gateway identity from X-Gateway-Token (same pattern as /api/request)
    token_ip, _ = _resolve_gateway_token(request)
    gw_ip = token_ip if token_ip else payload.gateway_ip
    if token_ip and token_ip != payload.gateway_ip:
        raise HTTPException(status_code=401, detail="Gateway token does not match self-reported gateway_ip")
    _check_rate_limit(gw_ip)
    # Single-use windows require a start time; immediate one-offs use JIT
    dow_req = payload.days_of_week or 0
    et_req = payload.execution_time or 0
    if dow_req == 0 and et_req == 0 and not payload.window_start:
        raise HTTPException(status_code=400, detail="Single-use windows require a start time (window_start). For an immediate one-off command, use standard JIT approval instead.")
    result = create_window_request(
        payload.gateway_ip, payload.command,
        payload.days_of_week, payload.execution_time,
        payload.expires_at, payload.match_type,
        payload.max_executions, payload.label,
        payload.window_start
    )
    record_audit_event("window_requested", payload.gateway_ip,
                       details=f"AI requested window for '{payload.command[:60]}'")
    send_notify('window', '🪟 Window Request', f'`{payload.command[:80]}` on {payload.gateway_ip}' +
                (f' — {payload.label}' if payload.label else ''))
    return {"id": result.get("id"), "retrieval_key": result.get("retrieval_key"), "status": "pending_review"}


@app.get("/api/window-requests/pending")
def list_pending_window_requests(request: Request):
    _check_session(request)
    """Must be registered BEFORE {request_id} so FastAPI routes this match first."""
    return get_pending_window_requests()


@app.get("/api/window-requests/{request_id}")
def check_window_request(request_id: str, request: Request):
    """Poll a window request — open to agents (read-only). Agents pass the opaque
    retrieval_key returned by POST /api/window-requests; the numeric id is only
    honoured for session-authed callers (the UI). Returns the status and, once
    approved, the window token."""
    _check_rate_limit(request.client.host if request.client else "127.0.0.1")
    authed = _check_session_optional(request)
    # Pending requests: key lookup for agents; numeric id only for the authed UI.
    wr = get_window_request_by_key(request_id) if request_id and not request_id.isdigit() else None
    if not wr and authed and request_id.isdigit():
        wr = get_window_request(int(request_id))
    if wr:
        return {"status": wr.get("status") or "pending_review"}
    # Already approved/denied — fetch from the main table
    conn = get_db()
    cursor = conn.cursor()
    if request_id.isdigit() and authed:
        cursor.execute("SELECT id, token, status, target_ip FROM approved_windows WHERE id = ?", (int(request_id),))
    else:
        cursor.execute("SELECT id, token, status, target_ip FROM approved_windows WHERE retrieval_key = ?", (request_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Window request not found")
    if row["status"] == "active":
        return {"status": "approved", "token": row["token"]}
    if row["status"] == "denied":
        return {"status": "denied", "message": "Window request was denied by operator"}
    return {"status": row["status"]}
    return {"status": "pending_review"}
@app.post("/api/window-requests/{request_id}/approve")
def approve_window(request_id: int, request: Request):
    _check_session(request)
    wr = get_window_request(request_id)
    if not wr:
        raise HTTPException(status_code=404, detail="Window request not found or already handled")
    result = approve_window_request(request_id)
    if not result:
        raise HTTPException(status_code=500, detail="Failed to approve window request")
    record_audit_event("window_request_approved", wr.get("target_ip"),
                       details=f"AI window request #{request_id} approved, token={result.get('token','')}")
    return {"status": "approved", "token": result.get("token", "")}

@app.post("/api/window-requests/{request_id}/deny")
def deny_window_request(request_id: int, request: Request):
    _check_session(request)
    wr = get_window_request(request_id)
    if not wr:
        raise HTTPException(status_code=404, detail="Window request not found or already handled")
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE approved_windows SET status = 'denied' WHERE id = ?", (request_id,))
    conn.commit()
    conn.close()
    record_audit_event("window_request_denied", wr.get("target_ip"),
                       details=f"AI window request #{request_id} denied")
    return {"status": "ok"}


# ── External Notifications Config ──────────────────────────────────────

class NotifyConfigPayload(BaseModel):
    url: str
    events: str
    dashboard_url: str = ''

@app.get("/api/notify-config")
def get_notify_config_endpoint(request: Request):
    _check_session(request)
    return get_notify_config()

@app.put("/api/notify-config")
def set_notify_config_endpoint(payload: NotifyConfigPayload, request: Request):
    _check_session(request)
    set_notify_config(payload.url, payload.events, payload.dashboard_url)
    record_audit_event("notify_config_updated", "dashboard",
                       details=f"Webhook URL {'set' if payload.url else 'cleared'}, events: {payload.events}")
    return {"status": "ok"}

@app.post("/api/notify-test")
def test_notify_endpoint(request: Request):
    _check_session(request)
    ok = send_notify('test', '🧪 Eshu Notification Test', 'This is a test notification from your Eshu Gateway dashboard.')
    return {"status": "ok", "delivered": ok}


# ── Dev Tools Settings ──────────────────────────────────────────────────

class DevToolsPayload(BaseModel):
    enabled: bool = False

@app.get("/api/settings/dev-tools")
def get_dev_tools_endpoint(request: Request):
    _check_session(request)
    return {"enabled": get_dev_tools_enabled()}

@app.put("/api/settings/dev-tools")
def set_dev_tools_endpoint(payload: DevToolsPayload, request: Request):
    _check_session(request)
    set_dev_tools_enabled(payload.enabled)
    record_audit_event("dev_tools_toggled", details=f"Development tools {'enabled' if payload.enabled else 'disabled'}")
    return {"status": "ok", "enabled": payload.enabled}


@app.get("/api/approved-windows/{window_id}")
def get_single_window(window_id: str, request: Request):
    """Single-window details — open to agents (read-only); includes the token.
    Agents pass the opaque retrieval_key; the numeric id is only honoured for
    session-authed callers (the UI)."""
    _check_rate_limit(request.client.host if request.client else "127.0.0.1")
    authed = _check_session_optional(request)
    if window_id.isdigit() and authed:
        win = get_approved_window_by_id(int(window_id))
    else:
        win = get_approved_window_by_key(window_id)
    if not win:
        raise HTTPException(status_code=404, detail="Window not found")
    return win

@app.get("/api/approved-windows/{window_id}/executions")
def list_window_executions(window_id: str, request: Request):
    """Get the last N executions of a window token (for usage history view).
    Accepts the opaque retrieval_key; the numeric id is only honoured for
    session-authed callers (the UI)."""
    _check_rate_limit(request.client.host if request.client else "127.0.0.1")
    authed = _check_session_optional(request)
    if window_id.isdigit() and authed:
        return get_window_executions(int(window_id), limit=50)
    win = get_approved_window_by_key(window_id)
    if not win:
        raise HTTPException(status_code=404, detail="Window not found")
    return get_window_executions(win['id'], limit=50)

# ── Integrations & MCP (agent API gateway) ─────────────────────────────

class IntegrationPayload(BaseModel):
    name: str
    base_url: str
    auth_type: str = 'bearer'
    auth_header_name: str = ''
    secret: str = ''
    enabled: bool = True
    kind: str = 'custom'

class IntegrationUpdatePayload(BaseModel):
    base_url: str = None
    auth_type: str = None
    auth_header_name: str = None
    secret: str = None
    enabled: bool = None
    kind: str = None

class ToolPayload(BaseModel):
    name: str
    description: str = ''
    method: str = 'GET'
    path_template: str = ''
    params: list = []
    example: str = ''
    read_only: bool = True

class ToolTogglePayload(BaseModel):
    enabled: bool = True

class AgentTokenPayload(BaseModel):
    name: str


@app.post("/api/agents")
def create_agent(payload: AgentTokenPayload, request: Request):
    """Mint a new agent token (shown once). Agents present it as a bearer token
    to /mcp. The dashboard stores only its SHA-256 hash."""
    _check_session(request)
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    token, agent_id = create_agent_token(name)
    record_audit_event("agent_token_created", details=f"Agent token '{name}' created (id {agent_id})")
    return {"status": "ok", "id": agent_id, "name": name, "token": token}


@app.get("/api/agents")
def list_agents(request: Request):
    _check_session(request)
    return get_agent_tokens()


@app.delete("/api/agents/{agent_id}")
def delete_agent(agent_id: int, request: Request):
    _check_session(request)
    if not delete_agent_token(agent_id):
        raise HTTPException(status_code=404, detail="Agent token not found")
    record_audit_event("agent_token_deleted", details=f"Agent token {agent_id} deleted")
    return {"status": "ok"}


@app.post("/api/integrations")
def create_integration_endpoint(payload: IntegrationPayload, request: Request):
    _check_session(request)
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    if get_integration(name):
        raise HTTPException(status_code=400, detail="Integration already exists")
    if payload.auth_type not in ('none', 'bearer', 'basic', 'header'):
        raise HTTPException(status_code=400, detail="Invalid auth_type")
    create_integration(name, payload.base_url.strip(), payload.auth_type, payload.secret,
                       payload.auth_header_name, payload.enabled, payload.kind)
    record_audit_event("integration_created", details=f"Integration '{name}' created (kind {payload.kind})")
    return {"status": "ok", "name": name}


@app.get("/api/integrations")
def list_integrations_endpoint(request: Request):
    _check_session(request)
    return get_integrations()


@app.put("/api/integrations/{name}")
def update_integration_endpoint(name: str, payload: IntegrationUpdatePayload, request: Request):
    _check_session(request)
    if not get_integration(name):
        raise HTTPException(status_code=404, detail="Integration not found")
    data = payload.model_dump(exclude_none=True)
    if 'auth_type' in data and data['auth_type'] not in ('none', 'bearer', 'basic', 'header'):
        raise HTTPException(status_code=400, detail="Invalid auth_type")
    update_integration(name, **data)
    record_audit_event("integration_updated", details=f"Integration '{name}' updated")
    return {"status": "ok"}


@app.post("/api/integrations/{name}/test")
def test_integration_endpoint(name: str, request: Request):
    """Test a connection: run the first enabled read-only tool with no required
    params and return status + a response preview. Surfaces wrong base URLs,
    bad secrets, and TLS issues without involving the agent."""
    _check_session(request)
    integration = get_integration(name)
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
    candidate = None
    for tool in get_tools(integration['id']):
        if not tool.get('enabled') or not tool.get('read_only'):
            continue
        if not any(p.get('required') for p in (tool.get('params') or [])):
            candidate = tool
            break
    if not candidate:
        raise HTTPException(status_code=400,
                            detail="No enabled read-only tool with zero required params — seed the integration first")
    result = execute_integration_call(integration, candidate, {}, agent='test')
    return {
        "status_code": result['status_code'],
        "error": result['error'],
        "tool": candidate['name'],
        "preview": (result['body'] or '')[:500],
        "truncated": result['truncated'],
    }


@app.delete("/api/integrations/{name}")
def delete_integration_endpoint(name: str, request: Request):
    _check_session(request)
    if not delete_integration(name):
        raise HTTPException(status_code=404, detail="Integration not found")
    refresh_mcp_tools()
    record_audit_event("integration_deleted", details=f"Integration '{name}' deleted")
    return {"status": "ok"}


@app.get("/api/integrations/{name}/tools")
def list_tools_endpoint(name: str, request: Request):
    _check_session(request)
    integration = get_integration(name)
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
    return get_tools(integration['id'])


@app.post("/api/integrations/{name}/tools")
def create_tool_endpoint(name: str, payload: ToolPayload, request: Request):
    _check_session(request)
    integration = get_integration(name)
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
    tool_id = create_tool(integration['id'], payload.name, payload.description, payload.method,
                          payload.path_template, payload.params, payload.example, payload.read_only)
    refresh_mcp_tools()
    record_audit_event("integration_tool_created", details=f"Tool '{name}.{payload.name}' created")
    return {"status": "ok", "id": tool_id}


@app.delete("/api/integrations/{name}/tools/{tool_id}")
def delete_tool_endpoint(name: str, tool_id: int, request: Request):
    _check_session(request)
    if not delete_tool(tool_id):
        raise HTTPException(status_code=404, detail="Tool not found")
    refresh_mcp_tools()
    record_audit_event("integration_tool_deleted", details=f"Tool id {tool_id} deleted from '{name}'")
    return {"status": "ok"}


@app.post("/api/integrations/{name}/tools/{tool_id}/toggle")
def toggle_tool_endpoint(name: str, tool_id: int, payload: ToolTogglePayload, request: Request):
    _check_session(request)
    if not set_tool_enabled(tool_id, payload.enabled):
        raise HTTPException(status_code=404, detail="Tool not found")
    refresh_mcp_tools()
    record_audit_event("integration_tool_toggled",
                       details=f"Tool id {tool_id} {'enabled' if payload.enabled else 'disabled'}")
    return {"status": "ok", "enabled": payload.enabled}


@app.post("/api/integrations/{name}/seed")
def seed_integration_endpoint(name: str, request: Request):
    """Populate an integration with the curated seed catalog for its kind
    (proxmox, ha, ...). Idempotent — re-seeding updates tools in place."""
    _check_session(request)
    integration = get_integration(name)
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
    kind = integration.get('kind') or 'custom'
    if kind == 'proxmox':
        created, updated = seed_proxmox_tools(integration['id'])
    elif kind == 'ha':
        from core.ha_seed import seed_ha_tools
        created, updated = seed_ha_tools(integration['id'])
    else:
        raise HTTPException(status_code=400,
                            detail=f"No seed catalog for this integration type ('{kind}') yet")
    refresh_mcp_tools()
    record_audit_event(f"{kind}_seeded",
                       details=f"{kind} seed for '{name}': {created} created, {updated} updated")
    return {"status": "ok", "created": created, "updated": updated}


@app.get("/api/integration-calls")
def list_integration_calls(request: Request):
    _check_session(request)
    return get_integration_calls(200)


@app.get("/api/integration-calls/pending")
def list_pending_integration_calls(request: Request):
    _check_session(request)
    return get_pending_calls()


def _surface_integration_call(call, status: str):
    """Insert a requests row so a resolved mutating API call appears in the main
    dashboard history (mirrors the fleet-run pattern)."""
    args = ', '.join(f"{k}={v}" for k, v in (call['payload'] or {}).items())
    create_request(
        target_ip=call['integration'],
        command=f"{call['integration']}.{call['tool']}({args})",
        status=status,
        ttl=0,
        reason=call['reason'],
    )


@app.post("/api/integration-calls/{call_id}/approve")
def approve_integration_call(call_id: int, request: Request):
    """Approve a pending mutating call and execute it against the integration."""
    _check_session(request)
    call = get_pending_call(call_id)
    if not call or call['status'] != 'pending':
        raise HTTPException(status_code=404, detail="Pending call not found")
    integration = get_integration(call['integration'])
    tool = get_tool(call['integration'], call['tool'])
    if not integration or not tool:
        raise HTTPException(status_code=404, detail="Integration or tool missing")
    result = execute_integration_call(integration, tool, call['payload'], agent='operator')
    set_pending_call_status(call_id, 'approved', json.dumps(result))
    _surface_integration_call(call, 'integration-approved')
    record_audit_event("integration_call_approved",
                       details=f"Integration call #{call_id} ({call['integration']}.{call['tool']}) approved and executed")
    return {"status": "ok", "id": call_id}


@app.post("/api/integration-calls/{call_id}/deny")
def deny_integration_call(call_id: int, request: Request):
    _check_session(request)
    call = get_pending_call(call_id)
    if not call or call['status'] != 'pending':
        raise HTTPException(status_code=404, detail="Pending call not found")
    set_pending_call_status(call_id, 'denied', '')
    _surface_integration_call(call, 'integration-denied')
    record_audit_event("integration_call_denied",
                       details=f"Integration call #{call_id} ({call['integration']}.{call['tool']}) denied")
    return {"status": "ok", "id": call_id}


class MCPSettingsPayload(BaseModel):
    allowed_hosts: str = ''


@app.get("/api/mcp-settings")
def get_mcp_settings_endpoint(request: Request):
    """The configured MCP Host allowlist (DNS-rebinding protection)."""
    _check_session(request)
    return {"allowed_hosts": get_mcp_allowed_hosts()}


@app.put("/api/mcp-settings")
def set_mcp_settings_endpoint(payload: MCPSettingsPayload, request: Request):
    """Set the MCP Host allowlist. Applied live — no restart needed."""
    _check_session(request)
    set_mcp_allowed_hosts(payload.allowed_hosts.strip())
    refresh_mcp_allowed_hosts()
    record_audit_event("mcp_settings_updated",
                       details=f"MCP allowed hosts updated to '{payload.allowed_hosts.strip()}'")
    return {"status": "ok"}


# ── Static Files ────────────────────────────────────────────────────────

static_dir = os.path.join(os.path.dirname(__file__), "static")
if not os.path.exists(static_dir):
    os.makedirs(static_dir)

dev_dir = os.path.join(static_dir, "dev")
os.makedirs(dev_dir, exist_ok=True)

# Only create golden on first-ever startup; never overwrite
install_script_src = os.path.join(os.path.dirname(__file__), "eshu-gateway-install.sh")
if os.path.exists(install_script_src):
    import shutil
    golden_path = os.path.join(static_dir, "eshu-gateway-install.sh")
    if not os.path.exists(golden_path):
        shutil.copy(install_script_src, golden_path)
    dev_path = os.path.join(dev_dir, "eshu-gateway-install.sh")
    if not os.path.exists(dev_path):
        shutil.copy(install_script_src, dev_path)

app.mount("/static", StaticFiles(directory=static_dir), name="static")

# MCP surface — agents (Hermes) connect to http://<dashboard>:8000/mcp with a
# bearer agent token. Mounted at module level; tools are (re)registered at
# startup and after catalog changes via refresh_mcp_tools().
app.mount("/mcp", eshu_mcp.streamable_http_app(), name="mcp")

@app.get("/", response_class=HTMLResponse)
def read_root():
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Frontend not found</h1>"