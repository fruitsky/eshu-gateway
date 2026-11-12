"""Regression tests for the gateway installer template.

The write_poller/write_gateway `__GATEWAY_TOKEN__` substitution must be scoped to
the header assignment ONLY (the poller/gateway headers each carry exactly one
placeholder). Token self-heal was removed from the poller — recovery from a lost
token is now re-enrollment — so nothing else depends on the placeholder.
"""
import os
import re
import shutil
import stat
import subprocess

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
    # S3: without `""` sudo allows any arguments (sudoers(5): no arguments
    # listed means the user may pass their own). The dashboard line already
    # names its one argument.
    assert '/usr/local/bin/eshu-gateway.sh "",' in line
    assert line.rstrip().endswith("eshu-gateway.sh --dashboard-key")


def test_f12_register_reports_pin_and_capabilities():
    src, _ = _installers()
    assert '"ssh_host_key":"\'"$HOST_KEY_LINE"\'"' in src
    assert '"ssh_agent_format":"eshu-ssh-agent-v1"' in src
    assert '"ssh_ticket_format":"v2"' in src
    # S1: one capability string, used by both registration paths, and the
    # agent format is only claimed when the token file exists on an upgrade.
    assert src.count('\'"$SSH_REG_CAPS"\'') == 1   # RR1: one register_gateway
    assert '[ -f /etc/eshu-token ]' in src
    assert src.count('"ssh_agent_format"') == 1


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


# ── R1: the token-holding files are root-only from creation ────────────────
def _function(src, name):
    """The exact text of one shell function from the template."""
    start = src.index("%s() {" % name)
    end = src.index("\n}\n", start) + 3
    return src[start:end]


def _stub(bindir, name, body):
    path = bindir / name
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(0o755)
    return path


