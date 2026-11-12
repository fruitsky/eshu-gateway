"""BI1/BI2/BI3 — the lockbox must be one line, one run, and live when claimed.

The poller ticket-poll fix made the lockbox path live for the first time on the
fleet, so these are the dashboard-side halves of the three findings: no line
breaks issued, exactly one consumer wins, and the stamp is the delivery time.
"""
import threading
import time

from db.core import db_conn
from db.requests import (
    create_request, update_request_status, get_request_status,
    update_ticket_consumed_by_ip, get_ticket_by_request_id, ticket_line,
)


def _approve(command, target='192.0.2.10', agent='', session='', created=None,
             expires=None):
    rid = create_request(target, command, agent=agent, session_id=session)
    update_request_status(rid, 'approved')
    if created is not None or expires is not None:
        now = int(time.time())
        with db_conn() as conn:
            conn.execute(
                'UPDATE requests SET created_at = ?, expires_at = ? WHERE id = ?',
                (created if created is not None else now,
                 expires if expires is not None else now + 90, rid))
            conn.commit()
    return rid


def _audit_details():
    from db.audit import get_audit_log
    return ' '.join((row['details'] or '') for row in get_audit_log(limit=50))


def test_bi1_a_multiline_approved_command_yields_no_poll_line():
    rid = _approve('echo one\necho two')
    assert update_ticket_consumed_by_ip('192.0.2.10') is None
    assert get_request_status(rid) == 'denied'
    assert 'line break' in _audit_details()


def test_bi1_a_multiline_approved_command_yields_no_by_id_line():
    rid = _approve('echo one\necho two')
    assert get_ticket_by_request_id(rid) is None
    assert get_request_status(rid) == 'denied'
    assert 'line break' in _audit_details()


def test_bi1_a_carriage_return_is_refused_too():
    rid = _approve('echo one\recho two')
    assert get_ticket_by_request_id(rid) is None
    assert get_request_status(rid) == 'denied'


def test_bi1_a_multiline_agent_ticket_is_refused_with_its_own_reason():
    """Even on a v2 gateway (so M1 does not fire), the line-break rule holds."""
    from db.gateways import register_gateway, set_gateway_ssh_capabilities
    register_gateway('192.0.2.30', 'gw-bi1', 'v-test')
    set_gateway_ssh_capabilities('192.0.2.30', 'eshu-ssh-agent-v1', 'v2')
    rid = _approve('echo one\necho two', target='192.0.2.30',
                   agent='agent-a', session='sess-1')
    assert update_ticket_consumed_by_ip('192.0.2.30') is None
    assert get_request_status(rid) == 'denied'
    assert 'line break' in _audit_details()


def test_bi2_poll_and_by_id_race_delivers_exactly_once():
    """Both consumers select the same approved row; only one may deliver."""
    rid = _approve('uptime')
    results = []
    barrier = threading.Barrier(2)

    def poll():
        barrier.wait()
        results.append(update_ticket_consumed_by_ip('192.0.2.10'))

    def by_id():
        barrier.wait()
        results.append(get_ticket_by_request_id(rid))

    threads = [threading.Thread(target=poll), threading.Thread(target=by_id)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    delivered = [r for r in results if r is not None]
    assert len(delivered) == 1, results
    assert get_request_status(rid) == 'consumed'


def test_bi2_two_pollers_deliver_exactly_once():
    _approve('uptime')
    results = []
    barrier = threading.Barrier(3)

    def poll():
        barrier.wait()
        results.append(update_ticket_consumed_by_ip('192.0.2.10'))

    threads = [threading.Thread(target=poll) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len([r for r in results if r is not None]) == 1, results


def test_bi3_the_stamp_is_the_delivery_time_not_created_at():
    now = int(time.time())
    _approve('uptime', created=now - 500, expires=now + 30)
    ticket = update_ticket_consumed_by_ip('192.0.2.10')
    assert ticket
    stamp = int(ticket.split('|', 1)[0])
    assert stamp >= now - 2, ticket


def test_bi3_a_late_approval_can_still_run():
    """created_at is long past; the gateway honours the stamp for 90 s."""
    now = int(time.time())
    _approve('uptime', created=now - 500, expires=now + 30)
    ticket = update_ticket_consumed_by_ip('192.0.2.10')
    stamp = int(ticket.split('|', 1)[0])
    assert 0 <= now - stamp <= 90


def test_bi3_no_60_second_grace_on_the_poll_path():
    now = int(time.time())
    rid = _approve('uptime', created=now - 120, expires=now - 30)
    assert update_ticket_consumed_by_ip('192.0.2.10') is None
    assert get_request_status(rid) == 'approved'


def test_ticket_line_defaults_to_created_at_for_the_old_callers():
    now = int(time.time())
    rid = _approve('uptime')
    row = None
    with db_conn() as conn:
        row = conn.execute('SELECT * FROM requests WHERE id = ?', (rid,)).fetchone()
    assert ticket_line(row, '').startswith(str(row['created_at']) + '|')
    assert ticket_line(row, '', now).startswith(str(now) + '|')
