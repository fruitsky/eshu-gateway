from typing import Optional
from pydantic import BaseModel

class LoginPayload(BaseModel):
    password: str

class SetPasswordPayload(BaseModel):
    password: str

class GatewayPayload(BaseModel):
    target_ip: str
    encoded_command: str
    status: str = "pending"
    reason: str = ""
    token: str = ""
    session_id: str = ""
    execution_id: str = ""
    # F12: attribution and gating for an MCP-run command. Both are validated
    # again server-side; a direct-SSH gateway sends neither.
    agent: str = ""
    force_jit: bool = False

class RegisterPayload(BaseModel):
    ip: str
    hostname: str
    version: str = "v6.0"
    # F12: sent by the same installer that writes the gateway and poller, so
    # the pair is always the pair that is installed. All optional: an old
    # installer reports nothing and the gateway is refused ssh_exec.
    ssh_host_key: str = ""
    ssh_agent_format: str = ""
    ssh_ticket_format: str = ""

class SSHHostKeyPayload(BaseModel):
    ssh_host_key: str = ""

class PolicyPayload(BaseModel):
    type: str
    content: str

class NotePayload(BaseModel):
    content: str

class SSHKeysPayload(BaseModel):
    eshu_key: str

class HeartbeatPayload(BaseModel):
    ip: str
    hostname: str
    poller_ok: int = 0
    gateway_ok: int = 0
    can_reach: int = 0
    ssh_host_key: str = ""

class UninstallProgressPayload(BaseModel):
    ip: str
    step: str
    message: str = ""

class FeatureFlagTogglePayload(BaseModel):
    enabled: bool

class GatewayModePayload(BaseModel):
    mode: str

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

class WindowRequestPayload(BaseModel):
    gateway_ip: str
    command: str
    days_of_week: int = 0
    execution_time: int = 0
    expires_at: int = None
    match_type: str = "exact"
    max_executions: int = 0
    label: str = ""
    window_start: int = 0

class ExcludeRequestsPayload(BaseModel):
    """F8: exclude by explicit ids and/or a whole session."""
    ids: list[int] = []
    session_id: str = ""


class InventorySettingsPayload(BaseModel):
    """F14: how often the inventory refreshes (clamped to 5-120 minutes)."""
    interval_minutes: int = 15

class ConclusionReviewPayload(BaseModel):
    note: str = ""

class ConclusionEditPayload(BaseModel):
    summary: Optional[str] = None
    root_cause: Optional[str] = None
    fix: Optional[str] = None
    note: str = ""

class RunbookPromotePayload(BaseModel):
    title: str = ""
    when_to_use: str = ""


class RunbookRetirePayload(BaseModel):
    """X2: one flag. Retiring is reversible, so there is no reason to delete."""
    retired: bool = True

class NotifyConfigPayload(BaseModel):
    url: str
    events: str
