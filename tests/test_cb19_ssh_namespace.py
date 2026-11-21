"""CB19: the `ssh` integration namespace is reserved for `ssh_exec`."""
from db.audit import get_audit_log
from db.core import db_conn
from db.integrations import (
    RESERVED_SSH_NAMESPACE_KEY, create_integration, create_tool, get_integration,
    get_tools, migrate_reserved_ssh_integration,
)


def _forget_namespace_check():
    with db_conn() as conn:
        conn.execute('DELETE FROM meta WHERE key = ?',
                     (RESERVED_SSH_NAMESPACE_KEY,))
        conn.commit()


def test_creating_an_integration_named_ssh_is_refused(auth_client):
    for name in ('ssh', 'SSH', ' ssh '):
        r = auth_client.post('/api/integrations', json={
            'name': name, 'base_url': 'http://192.0.2.60'})
        assert r.status_code == 400, (name, r.text)
        assert 'reserved' in r.json()['detail']
        assert get_integration(name.strip()) is None
    # A longer name that merely starts with ssh is not the reserved namespace.
    r = auth_client.post('/api/integrations', json={
        'name': 'ssh gateway', 'base_url': 'http://192.0.2.60'})
    assert r.status_code == 200, r.text


def test_an_existing_ssh_integration_is_disabled_once(auth_client):
    create_integration('ssh', 'http://192.0.2.61', 'none', '')
    row = get_integration('ssh')
    create_tool(row['id'], 'exec', 'Run', 'POST', '/exec', [], '',
                read_only=False)
    _forget_namespace_check()

    assert migrate_reserved_ssh_integration() == 1
    row = get_integration('ssh')
    assert row['enabled'] == 0
    assert all(not t['enabled'] for t in get_tools(row['id']))
    rows = [x for x in get_audit_log(limit=20)
            if x['event_type'] == 'integration_ssh_namespace_reserved']
    assert len(rows) == 1
    assert "integration 'ssh' disabled" in rows[0]['details']
    # The meta flag makes a second startup a no-op.
    assert migrate_reserved_ssh_integration() == 0


def test_the_existing_ssh_row_cannot_be_edited_or_given_a_tool(auth_client):
    create_integration('ssh', 'http://192.0.2.61', 'none', '')
    row = get_integration('ssh')
    r = auth_client.put('/api/integrations/ssh', json={'enabled': True})
    assert r.status_code == 400
    assert 'reserved' in r.json()['detail']
    r = auth_client.post('/api/integrations/ssh/tools', json={
        'name': 'exec', 'method': 'POST', 'path_template': '/exec'})
    assert r.status_code == 400
    assert 'reserved' in r.json()['detail']
    assert get_tools(row['id']) == []
