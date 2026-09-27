"""Offline detection.

`/api/gateways` exposes a server-derived `online` flag (and `last_seen_ago`) so
the UI never re-derives staleness from raw epochs. The transition watcher uses
the same DISCONNECTED_AFTER threshold. Regression guard for the old 30s
threshold that flapped against the 30s poller/logger cadence.
"""
import time


def _set_last_seen(ip, ts):
    from db.core import get_db
    conn = get_db()
    cur = conn.cursor()
    cur.execute('UPDATE gateways SET last_seen = ? WHERE ip = ?', (ts, ip))
    conn.commit()
    conn.close()


def _gw(auth_client, ip):
    return {g["ip"]: g for g in auth_client.get("/api/gateways").json()}[ip]


def test_gateways_reports_online_for_fresh(auth_client):
    from db.gateways import register_gateway, update_gateway_last_seen
    register_gateway("10.0.0.1", "fresh", "v15.3")
    update_gateway_last_seen("10.0.0.1")
    g = _gw(auth_client, "10.0.0.1")
    assert g["online"] is True
    assert g["last_seen_ago"] < 5


def test_gateways_reports_offline_past_threshold(auth_client):
    from db.gateways import register_gateway
    from core.gateway_watch import DISCONNECTED_AFTER
    register_gateway("10.0.0.2", "stale", "v15.3")
    _set_last_seen("10.0.0.2", int(time.time()) - (DISCONNECTED_AFTER + 10))
    assert _gw(auth_client, "10.0.0.2")["online"] is False


def test_still_online_between_old_30s_and_new_threshold(auth_client):
    # The poller and logger each report every 30s; a 60s-old last_seen is normal
    # jitter, not offline. This is the exact case the old 30s table threshold
    # misreported (the reported bug).
    from db.gateways import register_gateway
    register_gateway("10.0.0.3", "jittery", "v15.3")
    _set_last_seen("10.0.0.3", int(time.time()) - 60)
    assert _gw(auth_client, "10.0.0.3")["online"] is True


def test_transition_watcher_uses_new_threshold():
    from db.gateways import register_gateway
    from db.audit import get_audit_log
    from core.gateway_watch import (
        _check_gateway_transitions, _disconnected_gateways, DISCONNECTED_AFTER,
    )
    ip = "10.0.0.4"
    register_gateway(ip, "watch", "v15.3")
    _disconnected_gateways.discard(ip)
    now = int(time.time())

    def disconnected_logged():
        return any(e["event_type"] == "disconnected" and e["gateway_ip"] == ip
                   for e in get_audit_log())

    # 60s stale -> below the (new) threshold, no disconnect event.
    _set_last_seen(ip, now - 60)
    _check_gateway_transitions(now)
    assert not disconnected_logged()

    # Past the threshold -> disconnect fires exactly once.
    _set_last_seen(ip, now - (DISCONNECTED_AFTER + 5))
    _check_gateway_transitions(now)
    assert disconnected_logged()

    # Recovery clears the state.
    _set_last_seen(ip, now)
    _check_gateway_transitions(now)
    assert ip not in _disconnected_gateways


def test_offline_during_recent_update_reports_updating(auth_client):
    from db.gateways import register_gateway, set_trigger_update_version
    from core.gateway_watch import DISCONNECTED_AFTER
    register_gateway("10.0.0.5", "updating", "v15.3")
    _set_last_seen("10.0.0.5", int(time.time()) - (DISCONNECTED_AFTER + 10))
    set_trigger_update_version(str(int(time.time())))
    g = _gw(auth_client, "10.0.0.5")
    assert g["online"] is False
    assert g["updating"] is True


def test_offline_without_recent_trigger_is_not_updating(auth_client):
    from db.gateways import register_gateway, set_trigger_update_version
    from core.gateway_watch import DISCONNECTED_AFTER, UPDATE_ACTIVE_WINDOW
    register_gateway("10.0.0.6", "dead", "v15.3")
    _set_last_seen("10.0.0.6", int(time.time()) - (DISCONNECTED_AFTER + 10))
    # Trigger is older than the window -> genuinely offline, not updating.
    set_trigger_update_version(str(int(time.time()) - (UPDATE_ACTIVE_WINDOW + 60)))
    g = _gw(auth_client, "10.0.0.6")
    assert g["online"] is False
    assert g["updating"] is False


def test_online_wins_over_updating(auth_client):
    from db.gateways import register_gateway, update_gateway_last_seen, set_trigger_update_version
    register_gateway("10.0.0.7", "back", "v15.3")
    update_gateway_last_seen("10.0.0.7")
    set_trigger_update_version(str(int(time.time())))
    g = _gw(auth_client, "10.0.0.7")
    assert g["online"] is True
    assert g["updating"] is False
