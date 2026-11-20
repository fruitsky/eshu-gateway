"""Agent policy API, rate limiting, and revocation."""
from db.agent_tokens import get_agent_by_token


def _create(auth_client, name):
    r = auth_client.post('/api/agents', json={'name': name})
    assert r.status_code == 200, r.text
    return r.json()


class TestAgentApi:
    def test_duplicate_name_rejected(self, auth_client):
        assert auth_client.post('/api/agents', json={'name': 'hermes'}).status_code == 200
        r = auth_client.post('/api/agents', json={'name': 'hermes'})
        assert r.status_code == 400

    def test_policy_round_trip_and_available_tools(self, auth_client):
        created = _create(auth_client, 'household')
        agent_id = created['id']

        available = auth_client.get(f'/api/agents/{agent_id}/available-tools')
        assert available.status_code == 200
        assert isinstance(available.json()['integrations'], list)

        policy = {
            'tools_mode': 'allowlist',
            'tools': ['home_assistant_call_service'],
            'arg_policy': {
                'home_assistant_call_service': {
                    'domain_allow': ['light'],
                    'domain_deny': ['homeassistant'],
                }
            },
            'approval': {'home_assistant_call_service': 'ungated'},
            'rate_limit_per_min': 20,
        }
        r = auth_client.put(f'/api/agents/{agent_id}/policy', json=policy)
        assert r.status_code == 200, r.text
        got = auth_client.get(f'/api/agents/{agent_id}/policy').json()
        assert got['tools_mode'] == 'allowlist'
        assert got['tools'] == ['home_assistant_call_service']
        assert got['approval']['home_assistant_call_service'] == 'ungated'
        assert got['rate_limit_per_min'] == 20

        listed = auth_client.get('/api/agents').json()
        row = next(a for a in listed if a['id'] == agent_id)
        assert row['policy']['tools_mode'] == 'allowlist'
        assert 'token' not in row and 'token_hash' not in row

    def test_invalid_policy_rejected(self, auth_client):
        agent_id = _create(auth_client, 'household')['id']
        r = auth_client.put(f'/api/agents/{agent_id}/policy',
                            json={'tools_mode': 'nope'})
        assert r.status_code == 400
        r = auth_client.put(f'/api/agents/{agent_id}/policy',
                            json={'approval': {'x': 'maybe'}})
        assert r.status_code == 400

    def test_revoke_blocks_mcp_but_not_admin(self, auth_client):
        household = _create(auth_client, 'household')
        admin = _create(auth_client, 'hermes-admin')
        assert auth_client.post(f"/api/agents/{household['id']}/revoke").status_code == 200
        assert auth_client.get(
            '/mcp', headers={'Authorization': 'Bearer ' + household['token']},
            follow_redirects=False,
        ).status_code == 401
        # Revoking one token does not touch the other.
        assert auth_client.get(
            '/mcp', headers={'Authorization': 'Bearer ' + admin['token']},
            follow_redirects=False,
        ).status_code == 307

    def test_rate_limit_never_breaks_connect(self, auth_client):
        """The per-agent limit is enforced on tool calls, never on /mcp
        handshake/list traffic (a 429 there parks the MCP client)."""
        household = _create(auth_client, 'household')
        agent_id = household['id']
        auth_client.put(f'/api/agents/{agent_id}/policy',
                        json={'tools_mode': 'all', 'rate_limit_per_min': 1})
        h = {'Authorization': 'Bearer ' + household['token']}
        for _ in range(4):
            r = auth_client.get('/mcp', headers=h, follow_redirects=False)
            assert r.status_code == 307

    def test_last_used_is_tracked(self, auth_client):
        created = _create(auth_client, 'household')
        auth_client.get('/mcp', headers={'Authorization': 'Bearer ' + created['token']},
                        follow_redirects=False)
        agent = get_agent_by_token(created['token'])
        assert agent['last_used_at'] > 0


class TestReservedAgentNames:
    def test_reserved_names_are_refused_on_create_case_insensitively(self, auth_client):
        for name in ('operator-test', 'Operator', 'MCP'):
            r = auth_client.post('/api/agents', json={'name': name})
            assert r.status_code == 400, (name, r.text)

    def test_the_rename_endpoint_is_gone(self, auth_client):
        agent_id = _create(auth_client, 'household')['id']
        r = auth_client.patch('/api/agents/%d' % agent_id,
                              json={'name': 'household-2'})
        assert r.status_code == 405



class TestReservedNameStartupAudit:
    def test_existing_reserved_names_get_one_audit_row(self, temp_db):
        from db.core import db_conn
        from db.audit import get_audit_log
        import main

        with db_conn() as conn:
            conn.execute(
                "INSERT INTO agent_tokens (name, token_hash, created_at) "
                "VALUES ('Operator', 'h1', 0), ('mcp', 'h2', 0), "
                "('household', 'h3', 0)")
            conn.commit()
        names = main._audit_reserved_agent_names()
        assert names == ['Operator', 'mcp']
        rows = [row for row in get_audit_log()
                if row['event_type'] == 'agent_reserved_name_present']
        assert len(rows) == 1
        assert 'Operator' in rows[0]['details']
        assert 'mcp' in rows[0]['details']
        assert 'household' not in rows[0]['details']

    def test_no_reserved_names_writes_no_audit_row(self, temp_db):
        from db.audit import get_audit_log
        import main
        from db.agent_tokens import create_agent_token

        create_agent_token('household')
        assert main._audit_reserved_agent_names() == []
        assert not [row for row in get_audit_log()
                    if row['event_type'] == 'agent_reserved_name_present']
