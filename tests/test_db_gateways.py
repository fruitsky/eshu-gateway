def test_register_gateway_preserves_token():
    from db.gateways import register_gateway, get_gateway_token, set_gateway_token
    register_gateway("10.0.0.1", "test-host", "v15.3")
    set_gateway_token("10.0.0.1", "test-token-abc")
    register_gateway("10.0.0.1", "renamed-host", "v15.3")
    token = get_gateway_token("10.0.0.1")
    assert token == "test-token-abc"


def test_register_new_gateway_has_no_token():
    from db.gateways import register_gateway, get_gateway_token
    register_gateway("10.0.0.2", "new-host", "v15.3")
    assert get_gateway_token("10.0.0.2") is None


def test_register_new_gateway_never_defaults_to_literal_None_string():
    # Regression: the api_token column default can be the literal string 'None'
    # (v15.0-era schema). A fresh INSERT must set api_token NULL explicitly so
    # the buggy default can never apply and /api/register never returns 'None'.
    from db.gateways import register_gateway, get_gateway_token
    from db.core import db_conn
    register_gateway("10.0.0.2", "new-host", "v15.3")
    assert get_gateway_token("10.0.0.2") is None
    with db_conn() as conn:
        row = conn.execute("SELECT api_token FROM gateways WHERE ip = ?", ("10.0.0.2",)).fetchone()
    assert row["api_token"] != "None"
    assert row["api_token"] is None


def test_set_and_get_gateway_token():
    from db.gateways import register_gateway, set_gateway_token, get_gateway_token
    register_gateway("10.0.0.3", "token-host", "v15.3")
    set_gateway_token("10.0.0.3", "secret-token")
    assert get_gateway_token("10.0.0.3") == "secret-token"


def test_get_gateway_by_token():
    from db.gateways import register_gateway, set_gateway_token, get_gateway_by_token
    register_gateway("10.0.0.4", "lookup-host", "v15.3")
    set_gateway_token("10.0.0.4", "my-token")
    gw = get_gateway_by_token("my-token")
    assert gw is not None
    assert gw["ip"] == "10.0.0.4"
    assert gw["hostname"] == "lookup-host"


def test_get_gateway_by_token_returns_none_for_invalid():
    from db.gateways import get_gateway_by_token
    assert get_gateway_by_token("nonexistent") is None


def test_register_gateway_updates_hostname():
    from db.gateways import register_gateway, get_gateways
    register_gateway("10.0.0.5", "old-name", "v15.3")
    register_gateway("10.0.0.5", "new-name", "v15.3")
    gws = get_gateways()
    gw = next(g for g in gws if g["ip"] == "10.0.0.5")
    assert gw["hostname"] == "new-name"


def test_get_gateways_returns_all():
    from db.gateways import register_gateway, get_gateways
    register_gateway("10.0.0.6", "gw-a", "v15.3")
    register_gateway("10.0.0.7", "gw-b", "v15.3")
    gws = get_gateways()
    assert len(gws) == 2
    ips = {g["ip"] for g in gws}
    assert ips == {"10.0.0.6", "10.0.0.7"}


def test_deregister_gateway():
    from db.gateways import register_gateway, deregister_gateway, get_gateways
    register_gateway("10.0.0.8", "gone", "v15.3")
    assert len(get_gateways()) == 1
    deregister_gateway("10.0.0.8")
    assert len(get_gateways()) == 0


def test_update_gateway_last_seen():
    import time
    from db.gateways import register_gateway, update_gateway_last_seen, get_gateways
    register_gateway("10.0.0.9", "seen", "v15.3")
    before = get_gateways()[0]["last_seen"]
    time.sleep(1.1)
    update_gateway_last_seen("10.0.0.9")
    after = get_gateways()[0]["last_seen"]
    assert after > before
