# Architecture

This page holds the technical reference for Eshu Gateway — the deployment
layout, the policy pipeline, and the gateway lifecycle. For setup instructions
see the [README](../README.md); for the developer workflow see
[DEV_GUIDE.md](DEV_GUIDE.md).

## Deployment layout

```mermaid
flowchart TB
    subgraph operator["🖥️ Operator / AI Agent"]
        AGENT_SSH["SSH as eshu-gateway
        ssh eshu-gateway@node 'uptime'"]
        BROWSER["🌐 Browser
        Dashboard UI"]
    end

    subgraph dashboard["🖥️ Dashboard Server"]
        direction TB
        FASTAPI["FastAPI + Uvicorn
        :8000"]
        SQLITE[("SQLite
        eshu.db")]
        FASTAPI <--> SQLITE
    end

    subgraph gateway["🖥️ Gateway Node"]
        direction TB
        SSHD["sshd
        command= restriction"]
        GATEWAY_SCRIPT["eshu-gateway.sh
        6-stage policy engine"]
         POLLER["eshu-poller.service
         every 30s: sync policies + tickets + feature scripts"]
         TICKETS["Ticket lockbox
         /var/run/eshu.tickets"]
         LOGGER["eshu-logger.service
         every 30s: health heartbeat"]

        SSHD --> GATEWAY_SCRIPT
        GATEWAY_SCRIPT -- "JIT request" --> FASTAPI
        POLLER -- "sync" --> FASTAPI
    end

    BROWSER -- "HTTP :8000" --> FASTAPI
    AGENT_SSH --> SSHD
```

<details>
<summary>JIT Approval Sequence Diagram</summary>

```mermaid
sequenceDiagram
    actor Op as 👤 Operator / AI Agent
    participant GW as eshu-gateway.sh
    participant API as Dashboard API
    participant UI as Dashboard UI
    participant Poll as eshu-poller
    participant Lock as /var/run/eshu.tickets

    Op->>GW: ssh eshu-gateway@node 'cmd'
    GW-->>GW: 6-stage check → no match → JIT required
    GW->>API: POST /api/request
    API-->>GW: #000042 pending
    UI->>API: poll for pending requests
    Note over UI: 👤 Clicks Approve
    UI->>API: POST /api/approve/42
    Poll->>API: GET /api/poll/{ip}
    API-->>Poll: {ticket}
    Poll->>Lock: deposit ticket
    GW->>Lock: claim & burn
    GW->>Op: execute command → output
```
</details>

## Policy Pipeline

Every command passes through these stages in order:

| Stage | Rule Set | Match → Action |
|:-----:|----------|---------------|
| **0** | **Emergency Freeze** (global circuit breaker) — when the operator freezes the fleet, every gateway rejects all commands until unfrozen | **FATAL — rejected while frozen** |
| **1** | Hardcoded catastrophic blocklist (`rm -rf`, `mkfs`, `dd`, `iptables -F`, `reboot`, Eshu self-access, etc.) | **FATAL — blocked permanently** |
| **2** | Dashboard-managed blacklist (`/etc/eshu-rblack.txt`) | **Blocked** |
| **3** | Exact whitelist (`/etc/eshu-exact.txt`) | **Auto-approved** |
| **4** | Regex whitelist (`/etc/eshu-rwhite.txt`) | **Auto-approved** |
| **4.5** | Feature scripts (`/etc/eshu/features.d/*.sh`) — loaded by poller when feature flags are enabled | **Auto-approved by window token** |
| **5** | Claim-and-burn lockbox (`/var/run/eshu.tickets`) | **Execute & consume ticket** |
| **6** | JIT human approval (90s auto-poll) | **Execute on approval** |

The hardcoded blocklist is baked into the gateway script and **cannot be
changed from the dashboard** — even a compromised dashboard cannot unblock
catastrophic commands.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Dashboard Backend | Python 3 + FastAPI + Uvicorn |
| Dashboard Frontend | Vanilla HTML/CSS/JS + Tailwind CSS CDN |
| Database | SQLite (`eshu.db`) |
| Gateway Agent | Bash (POSIX) |
| Gateway Poller | Bash + systemd service |
| Installer | Single-file Bash script |
| Auth | PBKDF2-SHA256 session cookies |

## Gateway nodes

Requirements: Linux, `bash`, `curl`, `python3`, `systemd`, `sshd`.  
Network: Gateway must reach the dashboard on port 8000 (HTTP). No inbound
ports needed.

Installed components:

| Path | Purpose |
|------|---------|
| `/usr/local/bin/eshu-gateway.sh` | SSH-triggered command executor |
| `/usr/local/bin/eshu-poller.sh` | Background policy + ticket + feature sync loop |
| `/usr/local/bin/eshu-logger.sh` | Health heartbeat reporter (30s) |
| `/etc/systemd/system/eshu-poller.service` | Poller systemd unit |
| `/etc/systemd/system/eshu-logger.service` | Logger systemd unit |
| `/etc/eshu-*.txt` | Synced policy files |
| `/etc/eshu/features.d/*.sh` | Feature scripts loaded at runtime |
| `/var/run/eshu.tickets` | JIT ticket lockbox (tmpfs, root-only) |
| `/etc/sudoers.d/eshu-gateway` | Passwordless sudo for the gateway user |

## Gateway lifecycle

- **Updates:** Gateway updates flow through a **Build → Edge → Fleet** pipeline
  managed in the dashboard's Development & Deployment section. The current Build
  is the latest installer; Edge is the dev channel for dev-mode gateways; Deploy
  to Fleet pushes it to all production gateways. That section is hidden by
  default behind the **"Show development tools"** toggle in Settings
  (`dev_tools_enabled`), so non-dev operators don't see the pipeline.
- **Uninstall:** Clicking the uninstall action spawns a transient `systemd-run`
  service on the gateway that cleans up all binaries, services, users, sudoers,
  and SSH keys — then deregisters from the dashboard with live progress
  tracking.
