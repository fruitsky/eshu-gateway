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

    def test_rate_limit_is_per_agent(self, auth_client):
        household = _create(auth_client, 'household')
        admin = _create(auth_client, 'hermes-admin')
        agent_id = household['id']
        auth_client.put(f'/api/agents/{agent_id}/policy',
                        json={'tools_mode': 'all', 'rate_limit_per_min': 2})
        h = {'Authorization': 'Bearer ' + household['token']}
        a = {'Authorization': 'Bearer ' + admin['token']}
        assert auth_client.get('/mcp', headers=h, follow_redirects=False).status_code != 429
        assert auth_client.get('/mcp', headers=h, follow_redirects=False).status_code != 429
        assert auth_client.get('/mcp', headers=h, follow_redirects=False).status_code == 429
        # Admin is unlimited.
        for _ in range(4):
            assert auth_client.get('/mcp', headers=a, follow_redirects=False).status_code != 429

    def test_last_used_is_tracked(self, auth_client):
        created = _create(auth_client, 'household')
        auth_client.get('/mcp', headers={'Authorization': 'Bearer ' + created['token']},
                        follow_redirects=False)
        agent = get_agent_by_token(created['token'])
        assert agent['last_used_at'] > 0
