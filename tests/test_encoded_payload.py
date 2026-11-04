"""Encoded wrappers are forced through JIT, previewed, and scanned decoded."""
import base64


def b64(text):
    return base64.b64encode(text.encode()).decode()


def wrapper(script):
    return ("bash -c 'echo %s | base64 -d > /tmp/x.sh; "
            "bash /tmp/x.sh; rc=$?; rm -f /tmp/x.sh; exit $rc'" % b64(script))


def transport(command):
    return base64.b64encode(command.encode()).decode()


class TestEncodedPayloadApi:

    def test_safe_payload_is_pending_with_a_decoded_preview(
            self, auth_client, gateway_headers):
        cmd = wrapper("echo SAFE_ENCODED")
        r = auth_client.post("/api/request", json={
            "target_ip": "10.0.0.1",
            "encoded_command": transport(cmd),
            "session_id": "enc-safe",
        }, headers=gateway_headers)
        assert r.status_code == 200
        rows = auth_client.get("/api/requests?session=enc-safe").json()
        row = next(x for x in rows if "base64 -d" in x["command"])
        assert row["status"] == "pending"
        assert row["encoded"] is True
        assert "SAFE_ENCODED" in row["decoded_command"]
        assert "Encoded payload" in row["risk"]

    def test_decoded_hard_block_is_refused(self, auth_client, gateway_headers):
        cmd = wrapper("cat /etc/eshu-exact.txt")
        r = auth_client.post("/api/request", json={
            "target_ip": "10.0.0.1",
            "encoded_command": transport(cmd),
            "session_id": "enc-danger",
        }, headers=gateway_headers)
        assert r.status_code == 403
        assert "Encoded payload blocked" in r.json()["detail"]
        rows = auth_client.get("/api/requests?session=enc-danger").json()
        row = next(x for x in rows if "base64 -d" in x["command"])
        assert row["status"] == "blocked"
        assert row["reason"].startswith("encoded-payload:")
