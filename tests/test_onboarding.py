"""The first-run walkthrough's state rule.

It is a guide, not a gate: it shows only on an empty install, it can be
dismissed, and an install that already has gateways never sees it.
"""


def test_empty_install_shows_it(auth_client):
    d = auth_client.get("/api/onboarding").json()
    assert d["show"] is True
    assert d["done"] is False
    assert d["gateways"] == 0


def test_dismissing_it_stops_it_returning(auth_client):
    assert auth_client.post("/api/onboarding/done").json()["done"] is True
    d = auth_client.get("/api/onboarding").json()
    assert d["show"] is False
    assert d["done"] is True


def test_an_enrolled_gateway_never_shows_it(auth_client):
    from db.gateways import register_gateway
    register_gateway("10.0.0.1", "gw", "v0.1.0")
    d = auth_client.get("/api/onboarding").json()
    assert d["gateways"] == 1
    assert d["show"] is False


def test_key_state_is_reflected(auth_client):
    auth_client.put("/api/enroll/keys",
                    json={"eshu_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI test@eshu"})
    d = auth_client.get("/api/onboarding").json()
    assert d["key_configured"] is True
