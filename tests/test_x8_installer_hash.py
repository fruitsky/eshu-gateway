"""X8: which installer each gateway runs.

The gateway reports the first 12 hex of the installer file's sha256 on
register and on every heartbeat; the dashboard stores it per gateway, turns
it into a Fleet/Edge/Build/outdated/unknown channel, and counts the channels
for the dev status. Old gateways send nothing and read as "unknown".
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "dashboard" / "eshu-installer-template.sh"
GENERATED = REPO / "dashboard" / "eshu-gateway-install.sh"
POLLER = REPO / "dashboard" / "eshu-poller.sh"
LOGGER = REPO / "dashboard" / "eshu-logger.sh"

BUILD = "0123456789ab"
EDGE = "1123456789ab"
FLEET = "2123456789ab"


def _gateway(ip="10.0.0.1", host="gw"):
    from db.gateways import register_gateway, set_gateway_token
    register_gateway(ip, host, "v6.0.0")
    set_gateway_token(ip, "tok-" + ip.replace(".", "-"))
    return {"X-Gateway-Token": "tok-" + ip.replace(".", "-")}


# ── the store ───────────────────────────────────────────────────────────────

def test_a_valid_hash_is_stored_and_a_change_moves_the_timestamp():
    from db.gateways import get_gateway, set_gateway_installer_hash
    _gateway()
    assert set_gateway_installer_hash("10.0.0.1", BUILD, now=1000) is True
    row = get_gateway("10.0.0.1")
    assert row["installer_hash"] == BUILD
    assert row["installer_hash_changed_at"] == 1000

    # The same hash does not count as a change.
    assert set_gateway_installer_hash("10.0.0.1", BUILD, now=2000) is True
    row = get_gateway("10.0.0.1")
    assert row["installer_hash_changed_at"] == 1000

    assert set_gateway_installer_hash("10.0.0.1", FLEET, now=3000) is True
    row = get_gateway("10.0.0.1")
    assert row["installer_hash"] == FLEET
    assert row["installer_hash_changed_at"] == 3000


@pytest.mark.parametrize("bad", ["", "xyz", "0123456789", "0123456789abc",
                                 "0123456789AG", None])
def test_an_invalid_hash_is_ignored_not_stored(bad):
    from db.gateways import get_gateway, set_gateway_installer_hash
    _gateway()
    set_gateway_installer_hash("10.0.0.1", BUILD)
    assert set_gateway_installer_hash("10.0.0.1", bad) is False
    assert get_gateway("10.0.0.1")["installer_hash"] == BUILD
    # An unknown gateway is not created by the call either.
    assert set_gateway_installer_hash("10.0.0.9", BUILD) is False


# ── the endpoints ───────────────────────────────────────────────────────────

def test_register_stores_the_hash_and_old_installers_read_unknown(
        client, auth_client):
    from core import installer_channels as ic
    ic.ensure_initialised()
    ic._atomic_write(ic.channel_path(ic.EDGE), b"#!/bin/bash\n# edge\n")
    ic._atomic_write(ic.channel_path(ic.FLEET), b"#!/bin/bash\n# fleet\n")
    build = ic.build_hash()
    headers = _gateway()
    response = client.post("/api/register", json={
        "ip": "10.0.0.1", "hostname": "gw", "version": "v6.0.0",
        "installer_hash": build}, headers=headers)
    assert response.status_code == 200
    row = next(g for g in auth_client.get("/api/gateways").json()
               if g["ip"] == "10.0.0.1")
    assert row["installer_hash"] == build
    assert row["installer_channel"] == "build"

    # An old installer: no field at all. It keeps the stored value, and a
    # fresh old gateway is "unknown".
    headers2 = _gateway("10.0.0.2", "old")
    assert client.post("/api/register", json={
        "ip": "10.0.0.2", "hostname": "old", "version": "v6.0.0"},
        headers=headers2).status_code == 200
    rows = {g["ip"]: g for g in auth_client.get("/api/gateways").json()}
    assert rows["10.0.0.1"]["installer_hash"] == build
    assert rows["10.0.0.2"]["installer_hash"] == ""
    assert rows["10.0.0.2"]["installer_channel"] == "unknown"


def test_an_invalid_register_value_does_not_clear_the_stored_one(
        client, auth_client):
    headers = _gateway()
    client.post("/api/register", json={
        "ip": "10.0.0.1", "hostname": "gw", "version": "v6.0.0",
        "installer_hash": BUILD}, headers=headers)
    client.post("/api/register", json={
        "ip": "10.0.0.1", "hostname": "gw", "version": "v6.0.0",
        "installer_hash": "not-a-hash"}, headers=headers)
    row = next(g for g in auth_client.get("/api/gateways").json()
               if g["ip"] == "10.0.0.1")
    assert row["installer_hash"] == BUILD


def test_a_heartbeat_can_report_a_new_hash(client, auth_client):
    headers = _gateway()
    client.post("/api/register", json={
        "ip": "10.0.0.1", "hostname": "gw", "version": "v6.0.0",
        "installer_hash": BUILD}, headers=headers)
    response = client.post("/api/gateway-heartbeat", json={
        "ip": "10.0.0.1", "hostname": "gw", "poller_ok": 1, "gateway_ok": 1,
        "can_reach": 1, "installer_hash": FLEET}, headers=headers)
    assert response.status_code == 200
    from db.gateways import get_gateway
    assert get_gateway("10.0.0.1")["installer_hash"] == FLEET


def test_dev_status_counts_every_channel(auth_client):
    from core import installer_channels as ic
    ic.ensure_initialised()
    ic._atomic_write(ic.channel_path(ic.EDGE), b"#!/bin/bash\n# edge\n")
    ic._atomic_write(ic.channel_path(ic.FLEET), b"#!/bin/bash\n# fleet\n")
    build = ic.build_hash()
    edge = ic.channel_hash(ic.EDGE)
    fleet = ic.channel_hash(ic.FLEET)
    assert len({build, edge, fleet}) == 3

    from db.gateways import register_gateway, set_gateway_installer_hash
    for i, h in enumerate((fleet, edge, build, "deadbeefcafe", "")):
        ip = "10.0.0.%d" % (10 + i)
        register_gateway(ip, "gw%d" % i, "v6.0.0")
        if h:
            assert set_gateway_installer_hash(ip, h) is True
    body = auth_client.get("/api/dev/status").json()
    assert (body["on_fleet"], body["on_edge"], body["outdated"],
            body["unknown"]) == (1, 1, 1, 1)
    assert body["build_hash"] == build
    assert body["edge_hash"] == edge
    assert body["fleet_hash"] == fleet


# ── the scripts ─────────────────────────────────────────────────────────────

def test_the_installer_hashes_itself_and_writes_the_file():
    text = TEMPLATE.read_text()
    assert 'INSTALLER_HASH=$(sha256sum -- "$0"' in text
    # only 12 lowercase hex may reach the body or the file
    assert "[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]" in text
    assert '"installer_hash":"\'"$INSTALLER_HASH"\'"' in text
    assert "/etc/eshu-installer-hash" in text
    assert "chmod 644 /etc/eshu-installer-hash.tmp" in text
    assert "chown root:root /etc/eshu-installer-hash.tmp" in text
    # The generated Build installer carries the same hunk.
    generated = GENERATED.read_text()
    assert 'INSTALLER_HASH=$(sha256sum -- "$0"' in generated
    assert '"installer_hash":"\'"$INSTALLER_HASH"\'"' in generated


def test_the_poller_uses_the_hash_file_when_it_re_registers():
    text = POLLER.read_text()
    assert "read_installer_hash()" in text
    assert 'head -n1 "/etc/eshu-installer-hash"' in text
    assert text.count('"installer_hash":"\'"$(read_installer_hash)"\'"') == 2


def test_the_logger_resends_the_hash_on_every_heartbeat():
    text = LOGGER.read_text()
    assert 'INSTALLER_HASH=$(head -n1 "/etc/eshu-installer-hash"' in text
    assert '\\"installer_hash\\":\\"$INSTALLER_HASH\\"' in text


# ── the badge ───────────────────────────────────────────────────────────────

def test_the_badge_is_in_the_gateway_cell_not_a_new_column():
    js = (REPO / "dashboard" / "static" / "app.js").read_text()
    html = (REPO / "dashboard" / "static" / "index.html").read_text()
    css = (REPO / "dashboard" / "static" / "style.css").read_text()
    assert "function gwChannelBadge(g)" in js
    # The channel and hash come from the API; the UI does not compare them.
    assert "g.installer_channel" in js
    assert "g.installer_hash" in js
    assert "installer_channel" in js[js.index("function gwChannelBadge"):][:1500]
    # Seven table columns: the badge rides in the gateway cell.
    head = html[html.index('<thead><tr><th>Status</th>'):]
    head = head[:head.index("</tr>")]
    assert head.count("<th>") + head.count("<th ") == 7
    assert ".gw-ch-fleet" in css and ".gw-ch-outdated" in css
    assert ".gw-ch-unknown" in css
