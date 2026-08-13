class TestDevToolsSetting:

    def test_defaults_to_off(self, auth_client):
        r = auth_client.get("/api/settings/dev-tools")
        assert r.status_code == 200
        assert r.json()["enabled"] is False

    def test_toggle_on_and_reflect(self, auth_client):
        r = auth_client.put("/api/settings/dev-tools", json={"enabled": True})
        assert r.status_code == 200
        assert r.json()["enabled"] is True
        assert auth_client.get("/api/settings/dev-tools").json()["enabled"] is True

    def test_toggle_off(self, auth_client):
        auth_client.put("/api/settings/dev-tools", json={"enabled": True})
        auth_client.put("/api/settings/dev-tools", json={"enabled": False})
        assert auth_client.get("/api/settings/dev-tools").json()["enabled"] is False

    def test_requires_auth(self, client):
        assert client.get("/api/settings/dev-tools").status_code == 401
        assert client.put("/api/settings/dev-tools", json={"enabled": True}).status_code == 401


class TestFeatureFlagsAuth:

    def test_feature_flags_list_requires_auth(self, client):
        # Feature-flag names/descriptions are operator info — must be
        # session-gated (not discoverable by unauthenticated agents).
        assert client.get("/api/feature-flags").status_code == 401

    def test_feature_flags_list_works_for_session(self, auth_client):
        r = auth_client.get("/api/feature-flags")
        assert r.status_code == 200


class TestNotifyConfig:

    def test_get_notify_config_defaults(self, auth_client):
        r = auth_client.get("/api/notify-config")
        assert r.status_code == 200
        data = r.json()
        assert data["url"] == ""
        assert "jit" in data["events"]
        assert "dashboard_url" in data

    def test_save_notify_config(self, auth_client):
        r = auth_client.put("/api/notify-config", json={
            "url": "https://hooks.example.com/abc",
            "events": "jit,blocked,offline",
            "dashboard_url": "http://192.168.1.100:8000",
        })
        assert r.status_code == 200
        data = auth_client.get("/api/notify-config").json()
        assert data["url"] == "https://hooks.example.com/abc"
        assert data["events"] == "jit,blocked,offline"
        assert data["dashboard_url"] == "http://192.168.1.100:8000"


class TestNotifySend:

    def test_test_event_bypasses_event_filter(self):
        # Regression: the dashboard Test button must send even when the
        # configured events list excludes 'test' (the old filter silently
        # dropped it, so the Test button never actually delivered anything).
        from db.misc import set_notify_config
        from core.notify import _build_payload
        set_notify_config("https://hooks.example.com/x", "jit,window", "")
        # No webhook reachable here, so send_notify returns False (delivery
        # failure) — but crucially it must NOT short-circuit on the event filter.
        from core.notify import send_notify
        ok = send_notify('test', 'T', 'B')
        assert ok is False  # attempted delivery, failed (unreachable URL) — not filtered

    def test_offline_event_filtered_when_not_subscribed(self):
        from db.misc import set_notify_config
        from core.notify import send_notify
        set_notify_config("https://hooks.example.com/x", "jit,window", "")
        # 'offline' is not in the events list -> filtered before any HTTP call.
        assert send_notify('offline', 'T', 'B') is False

    def test_build_payload_includes_dashboard_link(self):
        from core.notify import _build_payload
        p = _build_payload("Title", "Body", "http://192.168.1.100:8000")
        assert "Open dashboard" in p["text"]
        assert "http://192.168.1.100:8000" in p["text"]
        p2 = _build_payload("Title", "Body", "")
        assert "Open dashboard" not in p2["text"]

    def test_discord_native_webhook_uses_content(self):
        # Regression: Discord's native webhook endpoint rejects Slack-format
        # {"text": ...} with "Cannot send an empty message" (400). It must
        # receive {"content": ...} instead, with Markdown bold + link syntax.
        from core.notify import _build_payload
        url = "https://discord.com/api/webhooks/123/abc"
        p = _build_payload("Title", "Body", "http://192.168.1.100:8000", url)
        assert "content" in p
        assert "text" not in p
        assert "**Title**" in p["content"]          # Markdown bold
        assert "[Open dashboard](http://192.168.1.100:8000)" in p["content"]  # Markdown link
        assert "<http://192.168.1.100:8000|Open dashboard>" not in p["content"]  # not Slack syntax

    def test_discord_slack_url_still_uses_text(self):
        # The /slack-suffixed Discord endpoint understands Slack format.
        from core.notify import _build_payload
        url = "https://discord.com/api/webhooks/123/abc/slack"
        p = _build_payload("Title", "Body", "", url)
        assert "text" in p
        assert "content" not in p

    def test_slack_webhook_uses_text(self):
        from core.notify import _build_payload
        p = _build_payload("Title", "Body", "", "https://hooks.slack.com/services/abc")
        assert "text" in p
        assert "content" not in p

    def test_slack_uses_slack_bold_and_link(self):
        from core.notify import _build_payload
        p = _build_payload("Title", "Body", "http://192.168.1.100:8000",
                           "https://hooks.slack.com/services/abc")
        assert "*Title*" in p["text"]                                   # Slack bold
        assert "<http://192.168.1.100:8000|Open dashboard>" in p["text"]  # Slack link
        assert "[Open dashboard]" not in p["text"]

    def test_webhook_sets_user_agent(self):
        # Regression: Discord rejects the default Python-urllib UA with 403,
        # so the request must carry an explicit EshuGateway User-Agent.
        import urllib.request
        from core.notify import _do_webhook, _USER_AGENT

        # Monkeypatch urlopen to capture the Request without touching the network.
        captured = {}
        def fake_urlopen(req, timeout=None):
            captured['req'] = req
            captured['timeout'] = timeout
            class FakeResp:
                status = 204
                def __enter__(self):
                    return self
                def __exit__(self, *a):
                    return False
            return FakeResp()
        original = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        try:
            assert _do_webhook("https://discord.com/api/webhooks/123/abc", {"content": "x"}) is True
        finally:
            urllib.request.urlopen = original
        assert captured['req'].get_header('User-agent') == _USER_AGENT
        assert captured['timeout'] is not None
