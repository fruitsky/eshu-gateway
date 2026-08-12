import time
from db.audit import record_audit_event
from db.gateways import get_gateways, deregister_gateway, clear_trigger_uninstall
from db.fleet import purge_old_fleet_commands
from core.notify import send_notify

_disconnected_gateways = set()
_offline_alerted = set()
OFFLINE_THRESHOLD = 300

def _check_gateway_transitions(now: int):
    gateways = get_gateways()
    for g in gateways:
        if now - g['last_seen'] > 30 and g['ip'] not in _disconnected_gateways:
            record_audit_event("disconnected", g['ip'], g.get('hostname'), f"Last seen {now - g['last_seen']}s ago")
            _disconnected_gateways.add(g['ip'])
        elif now - g['last_seen'] <= 30 and g['ip'] in _disconnected_gateways:
            _disconnected_gateways.discard(g['ip'])
            record_audit_event("connected", g['ip'], g.get('hostname'), "Reconnected")
        if now - g['last_seen'] > OFFLINE_THRESHOLD and g['ip'] not in _offline_alerted:
            send_notify('offline', 'Gateway Offline', f'{g.get("hostname") or g["ip"]} — last seen {now - g["last_seen"]}s ago')
            _offline_alerted.add(g['ip'])
        elif now - g['last_seen'] <= OFFLINE_THRESHOLD and g['ip'] in _offline_alerted:
            _offline_alerted.discard(g['ip'])
            send_notify('online', 'Gateway Online', f'{g.get("hostname") or g["ip"]} — back after ~{now - g["last_seen"]}s')

def _gateway_watch_loop():
    while True:
        time.sleep(30)
        try:
            _check_gateway_transitions(int(time.time()))
        except Exception:
            pass


_STALE_GATEWAY_DAYS = 7
_STALE_CLEANUP_INTERVAL = 3600

def _stale_gateway_cleanup_loop():
    """Background thread: periodically remove gateways offline > 7 days."""
    while True:
        time.sleep(_STALE_CLEANUP_INTERVAL)
        try:
            now = int(time.time())
            cutoff = now - (_STALE_GATEWAY_DAYS * 86400)
            gateways = get_gateways()
            for g in gateways:
                if g['last_seen'] < cutoff:
                    deregister_gateway(g['ip'])
                    clear_trigger_uninstall(g['ip'])
                    if g['ip'] in _disconnected_gateways:
                        _disconnected_gateways.discard(g['ip'])
                    record_audit_event("auto_deregistered", g['ip'], g.get('hostname'),
                                     f"Auto-removed after {_STALE_GATEWAY_DAYS} days offline. Last seen {now - g['last_seen']}s ago")
        except Exception:
            pass


_FLEET_RETENTION_DAYS = 7
_FLEET_CLEANUP_INTERVAL = 3600

def _fleet_cleanup_loop():
    """Background thread: purge completed fleet commands/results older than
    the retention window (7 days). Audit events remain as the permanent record."""
    while True:
        time.sleep(_FLEET_CLEANUP_INTERVAL)
        try:
            cutoff = int(time.time()) - (_FLEET_RETENTION_DAYS * 86400)
            removed = purge_old_fleet_commands(cutoff)
            if removed > 0:
                record_audit_event("fleet_purged", details=f"Auto-purged {removed} completed fleet command(s) older than {_FLEET_RETENTION_DAYS} days")
        except Exception:
            pass
