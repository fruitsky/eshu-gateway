"""Regression tests for the gateway installer template.

The write_poller/write_gateway `__GATEWAY_TOKEN__` substitution must be scoped to
the header assignment ONLY (the poller/gateway headers each carry exactly one
placeholder). Token self-heal was removed from the poller — recovery from a lost
token is now re-enrollment — so nothing else depends on the placeholder.
"""
import os
import re

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def _read(path):
    with open(os.path.join(ROOT, path), "r", encoding="utf-8") as f:
        return f.read()


def test_write_poller_sed_is_scoped_to_header_assignment():
    poller = _read("dashboard/eshu-poller.sh")
    # Apply the exact (fixed) write_poller sed: header assignment line only.
    fixed = re.sub(r'^GATEWAY_TOKEN="__GATEWAY_TOKEN__"',
                   'GATEWAY_TOKEN="tok123"', poller, flags=re.M)
    # Header replaced with the token, and the placeholder is fully consumed.
    assert 'GATEWAY_TOKEN="tok123"' in fixed
    assert "__GATEWAY_TOKEN__" not in fixed
    # The dead self-heal block is gone (recovery is re-enrollment now).
    assert "self_heal" not in poller
    assert 'GATEWAY_TOKEN" = "__GATEWAY_TOKEN__"' not in poller


def test_logger_heartbeat_sends_gateway_token():
    logger = _read("dashboard/eshu-logger.sh")
    # The heartbeat authenticates with the gateway token, read at runtime from
    # the gateway script (never templated into the logger).
    assert "X-Gateway-Token" in logger
    assert "eshu-gateway.sh" in logger
    assert "__GATEWAY_TOKEN__" not in logger


def test_uninstall_report_sends_gateway_token():
    template = _read("dashboard/eshu-installer-template.sh")
    # Token is read from the existing gateway script before it is removed.
    assert "grep -oP '^GATEWAY_TOKEN=\"\\K[^\"]+'" in template
    # The uninstall _report() authenticates its progress posts.
    m = re.search(r'# Helper: report progress to dashboard\n\s*_report\(\) \{(.*?)\n  \}',
                  template, re.S)
    assert m, "uninstall _report() not found"
    assert "X-Gateway-Token" in m.group(1)


def test_template_has_no_global_token_sed():
    template = _read("dashboard/eshu-installer-template.sh")
    # The old global token substitution must be gone from write_gateway/write_poller.
    assert 's|__GATEWAY_TOKEN__|${GATEWAY_TOKEN:-}|g' not in template


def test_uninstall_completes_before_deregistering():
    template = _read("dashboard/eshu-installer-template.sh")
    m = re.search(r'# Handle --uninstall immediately before any other logic.*?\n  exit 0\nfi',
                  template, re.S)
    assert m, "uninstall branch not found"
    branch = m.group(0)
    # The uninstaller must not DELETE its own gateway row before the final
    # progress reports: the DELETE invalidates the gateway token, so the
    # "deregistering"/"complete" posts would 401 and the dashboard modal would
    # poll forever at the last step. The "complete" endpoint deregisters.
    assert 'DELETE "$DASHBOARD_URL/api/gateways/' not in branch
    assert branch.index('_report "deregistering"') < branch.index('_report "complete"')


def test_template_no_longer_requests_or_installs_approver_key():
    template = _read("dashboard/eshu-installer-template.sh")
    # The legacy approver key (operator root SSH key bypassing the gateway) was
    # removed from the product entirely. The template must not request it, parse
    # it as an argument, append it to /root/.ssh/authorized_keys, or carry any
    # transition hook for it — the fleet migration (v15.11) has completed.
    assert 'APPROVER_PUB_KEY="$2"' not in template
    assert "Enter SSH pubkey for APPROVER" not in template
    assert '$APPROVER_PUB_KEY" >> /root/.ssh/authorized_keys' not in template
    assert "Both SSH keys are required" not in template
    assert "LEGACY_APPROVER_PUB_KEY" not in template
    assert "remove_legacy_approver_key" not in template
    # The arg shift must be in place: dashboard URL is now the 2nd positional arg.
    assert 'DASHBOARD_URL="$2"' in template


def test_enroll_script_does_not_reference_approver():
    main = _read("dashboard/main.py")
    # The enrollment one-liner must no longer embed or pass an approver key.
    assert "APPROVER_KEY='" not in main
    assert '"$APPROVER_KEY"' not in main
    # Single-key save signature.
    assert "save_ssh_keys(payload.eshu_key)" in main


def test_schema_has_no_approver_field():
    schema = _read("dashboard/schemas.py")
    assert "approver_key" not in schema


# ── F12: the dashboard key, the host-key pin and the ssh_mode line ──────────
def _installers():
    return (_read("dashboard/eshu-gateway-install.sh"),
            _read("dashboard/static/eshu-gateway-install.sh"))


def test_f12_both_installers_are_regenerated_identically():
    src, static = _installers()
    assert src == static
    assert "--dashboard-key" in src


def test_f12_dashboard_key_line_and_marker():
    src, _ = _installers()
    # The dashboard line's forced command carries exactly the one argument
    # sudoers allows; the comment is what makes a re-run idempotent.
    assert 'eshu-gateway.sh --dashboard-key",no-port-forwarding' in src
    assert "eshu-dashboard" in src
    assert "eshu-gateway-key" in src
    # A pre-F12 line without a comment is dropped by the same prefix match.
    assert 'grep -v \'command="/usr/bin/sudo /usr/local/bin/eshu-gateway.sh\'' in src


def test_f12_sudoers_allows_exactly_two_argv_shapes():
    src, _ = _installers()
    line = next(l for l in src.splitlines()
                if l.startswith("eshu-gateway ALL=(root) NOPASSWD:"))
    assert line.count("/usr/local/bin/eshu-gateway.sh") == 2
    assert line.rstrip().endswith("eshu-gateway.sh --dashboard-key")


def test_f12_register_reports_pin_and_capabilities():
    src, _ = _installers()
    assert '"ssh_host_key":"\'"$HOST_KEY_LINE"\'"' in src
    assert '"ssh_agent_format":"eshu-ssh-agent-v1"' in src
    assert '"ssh_ticket_format":"v2"' in src
    assert src.count('"ssh_agent_format"') == 2   # install and update paths


def test_f12_nonce_dir_is_created_by_the_installer():
    src, _ = _installers()
    assert "install -d -m 700 -o root -g root /var/lib/eshu" in src


def test_f12_installer_warns_about_a_wide_allowed_ssh_from():
    src, _ = _installers()
    assert "is wider" not in src
    assert "may use the dashboard key" in src


def test_f12_mcp_only_drops_the_shared_key_line():
    src, _ = _installers()
    assert '"${GATEWAY_SSH_MODE:-direct+mcp}" != "mcp_only"' in src
    assert "ssh_mode" in src


def test_f12_mac_key_file_is_written_and_removed():
    src, _ = _installers()
    # The gateway reads the token itself (never from argv) ...
    assert "if [ -n \"${GATEWAY_TOKEN:-}\" ] && [ \"$GATEWAY_TOKEN\" != \"None\" ]; then" in src
    assert "mv -f /etc/eshu-token.tmp /etc/eshu-token" in src
    # ... and an uninstall does not leave the key material behind.
    uninstall = src.split("Removing binaries...", 1)[1].split("Removing sudoers", 1)[0]
    assert "rm -f /etc/eshu-token" in uninstall
