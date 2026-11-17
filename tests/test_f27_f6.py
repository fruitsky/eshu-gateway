"""F27: an allowance run is its own F6 approval bucket."""
from db.integrations import record_integration_call
from db.sessions import get_session_summary


def test_an_allowance_run_counts_in_the_session_summary():
    record_integration_call('ha', 'call_service', 'household', 'POST',
                            '/services/cover/open_cover', 200, 5, '', 0, 0,
                            session_id='sess-f27', approval='allowance:7')
    summary = get_session_summary('sess-f27')
    assert summary['by_approval']['allowance'] == 1


def test_an_excluded_allowance_run_does_not_count(auth_client):
    from db.core import db_conn
    record_integration_call('ha', 'call_service', 'household', 'POST',
                            '/services/light/turn_on', 200, 5, '', 0, 0,
                            session_id='sess-f27-excluded',
                            approval='allowance:7')
    with db_conn() as conn:
        conn.execute("UPDATE integration_calls SET excluded = 1 "
                     "WHERE session_id = 'sess-f27-excluded'")
        conn.commit()
    summary = get_session_summary('sess-f27-excluded')
    assert summary['by_approval']['allowance'] == 0
    data = auth_client.get('/api/statistics?days=14').json()
    assert data['approval_breakdown']['totals']['allowance'] == 0