def test_f12_write_gateway_is_root_only_in_every_mode(tmp_path):
    """R1: the script carries GATEWAY_TOKEN (the MAC key), so it is 0700.

    `write_gateway` runs unconditionally — install, reinstall and update all
    take this path — so one run covers every mode. The second run exercises
    the update path (an existing file and its temporary `.bak`).
    """
    template = _read("dashboard/eshu-installer-template.sh")
    body = _function(template, "write_gateway")
    body = body.replace(
        "__GATEWAY_CONTENT__",
        '#!/bin/bash\nGATEWAY_TOKEN="__GATEWAY_TOKEN__"\n')
    target = tmp_path / "eshu-gateway.sh"
    harness = tmp_path / "harness.sh"
    harness.write_text(body + """
GATEWAY="%s"
GATEWAY_TOKEN="tok-123"
DASHBOARD_URL="http://dashboard.test"
GATEWAY_VERSION="v-test"
write_gateway
write_gateway
""" % target)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _stub(bindir, "chown", "exit 0")     # root-only chown is not ours to run
    env = {"PATH": "%s:%s" % (bindir, os.environ.get("PATH", "/usr/bin:/bin"))}
    proc = subprocess.run(["bash", str(harness)], capture_output=True,
                          text=True, env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    assert "tok-123" in target.read_text()
    # The temporary backup held the old token too; it must not be left behind.
    assert not (tmp_path / "eshu-gateway.sh.bak").exists()


def test_f12_token_file_is_created_0600_under_a_permissive_umask(tmp_path):
    """R1: `/etc/eshu-token` is never readable, not even for an instant.

    The block runs under `umask 022`; a `chmod` shim records the mode *at the
    moment* the explicit chmod runs, which is what proves the temp file was
    already 0600 from its creating `umask 077` (the final mode alone would not
    distinguish the two).
    """
    template = _read("dashboard/eshu-installer-template.sh")
    marker = ('if [ -n "${GATEWAY_TOKEN:-}" ] && '
              '[ "$GATEWAY_TOKEN" != "None" ]; then')
    start = template.index(marker)
    end = template.index("\nfi\n", start) + len("\nfi\n")
    token = tmp_path / "eshu-token"
    block = template[start:end].replace("/etc/eshu-token", str(token))

    observed = tmp_path / "modes"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _stub(bindir, "chown", "exit 0")
    chmod = bindir / "chmod"
    chmod.write_text("#!/bin/sh\n"
                     'if [ "$1" = "600" ]; then stat -c "%%a" "$2" >> "%s"; fi\n'
                     'exec %s "$@"\n' % (observed, shutil.which("chmod")))
    chmod.chmod(0o755)

    harness = tmp_path / "harness.sh"
    harness.write_text("umask 022\nGATEWAY_TOKEN='tok-123'\n" + block)
    env = {"PATH": "%s:%s" % (bindir, os.environ.get("PATH", "/usr/bin:/bin"))}
    proc = subprocess.run(["bash", str(harness)], capture_output=True,
                          text=True, env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert token.read_text() == "tok-123\n"
    assert stat.S_IMODE(token.stat().st_mode) == 0o600
    assert observed.read_text().split() == ["600"], observed.read_text()


def test_f12_poller_token_rotation_uses_umask_077():
    """R1: both rotation paths create the temp token file root-only."""
    poller = _read("dashboard/eshu-poller.sh")
    write = ("( umask 077; printf '%s\\n' \"$NEW_TOKEN\" > "
             "/etc/eshu-token.tmp )")
    assert poller.count(write) == 2


# ── RR1: re-registration presents the existing token ─────────────────────

def _register_block(template, token_file):
    start = template.index("# RR1: the existing token is loaded BEFORE registering.")
    end = template.index("\nwrite_gateway() {", start)
    return template[start:end].replace("/etc/eshu-token", str(token_file))


def _run_register(tmp_path, responses, script_token=None, env_token="",
                  enroll="", file_token=None, mode="upgrade"):
    """Run the real block with a stub curl.

    `responses` is one "<body>\n<code>" per call ("" = curl fails). Returns
    (proc, calls, final_token) where calls is a list of argv lists.
    """
    template = _read("dashboard/eshu-installer-template.sh")
    token_file = tmp_path / "eshu-token"
    if file_token is not None:
        token_file.write_text(file_token + "\n")
    gateway = tmp_path / "eshu-gateway.sh"
    if script_token is not None:
        gateway.write_text('#!/bin/bash\nGATEWAY_TOKEN="%s"\n' % script_token)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    replies = tmp_path / "replies"
    replies.mkdir()
    for i, reply in enumerate(responses):
        (replies / str(i)).write_text(reply)
    log = tmp_path / "calls"
    _stub(bindir, "curl", r"""
n=$(ls "%(log)s".* 2>/dev/null | wc -l)
for a in "$@"; do printf '%%s\n' "$a"; done > "%(log)s.$n"
[ -s "%(replies)s/$n" ] || exit 7
cat "%(replies)s/$n"
""" % {"log": log, "replies": replies})
    harness = tmp_path / "harness.sh"
    harness.write_text("""
GATEWAY="%s"
GATEWAY_TOKEN="%s"
ESHU_ENROLL_TOKEN="%s"
DASHBOARD_URL="http://dashboard.test"
TARGET_IP="192.0.2.10"; HOST_NAME="gw"; GATEWAY_VERSION="v-test"
SSH_REG_CAPS='"run_limits":"v1"'
GATEWAY_SSH_MODE="direct+mcp"
mode="%s"
%s
printf 'FINAL=%%s\nMODE=%%s\n' "$GATEWAY_TOKEN" "$GATEWAY_SSH_MODE"
""" % (gateway, env_token, enroll, mode, _register_block(template, token_file)))
    env = {"PATH": "%s:%s" % (bindir, os.environ.get("PATH", "/usr/bin:/bin"))}
    proc = subprocess.run(["bash", str(harness)], capture_output=True,
                          text=True, env=env, timeout=60)
    calls = []
    i = 0
    while (tmp_path / ("calls.%d" % i)).exists():
        calls.append((tmp_path / ("calls.%d" % i)).read_text().splitlines())
        i += 1
    final = re.search(r"^FINAL=(.*)$", proc.stdout, re.M).group(1)
    return proc, calls, final


TOK = "a" * 64
NEW = "b" * 64


def _header(call, name):
    return [a for a in call if a.startswith(name + ":")]


def test_rr1_update_presents_the_installed_token(tmp_path):
    """The bug: an update registered with an empty token and got 401."""
    proc, calls, final = _run_register(
        tmp_path, ['{"status":"ok","gateway_token":"%s"}\n200' % TOK],
        script_token=TOK)
    assert proc.returncode == 0, proc.stderr
    assert len(calls) == 1
    assert _header(calls[0], "X-Gateway-Token") == ["X-Gateway-Token: " + TOK]
    assert final == TOK
    assert "WARNING" not in proc.stdout


def test_rr1_reinstall_presents_the_installed_token(tmp_path):
    proc, calls, final = _run_register(
        tmp_path, ['{"status":"ok","gateway_token":"%s"}\n200' % TOK],
        script_token=TOK, mode="reinstall")
    assert _header(calls[0], "X-Gateway-Token") == ["X-Gateway-Token: " + TOK]
    assert final == TOK


def test_rr1_token_file_is_the_fallback(tmp_path):
    proc, calls, final = _run_register(
        tmp_path, ['{"status":"ok","gateway_token":"%s"}\n200' % TOK],
        script_token="__GATEWAY_TOKEN__", file_token=TOK)
    assert _header(calls[0], "X-Gateway-Token") == ["X-Gateway-Token: " + TOK]
    assert final == TOK


def test_rr1_none_and_garbage_are_never_sent(tmp_path):
    proc, calls, final = _run_register(
        tmp_path, ['{"detail":"x"}\n401'], script_token="None",
        file_token="not a token; rm -rf /")
    assert _header(calls[0], "X-Gateway-Token") == ["X-Gateway-Token: "]
    assert final == ""


def test_rr1_a_refused_registration_keeps_the_token_and_warns(tmp_path):
    proc, calls, final = _run_register(
        tmp_path, ['{"detail":"no"}\n401'], script_token=TOK)
    assert len(calls) == 1            # no enrollment token: no second try
    assert final == TOK
    assert "WARNING" in proc.stdout and "HTTP 401" in proc.stdout


def test_rr1_an_unreachable_dashboard_keeps_the_token_and_warns(tmp_path):
    proc, calls, final = _run_register(tmp_path, [""], script_token=TOK)
    assert final == TOK
    assert "HTTP 000" in proc.stdout


def test_rr1_stale_token_retries_once_with_the_enrollment_token(tmp_path):
    """A leftover token from a gateway the operator removed: the enrollment
    token decides, and the new token replaces the stale one."""
    proc, calls, final = _run_register(
        tmp_path,
        ['{"detail":"bad token"}\n401',
         '{"status":"ok","gateway_token":"%s"}\n200' % NEW],
        script_token=TOK, enroll="enroll-123", mode="reinstall")
    assert len(calls) == 2
    assert _header(calls[0], "X-Gateway-Token") == ["X-Gateway-Token: " + TOK]
    assert _header(calls[1], "X-Gateway-Token") == ["X-Gateway-Token: "]
    assert _header(calls[1], "X-Enrollment-Token") == ["X-Enrollment-Token: enroll-123"]
    assert final == NEW


def test_rr1_a_200_without_a_valid_token_keeps_ours(tmp_path):
    proc, calls, final = _run_register(
        tmp_path, ['{"status":"ok","gateway_token":"None"}\n200'],
        script_token=TOK)
    assert final == TOK


def test_rr1_ssh_mode_comes_only_from_a_successful_answer(tmp_path):
    proc, calls, _ = _run_register(
        tmp_path, ['{"status":"ok","gateway_token":"%s","ssh_mode":"mcp_only"}\n200' % TOK],
        script_token=TOK, mode="reinstall")
    assert "MODE=mcp_only" in proc.stdout


def test_rr1_token_is_loaded_before_the_first_registration():
    src = _read("dashboard/eshu-installer-template.sh")
    load = src.index("# RR1: the existing token is loaded BEFORE registering.")
    call = src.index('register_gateway "${GATEWAY_TOKEN:-}"')
    assert load < call
    assert src.count("/api/register") == 1
