# Feature Roadmap

## Phase 2: MCP / API Session IDs

**Status:** Planned (after SSH session-id is verified in production)

The SSH `ESHU_SESSION_ID` prefix mechanism is implemented and tested. The MCP/API path needs a structured equivalent.

### What exists today
- SSH: agent prefixes commands with `ESHU_SESSION_ID=<id>` — gateway parses it before policy eval
- Dashboard: groups pending requests by `session_id`, shows session history
- Backend: `session_id` column on `requests` table, populated from gateway payload

### What Phase 2 adds
- MCP tool calls accept `session_id` and `execution_id` as structured JSON fields (no string parsing needed)
- Agent authenticates via bearer token; `session_id` arrives in the tool-call payload
- The dashboard's session grouping already works for both SSH and API requests — no frontend changes needed

### MCP payload shape
```json
{
  "tool": "proxmox_get_vm_status",
  "session_id": "cf0f09869854",
  "execution_id": "20260829_201145_02deab",
  "arguments": { "node": "pve", "vmid": 109 }
}
```

### Implementation steps
1. Add `session_id` / `execution_id` fields to the MCP tool-call schema
2. Thread them into the `GatewayPayload` on the dashboard side (already supports these fields)
3. The `create_request()` call already stores `session_id` — no DB changes needed
4. Test: MCP tool call with `session_id` → appears grouped in the same session as SSH commands from the same conversation

### Notes
- The `execution_id` is per-run (which subagent ran this) — enables drill-down within a session
- `session_id = "unknown"` remains valid for context-less cron/fire-and-forget jobs
