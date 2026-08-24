# database.py — re-export wrapper for domain-split modules
# All functions are implemented in db/ modules.
# This file exists so existing imports (from database import ...) keep working.

from db.core import DB_PATH, get_db, init_db

from db.requests import (
    create_request,
    update_request_status,
    update_ticket_consumed_by_ip,
    get_all_requests,
    get_pending_request_by_cmd,
    get_request_status,
    get_request_command,
    count_denied,
    get_ticket_by_request_id,
    delete_old_requests,
    search_requests,
)

from db.gateways import (
    register_gateway,
    get_gateways,
    update_gateway_last_seen,
    update_gateway_policy_version,
    update_gateway_policy_sync,
    update_gateway_last_updated,
    deregister_gateway,
    get_gateway_token,
    set_gateway_token,
    get_gateway_by_token,
    set_trigger_uninstall,
    check_trigger_uninstall,
    clear_trigger_uninstall,
    set_uninstall_progress,
    get_uninstall_progress,
    clear_uninstall_progress,
    get_trigger_dev_update,
    set_trigger_dev_update, clear_trigger_dev_update,
    get_trigger_update_version,
    set_trigger_update_version,
    get_trigger_rollback,
    set_trigger_rollback,
    clear_trigger_rollback,
    set_trigger_freeze,
    get_trigger_freeze,
    clear_trigger_freeze,
    get_gateway_mode,
    set_gateway_mode,
    get_gateway_zero_trust,
    set_gateway_zero_trust,
    get_dev_gateways,
    update_gateway_windows_count,
    update_gateway_heartbeat,
    set_override,
    clear_override,
    get_override_active,
)

from db.policies import (
    get_policies,
    update_policy,
    get_policy_version,
    increment_policy_version,
    get_policy_updated_at,
    set_policy_updated_at,
    record_policy_change,
    get_policy_changes,
    get_policy_change,
    seed_core_blocklist_if_needed,
)

from db.enrollment import (
    get_ssh_keys,
    save_ssh_keys,
    generate_enrollment_token,
    validate_enrollment_token,
)

from db.audit import (
    record_audit_event,
    get_audit_log,
    search_audit_log,
)

from db.auth import (
    get_password_hash,
    set_password_hash,
)

from db.misc import (
    get_note,
    update_note,
    get_feature_flags,
    set_feature_flag, set_feature_flag_scope,
    get_notify_config,
    set_notify_config,
    get_dev_tools_enabled,
    set_dev_tools_enabled,
    get_deployed_golden_hash,
    set_deployed_golden_hash,
    get_dev_push_initiated, set_dev_push_initiated, clear_dev_push_initiated,
    dismiss_policy_gap,
    get_dismissed_policy_gaps,
    get_seen_gaps,
    set_seen_gaps,
    get_mcp_allowed_hosts,
    set_mcp_allowed_hosts,
)

from db.windows import (
    create_approved_window,
    update_approved_window,
    get_approved_windows,
    get_active_approved_windows,
    delete_approved_window,
    toggle_approved_window,
    increment_window_execution,
    get_recent_jit_approved,
    create_window_request,
    get_window_request,
    get_window_request_by_key,
    get_approved_window_by_id,
    get_approved_window_by_key,
    approve_window_request,
    get_pending_window_requests,
    record_window_execution,
    get_window_executions,
)

from db.fleet import (
    create_fleet_command,
    get_fleet_commands,
    get_fleet_command,
    set_fleet_status,
    approve_fleet_command,
    upsert_fleet_result,
    get_fleet_result,
    get_fleet_results,
    get_injectable_fleet_cmd,
    purge_old_fleet_commands,
    delete_fleet_command,
)

from db.integrations import (
    create_integration,
    get_integrations,
    get_integration,
    get_integration_by_id,
    update_integration,
    delete_integration,
    create_tool,
    get_tools,
    get_enabled_tools,
    get_tool,
    set_tool_enabled,
    set_all_tools_enabled,
    update_tool,
    delete_tool,
    record_integration_call,
    get_integration_calls,
    create_pending_call,
    get_pending_calls,
    get_pending_call,
    set_pending_call_status,
    mask_sensitive_args,
    strip_resolved_payloads,
)

from db.agent_tokens import (
    create_agent_token,
    get_agent_tokens,
    get_agent_by_token,
    touch_agent_token,
    revoke_agent_token,
    delete_agent_token,
)
