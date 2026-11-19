"""Seed a throwaway database for the browser view check.

Synthetic data only: 192.0.2.x addresses and *.example.test names. The check
runs against a real dashboard (tests/ui/serve_ui.py) because the layout bugs
it guards live in the fetched lists, not in the static markup.
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "dashboard"))

import db.core as core  # noqa: E402

core.DB_PATH = os.environ["ESHU_UI_DB"]
core.init_db()

from db import (agent_policies, agent_tokens, conclusions, gateways,  # noqa: E402
                integrations, inventory, policies, requests, session_plans,
                sessions, web_fetch)
from db import auth as db_auth  # noqa: E402
from core.utils import _hash_password  # noqa: E402
from core import allowances as allowance_core  # noqa: E402
from core import ha_devices  # noqa: E402
from db.core import db_conn  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "tests", "ui"))
import synthetic_ha  # noqa: E402

ha_devices.clear_cache()
allowance_core._ha_registry = synthetic_ha.registry
allowance_core._risky_classes = synthetic_ha.risky_classes

now = int(time.time())
db_auth.set_password_hash(_hash_password("synthetic-ui-pass"))

for ip, host, seen in (("192.0.2.10", "web01.example.test", now - 20),
                       ("192.0.2.11", "db01.example.test", now - 120),
                       ("192.0.2.12", "pve.example.test", now - 600)):
    gateways.register_gateway(ip, host, "v6.0.0")
    with db_conn() as conn:
        conn.execute("""UPDATE gateways SET last_seen=?, first_seen=?, last_updated=?,
            last_heartbeat=?, heartbeat_poller_ok=1, heartbeat_gateway_ok=1,
            heartbeat_can_reach=1, run_limits='v1', ssh_mode='direct+mcp'
            WHERE ip=?""",
                     (seen, str(now - 30 * 86400), seen, seen, ip))
        conn.commit()

agents = {}
for name in ("ops-agent", "db-agent"):
    _raw, aid = agent_tokens.create_agent_token(name)
    agents[name] = aid
agent_policies.set_agent_policy(
    agents["ops-agent"], rate_limit_per_min=30,
    arg_policy={"sonarr_series": {"status": ["continuing", "ended"]}},
    ssh_hosts_allow=["192.0.2.10", "192.0.2.12"])
agent_policies.set_agent_policy(agents["db-agent"], tools_mode="allowlist",
                                tools=["whereis"], ssh_hosts_allow=["*"])

sessions.upsert_session("sess-alpha", "ops-agent",
                        "Investigate the 502s on web01 after the deploy",
                        seen_at=now - 60)
sessions.upsert_session("sess-beta", "db-agent", "Replication lag on db01",
                        seen_at=now - 3600)

pending = requests.create_request("192.0.2.10", "systemctl restart nginx",
                                  ttl=1800, session_id="sess-alpha",
                                  agent="ops-agent")
requests.create_request("192.0.2.11", "systemctl status postgresql", ttl=1800,
                        session_id="sess-beta", agent="db-agent")
ran = requests.create_request("192.0.2.10", "uptime", ttl=90,
                              session_id="sess-alpha", agent="ops-agent")
with db_conn() as conn:
    conn.execute("UPDATE requests SET status='consumed' WHERE id=?", (ran,))
    conn.execute("""INSERT INTO request_results
        (request_id, exit_code, duration_ms, output, truncated, created_at)
        VALUES (?, 0, 120, ' 10:42 up 4 days', 0, ?)""", (ran, now - 300))
    conn.commit()

plan = session_plans.create_plan("sess-alpha", [
    {"host": "192.0.2.10", "command": "nginx -t", "max_uses": 1},
    {"host": "192.0.2.10", "command": "systemctl reload nginx", "max_uses": 2,
     "check": {"kind": "ssh", "command": "uptime", "wait_s": 5, "retries": 1}},
], note="Reload after the config change")

draft = conclusions.upsert_draft("sess-alpha", "ops-agent",
                                 "Investigate the 502s on web01", {
    "title": "Nginx 502s after the deploy", "outcome": "partial",
    "summary": "The upstream app was restarting; nginx returned 502 for 40 s.",
    "worked": ["the app came back by itself"],
    "did_not_work": ["the deploy waits for readiness"],
    "root_cause": "no readiness gate", "fix": "add a readiness gate",
    "tags": ["nginx", "deploy"], "runbook_candidate": True})
confirmed = conclusions.confirm_conclusion(draft["id"], by="operator")
conclusions.create_runbook(confirmed["id"], "Rotate the TLS certificate",
                           "The certificate is near expiry.",
                           [{"kind": "command", "command": "nginx -t",
                             "host": "web01.example.test", "note": "check"}],
                           [{"note": "restart drops connections", "ref": "#12"}],
                           tags=["tls"], by="operator")

from core.seeds import seed_for_kind  # noqa: E402
from db.integrations import set_integration_config  # noqa: E402

INTEGRATIONS = [
    ("Home Assistant", "http://192.0.2.30:8123", "bearer", "ha"),
    ("Pi-hole", "http://192.0.2.31", "bearer", "pihole"),
    ("Sonarr", "http://192.0.2.40:8989", "header", "sonarr"),
    ("Radarr", "http://192.0.2.41:7878", "header", "radarr"),
    ("Prowlarr", "http://192.0.2.42:9696", "header", "prowlarr"),
    ("Bazarr", "http://192.0.2.43:6767", "header", "bazarr"),
    ("Jellyfin", "http://192.0.2.44:8096", "header", "jellyfin"),
    ("Seerr", "http://192.0.2.45:5055", "header", "seerr"),
    ("Proxmox", "http://192.0.2.46:8006", "header", "proxmox"),
    ("Omada", "http://192.0.2.47:8043", "header", "omada"),
    ("Nginx Proxy Manager", "http://192.0.2.48:81", "header", "npm"),
    ("Pulse", "http://192.0.2.49:7655", "bearer", "pulse"),
    ("Web fetch", "http://192.0.2.52", "none", "webfetch"),
]
for name, url, auth, kind in INTEGRATIONS:
    integrations.create_integration(name, url, auth, "synthetic", kind=kind)
for name, _url, _auth, _kind in INTEGRATIONS:
    seed_for_kind(integrations.get_integration(name))

# Files (SSH add-on) is an integration of its own, merged under Home Assistant
# in the agent view. Its tools carry the write-always-asks promise.
files = integrations.create_integration(
    "ha-files", "192.0.2.32", "none", "", kind="ha_ssh")
seed_for_kind(integrations.get_integration("ha-files"))
set_integration_config(integrations.get_integration("ha-files"), {
    "ha_integration": "Home Assistant", "ssh_user": "root", "ssh_port": 22,
    "host_fingerprint": "SHA256:synthetic-stage4-fingerprint"})
integrations.update_integration(
    "ha-files",
    secret="-----BEGIN OPENSSH PRIVATE KEY-----\nsynthetic\n"
           "-----END OPENSSH PRIVATE KEY-----")
# A Files row whose saved HA parent is now disabled: it must stay editable
# and show the broken link, rather than vanish or refuse every edit.
integrations.create_integration("Home Assistant (old)",
                                "http://192.0.2.33:8123", "bearer", "",
                                kind="ha")
integrations.update_integration("Home Assistant (old)", enabled=False)
old_files = integrations.create_integration(
    "ha-files-old", "192.0.2.34", "none", "", kind="ha_ssh")
seed_for_kind(integrations.get_integration("ha-files-old"))
set_integration_config(integrations.get_integration("ha-files-old"), {
    "ha_integration": "Home Assistant (old)", "ssh_user": "root", "ssh_port": 22})
integrations.update_integration(
    "ha-files-old",
    secret="-----BEGIN OPENSSH PRIVATE KEY-----\nsynthetic\n"
           "-----END OPENSSH PRIVATE KEY-----")
integrations.update_integration("ha-files-old", enabled=False)


# X11: synthetic permissions for the real Agents view. One pick permission,
# one label-followed permission, a normal light permission, and one paused
# permission so the banner and Resume path are both visible.
allowance_core.ensure_initialised()
allowance_core.apply_ceiling(
    "seed-ui", allowance_core.INTEGRATION,
    allowance_core.enabled_ids() + ["cover.open_cover", "cover.close_cover"],
    risk_ack=True, expected_hash=allowance_core.set_hash())
allowance_core.set_protected_entities("seed-ui", ["switch.plug"])
allowance_core.create_allowance(
    "seed-ui", agents["ops-agent"], "Eight plugs", "switch",
    "turn_off", ["switch.lamp", "switch.kettle",
                 "switch.coffee", "switch.electric_blanket",
                 "switch.office_monitor", "switch.garage_freezer",
                 "switch.joiner", "switch.office_heater"], rate_per_hour=30)
allowance_core.create_allowance(
    "seed-ui", agents["ops-agent"], "Living room lights", "switch",
    "turn_on", ["switch.lamp"], source_label_id="lbl-lights",
    rate_per_hour=30)
allowance_core.create_allowance(
    "seed-ui", agents["ops-agent"], "Ceiling light", "light",
    "turn_off", ["light.ceiling"], expires_at=now + 86400,
    rate_per_hour=30)
allowance_core.create_allowance(
    "seed-ui", agents["ops-agent"], "Living room lights on", "light",
    "turn_on", ["light.ceiling"], source_label_id="lbl-lights",
    rate_per_hour=30)
paused = allowance_core.create_allowance(
    "seed-ui", agents["ops-agent"], "Office heater", "switch",
    "toggle", ["switch.office_heater"], rate_per_hour=30)
allowance_core.suspend_allowance("seed-ui", paused["id"], "registry_changed")
allowance_core.refresh_labels("seed-ui")

policies.update_policy("exact_whitelist", "uptime\ndf -h /\nfree -m")
policies.update_policy("regex_whitelist", r"^systemctl status [a-z0-9_.@-]+$")
policies.update_policy("regex_blacklist", "rm -rf")
web_fetch.set_web_fetch_policy("ops-agent", True, allow=["docs.example.test"])

for kind, key, name, ips, host, vmid in (
        ("host", "web01", "web01", "192.0.2.10", "web01", ""),
        ("guest", "101", "app", "192.0.2.10", "pve", "101")):
    with db_conn() as conn:
        conn.execute("""INSERT OR REPLACE INTO inventory_items
            (source, kind, key, name, ips, fqdns, host, vmid, url, extra_json, seen_at)
            VALUES ('proxmox', ?, ?, ?, ?, '', ?, ?, '', '{}', ?)""",
                     (kind, key, name, ips, host, vmid, now - 60))
        conn.commit()

print(json.dumps({"pending": pending, "plan": plan["plan_id"]}))
