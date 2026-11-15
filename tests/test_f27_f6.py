"""F27: an allowance run is its own F6 approval bucket."""
from db.integrations import record_integration_call
from db.sessions import get_session_summary


def test_an_allowance_run_counts_in_the_session_summary():
    record_integration_call('ha', 'call_service', 'household', 'POST',
                            '/services/cover/open_cover', 200, 5, '', 0, 0,
                            session_id='sess-f27', approval='allowance:7')
    summary = get_session_summary('sess-f27')
    assert summary['by_approval']['allowance'] == 1
