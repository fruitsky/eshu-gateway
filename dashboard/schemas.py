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

class RegisterPayload(BaseModel):
    ip: str
    hostname: str
    version: str = "v6.0"

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

class NotifyConfigPayload(BaseModel):
    url: str
    events: str
