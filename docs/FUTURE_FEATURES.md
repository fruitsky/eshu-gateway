# Future Feature Ideas

## Feature-flag delivery system

The flag → script → sync pipeline decouples feature delivery from core script updates.
Feature scripts are served from `dashboard/static/features/` and synced to
`/etc/eshu/features.d/` by the poller when the flag is enabled.

Each row below evaluates whether the feature fits this system.

| Feature | What it does | Fits flag → script? | Feasibility |
|---------|-------------|---------------------|-------------|
| **Custom per-gateway policies** | Override the global allow/block list per gateway via a flag that syncs a policy-override file. The gateway merges the override with the global list. | ✅ Yes | Medium. Core gateway script needs a new stage that loads per-gateway overrides. The override file is synced alongside feature scripts. |
| **Quota enforcement** | Max executions per command per hour/day, tracked locally on the gateway and reset each window. The quota file is synced by the poller. | ✅ Yes | Low. A small feature script reads the quota file and increments counters locally. Logs on the dashboard could show usage via the existing request queue. |
| **Execution confirmation** | For sensitive commands, the gateway pauses for N seconds and prints a confirmation prompt. The user must type CONFIRM or the command is rejected. Configurable in the feature script. | ✅ Yes | Low. Feature script intercepts `$cmd`, checks a "requires confirmation" list, and prompts. Falls through to normal execution if no confirmation needed. |
| **Rate limiting per command** | Limit how often commands like `rm`, `shutdown`, `poweroff` can run across all gateways in a time window. Tracked locally, synced state optional. | ✅ Yes | Low. Uses the same counter pattern as quota enforcement but scoped to a command family instead of per-gateway. |
| **Webhook on execution** | POST execution events (command, gateway, timestamp, result) to an external webhook URL. Configured via a feature script that fires after every approved execution. | ✅ Yes | Low. Feature script adds a `curl` call after `run_sanitized`. Webhook URL stored in a config file synced alongside the script. |
| **Environment variable injection** | Define approved environment variables that are injected into the executed command's environment. The allowed vars list is synced as a data file. | ✅ Yes | Low. Core gateway script already has an env sanitizer — extend it to also inject allowed vars from a synced file. |
| **Command argument validation** | For commands like `docker logs`, validate the arguments (e.g., `-n N` must be a positive integer). Multi-command modes like `&&` would only validate the first command. | ✅ Yes | Medium. Feature script adds an argument validation hook before execution. Requires careful handling of chained commands. |
| **Per-gateway active-hours** | Gateway only accepts commands during specific hours (e.g., `09:00–17:00 UTC`). Out-of-hours commands fall through to JIT. | ⚠️ Partially | Low — but overlaps with the Approved Windows system. Could be a simpler feature flag that checks current time vs a synced schedule file. |
| **Command chaining control** | Explicitly allow or block `&&`, `||`, `;`, `|` chaining operators. Currently handled by the core blocklist (some patterns blocked). A feature script could extend this. | ⚠️ Partially | Medium. Chaining happens before Stage 4.5, so the check would need to be in the core script or in a feature script sourced earlier. |
| **Advanced command parsing** | Smarter splitting of multi-command strings — proper `&&`/`||` splitting instead of the current regex, per-command argument validation, etc. | ❌ Core change | High. This is a core gateway script capability, not a feature script. Changing the parsing affects all stages. |
| **SSH session recording** | Record the terminal session (I/O) for compliance/audit. Requires `script(1)`, session multiplexing, and storage on the gateway or forwarding to a remote server. | ❌ Infrastructure | Very high. Needs system-level changes, storage, and potentially streaming infrastructure. Not a simple feature script. |
| **Two-factor for critical commands** | Require a second factor (TOTP code, approval from a second operator) for commands like `rm -rf /`, `poweroff`, etc. | ✅ Yes | Medium. The feature script could validate a TOTP code passed alongside the command. Requires TOTP secret storage on the dashboard and sync to gateways. |
