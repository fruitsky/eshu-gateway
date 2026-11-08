"""KI-1 — /api/statistics narrowed to gateways must not 500.

`get_statistics` builds its IP filter as `AND r.target_ip IN (…)` but several
queries read `FROM requests` without the `r` alias, so SQLite raised
`no such column: r.target_ip` for both the basic and the extended response.
"""

from db.gateways import register_gateway
from db.requests import create_request


def _seed():
    register_gateway('10.0.0.1', 'web01', 'v15.3')
    register_gateway('10.0.0.2', 'web02', 'v15.3')
    create_request('10.0.0.1', 'uptime', status='auto-approved', ttl=0, session_id='s1')
    create_request('10.0.0.1', 'df -h', status='pending', ttl=90, session_id='s1')
    create_request('10.0.0.2', 'free -h', status='auto-approved', ttl=0, session_id='s2')
    create_request('10.0.0.2', 'rm -rf /tmp/x', status='denied', ttl=0, session_id='s2')


def test_statistics_gateway_filter_basic(auth_client):
    _seed()
    response = auth_client.get('/api/statistics?days=14&gateway_ips=10.0.0.1')
    assert response.status_code == 200, response.text
    data = response.json()
    # Every block that carries a gateway filter is narrowed.
    assert [g['ip'] for g in data['per_gateway']] == ['10.0.0.1']
    assert data['top_commands'][0]['command'] == 'uptime'
    assert data['top_commands'][0]['hosts'] == 1
    assert sum(day['total'] for day in data['daily']) == 2


def test_statistics_gateway_filter_extended(auth_client):
    _seed()
    response = auth_client.get(
        '/api/statistics?days=14&extended=1&gateway_ips=10.0.0.1,10.0.0.2')
    assert response.status_code == 200, response.text
    data = response.json()
    assert sorted(g['ip'] for g in data['per_gateway']) == ['10.0.0.1', '10.0.0.2']
    # The extended blocks (heatmaps, per-gateway hourly) obey the filter too.
    assert sum(data['hourly_heatmap']) == 4
    assert sum(sum(row) for row in data['weekly_heatmap']) == 4
    assert sum(sum(buckets) for buckets in data['per_gateway_hourly'].values()) == 4


def test_statistics_gateway_filter_single_ip_and_totals(auth_client):
    _seed()
    data = auth_client.get('/api/statistics?days=14&gateway_ip=10.0.0.2').json()
    assert [g['ip'] for g in data['per_gateway']] == ['10.0.0.2']
    assert sum(day['total'] for day in data['daily']) == 2
    assert sum(day['denied'] for day in data['daily']) == 1


def test_statistics_unfiltered_still_works(auth_client):
    _seed()
    data = auth_client.get('/api/statistics?days=14&extended=1').json()
    assert sorted(g['ip'] for g in data['per_gateway']) == ['10.0.0.1', '10.0.0.2']
    assert sum(day['total'] for day in data['daily']) == 4
