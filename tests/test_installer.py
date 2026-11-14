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
    # X7: only Build is committed; Edge and Fleet are runtime copies the
    # dashboard makes from it, so there is one installer to check.
    src = _read("dashboard/eshu-gateway-install.sh")
    return src, src


def test_x7_only_build_is_committed():
    import subprocess
    tracked = subprocess.run(
        ["git", "ls-files", "dashboard/static"], cwd=ROOT, capture_output=True,
        text=True, check=True).stdout.split()
    assert not [t for t in tracked if t.endswith("eshu-gateway-install.sh")]
    ignore = _read(".gitignore")
    for entry in ("dashboard/installer-channels/",
                  "dashboard/static/eshu-gateway-install.sh",
                  "dashboard/static/dev/"):
        assert entry in ignore
    gen = _read("dashboard/gen_installer.py")
    assert 'write("static/' not in gen
    assert "--dashboard-key" in _installers()[0]


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
_stdin=""
for a in "$@"; do
  case "$a" in @-) _stdin=$(cat) ;; esac
done
for a in "$@"; do printf '%%s\n' "$a"; done > "%(log)s.$n"
if [ -n "$_stdin" ]; then
  printf '@stdin@%%s\n' "$(printf '%%s' "$_stdin" | base64 -w0)" >> "%(log)s.$n"
fi
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
    """Header lines the stub received on stdin (BE3 moved them off argv)."""
    import base64
    found = []
    for arg in call:
        if not arg.startswith("@stdin@"):
            continue
        text = base64.b64decode(arg[len("@stdin@"):]).decode()
        found.extend(line for line in text.splitlines()
                     if line.lower().startswith(name.lower() + ":"))
    return found


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


# ── BE1: a failed registration never rewrites authorized_keys ─────────────

def _key_block(template, home):
    start = template.index("# SSH key setup only for install/reinstall")
    end = template.index('\necho ""\necho "✅ Gateway', start)
    return template[start:end].replace("/home/$USER", str(home))


def _run_keys(tmp_path, register_ok, ssh_mode, mode="reinstall",
              dash_pub="ssh-ed25519 AAAAdash", original=None):
    template = _read("dashboard/eshu-installer-template.sh")
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    ak = home / ".ssh" / "authorized_keys"
    if original is None:
        original = ("ssh-ed25519 AAAAother operator-key\n"
                    'command="/usr/bin/sudo /usr/local/bin/eshu-gateway.sh",restrict '
                    "ssh-ed25519 AAAAold eshu-gateway-key\n")
    ak.write_text(original)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _stub(bindir, "chown", "exit 0")
    harness = tmp_path / "keys.sh"
    harness.write_text("""
USER="eshu-gateway"; mode="%s"
REGISTER_OK="%s"; REGISTER_STATUS="%s"
GATEWAY_SSH_MODE="%s"; ALLOWED_SSH_FROM=""
GATEWAY_PUB_KEY="ssh-ed25519 AAAAnew"
DASHBOARD_SSH_PUB="%s"
%s
""" % (mode, register_ok, "200" if register_ok else "000", ssh_mode,
       dash_pub, _key_block(template, home)))
    env = {"PATH": "%s:%s" % (bindir, os.environ.get("PATH", "/usr/bin:/bin"))}
    proc = subprocess.run(["bash", str(harness)], capture_output=True,
                          text=True, env=env, timeout=60, stdin=subprocess.DEVNULL)
    return proc, original, ak.read_text()


def test_be3_the_register_token_is_never_in_curl_argv(tmp_path):
    """BE3: the installer's register call keeps both tokens off argv."""
    token = "tok-secret-value"
    proc, calls, _ = _run_register(
        tmp_path, ['{"status":"ok"}\n200'], script_token=token,
        enroll="enroll-secret")
    assert calls, "no curl call recorded"
    for argv in calls:
        joined = " ".join(a for a in argv if not a.startswith("@stdin@"))
        assert token not in joined, joined
        assert "enroll-secret" not in joined, joined
        assert "X-Gateway-Token:" not in joined, joined
        assert "X-Enrollment-Token:" not in joined, joined


def test_be1_failed_registration_leaves_authorized_keys_untouched(tmp_path):
    proc, original, after = _run_keys(tmp_path, "", "direct+mcp")
    assert proc.returncode == 0, proc.stderr
    assert after == original
    assert "SSH keys were NOT changed" in proc.stdout


def test_be1_failed_registration_never_restores_the_shared_line(tmp_path):
    """The attack: make the dashboard unreachable during a reinstall of an
    mcp_only gateway. The default mode must not bring the shared key back."""
    proc, original, after = _run_keys(tmp_path, "", "direct+mcp")
    assert "AAAAnew" not in after


def test_be1_successful_registration_writes_the_keys(tmp_path):
    proc, _, after = _run_keys(tmp_path, "1", "direct+mcp")
    assert proc.returncode == 0, proc.stderr
    assert "operator-key" in after                 # foreign lines are kept
    assert "AAAAold" not in after                  # our old line is replaced
    assert "AAAAnew eshu-gateway-key" in after
    assert "AAAAdash eshu-dashboard" in after


def test_be1_mcp_only_after_a_200_gets_no_shared_line(tmp_path):
    proc, _, after = _run_keys(tmp_path, "1", "mcp_only")
    assert "eshu-gateway-key" not in after
    assert "AAAAdash eshu-dashboard" in after


def test_be1_a_failed_registration_does_not_set_ssh_mode(tmp_path):
    src = _read("dashboard/eshu-installer-template.sh")
    block = src[src.index("REGISTER_OK=\"\"\nif [ -n \"$DASHBOARD_URL\" ]"):
                src.index("# SSH key setup only for install/reinstall")]
    failure = block[block.index("  else\n    # Keep the token"):]
    assert "GATEWAY_SSH_MODE=" not in failure.split("\n  fi\n")[0]


def test_be2_build_carries_the_fix():
    """BE2, after X7: Edge is a byte copy of Build made by Seed Edge (see
    tests/test_golden.py), so the fix only has to be in Build."""
    build = _read("dashboard/eshu-gateway-install.sh")
    assert "# RR1: the existing token is loaded BEFORE registering." in build


# ── BE4: refresh only the eshu-dashboard line on update ────────────────────

OLD_DASH = ('from="10.0.0.0/8",'
            'command="/usr/bin/sudo /usr/local/bin/eshu-gateway.sh '
            '--dashboard-key",restrict ssh-ed25519 AAAAolddash eshu-dashboard\n')
OLD_GW = ('from="192.0.2.0/24",'
          'command="/usr/bin/sudo /usr/local/bin/eshu-gateway.sh",restrict '
          'ssh-ed25519 AAAAold eshu-gateway-key\n')


def test_be4_update_replaces_only_the_dashboard_line(tmp_path):
    original = "ssh-ed25519 AAAAother operator-key\n" + OLD_GW + OLD_DASH
    proc, _, after = _run_keys(tmp_path, "1", "direct+mcp", mode="update",
                               original=original)
    assert proc.returncode == 0, proc.stderr
    # Every line that is not ours is byte-identical, and so is the shared line.
    assert "ssh-ed25519 AAAAother operator-key\n" in after
    assert OLD_GW in after
    assert "AAAAolddash" not in after
    assert "AAAAdash eshu-dashboard" in after
    # Only two lines changed: the old dashboard line went, the new one came.
    assert len(after.splitlines()) == 3
    assert after.count("eshu-dashboard") == 1


def test_be4_update_copies_the_dashboard_lines_from(tmp_path):
    original = OLD_GW + OLD_DASH
    _, _, after = _run_keys(tmp_path, "1", "direct+mcp", mode="update",
                            original=original)
    dash = [line for line in after.splitlines() if line.endswith("eshu-dashboard")][0]
    assert dash.startswith('from="10.0.0.0/8",')


def test_be4_update_copies_from_from_the_gateway_line_when_there_is_none(
        tmp_path):
    original = "ssh-ed25519 AAAAother operator-key\n" + OLD_GW
    _, _, after = _run_keys(tmp_path, "1", "direct+mcp", mode="update",
                            original=original)
    dash = [line for line in after.splitlines() if line.endswith("eshu-dashboard")][0]
    assert dash.startswith('from="192.0.2.0/24",')


def test_be4_update_without_a_200_changes_nothing(tmp_path):
    original = OLD_GW + OLD_DASH
    _, _, after = _run_keys(tmp_path, "", "direct+mcp", mode="update",
                            original=original)
    assert after == original


def test_be4_update_with_an_empty_key_changes_nothing(tmp_path):
    original = OLD_GW + OLD_DASH
    proc, _, after = _run_keys(tmp_path, "1", "direct+mcp", mode="update",
                               dash_pub="", original=original)
    assert proc.returncode == 0, proc.stderr
    assert after == original


def test_be4_update_with_a_malformed_key_changes_nothing(tmp_path):
    original = OLD_GW + OLD_DASH
    for index, bad in enumerate(("not-a-key",
                                 "ssh-ed25519 AAAA\ncommand=/bin/sh eshu-dashboard",
                                 "ssh-rsa AAAAx eshu-dashboard")):
        sub = tmp_path / ("case%d" % index)
        sub.mkdir()
        _, _, after = _run_keys(sub, "1", "direct+mcp", mode="update",
                                dash_pub=bad, original=original)
        assert after == original, bad


def _block_harness(tmp_path, block):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    _stub(bindir, "chown", "exit 0")
    harness = tmp_path / "block.sh"
    harness.write_text('USER="eshu-gateway"\n' + block + "\n")
    env = {"PATH": "%s:%s" % (bindir, os.environ.get("PATH", "/usr/bin:/bin"))}
    return subprocess.run(["bash", str(harness)], capture_output=True,
                          text=True, env=env, timeout=60,
                          stdin=subprocess.DEVNULL)


def test_be4_a_symlinked_authorized_keys_is_refused(tmp_path):
    template = _read("dashboard/eshu-installer-template.sh")
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    victim = tmp_path / "victim"
    victim.write_text("do not touch\n")
    (home / ".ssh" / "authorized_keys").symlink_to(victim)
    block = _key_block(template, home)
    proc = _block_harness(tmp_path, 'mode="update"\nREGISTER_OK="1"\n'
                                    'DASHBOARD_SSH_PUB="ssh-ed25519 AAAAdash"\n'
                                    + block)
    assert proc.returncode == 0, proc.stderr
    assert victim.read_text() == "do not touch\n"
    assert (home / ".ssh" / "authorized_keys").is_symlink()
    assert "REFUSED" in proc.stdout


def test_be4_a_symlinked_ssh_directory_is_refused(tmp_path):
    template = _read("dashboard/eshu-installer-template.sh")
    real = tmp_path / "elsewhere"
    real.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    (home / ".ssh").symlink_to(real)
    block = _key_block(template, home)
    proc = _block_harness(tmp_path, 'mode="reinstall"\nREGISTER_OK="1"\n'
                                    'GATEWAY_SSH_MODE="direct+mcp"\n'
                                    'ALLOWED_SSH_FROM=""\n'
                                    'GATEWAY_PUB_KEY="ssh-ed25519 AAAAnew"\n'
                                    'DASHBOARD_SSH_PUB="ssh-ed25519 AAAAdash"\n'
                                    + block)
    assert proc.returncode == 0, proc.stderr
    assert not (real / "authorized_keys").exists()
    assert "REFUSED" in proc.stdout


def test_bh1_an_unreadable_from_is_refused(tmp_path):
    """BH1: a from= that ak_from cannot parse must not become unrestricted.

    The line keeps its from= after command=; ak_from only reads a from= that
    starts the line. Writing the dashboard key without it would open the
    gateway to every source.
    """
    weird = ('command="/usr/bin/sudo /usr/local/bin/eshu-gateway.sh",'
             'from="192.0.2.0/24",restrict '
             'ssh-ed25519 AAAAold eshu-gateway-key\n')
    proc, _, after = _run_keys(tmp_path, "1", "direct+mcp", mode="update",
                               original=weird)
    assert proc.returncode == 0, proc.stderr
    assert after == weird
    assert "could not be read" in proc.stdout


def test_bj1_a_later_unreadable_from_also_refuses(tmp_path):
    """BJ1: the check is on every Eshu line, not just the first.

    The gateway line is unrestricted; the dashboard line was restricted by
    hand with its options in another order. Rewriting the dashboard line
    without its from= would drop that restriction.
    """
    original = (
        'command="/usr/bin/sudo /usr/local/bin/eshu-gateway.sh",restrict '
        'ssh-ed25519 AAAAold eshu-gateway-key\n'
        'command="/usr/bin/sudo /usr/local/bin/eshu-gateway.sh --dashboard-key",'
        'from="192.0.2.0/24",restrict ssh-ed25519 AAAAdash eshu-dashboard\n')
    proc, _, after = _run_keys(tmp_path, "1", "direct+mcp", mode="update",
                               original=original)
    assert proc.returncode == 0, proc.stderr
    assert after == original
    assert "could not be read" in proc.stdout


def test_bj1_a_leading_from_on_every_line_still_updates(tmp_path):
    """The legal case: both Eshu lines lead with a parseable from=."""
    original = (
        'from="192.0.2.0/24",command="/usr/bin/sudo /usr/local/bin/eshu-gateway.sh",'
        'restrict ssh-ed25519 AAAAold eshu-gateway-key\n'
        'from="192.0.2.0/24",command="/usr/bin/sudo /usr/local/bin/eshu-gateway.sh '
        '--dashboard-key",restrict ssh-ed25519 AAAAdash eshu-dashboard\n')
    proc, _, after = _run_keys(tmp_path, "1", "direct+mcp", mode="update",
                               original=original)
    assert proc.returncode == 0, proc.stderr
    assert "could not be read" not in proc.stdout
    assert 'from="192.0.2.0/24"' in after
    assert after.count("eshu-dashboard") == 1


def test_bh1_no_eshu_line_means_no_change(tmp_path):
    """BH1: an old mcp_only install has no Eshu line to copy from= out of."""
    proc, _, after = _run_keys(tmp_path, "1", "mcp_only", mode="update",
                               original="")
    assert proc.returncode == 0, proc.stderr
    assert after == ""
    assert "reinstall to add the dashboard key" in proc.stdout


def test_bh1_only_foreign_lines_means_no_change(tmp_path):
    proc, _, after = _run_keys(tmp_path, "1", "direct+mcp", mode="update",
                               original="ssh-ed25519 AAAAother operator-key\n")
    assert proc.returncode == 0, proc.stderr
    assert after == "ssh-ed25519 AAAAother operator-key\n"
    assert "reinstall to add the dashboard key" in proc.stdout


def test_bh1_a_gateway_line_without_from_stays_unrestricted(tmp_path):
    """The legal case: the Eshu line has no from=, so there is nothing to
    copy and the dashboard line is written without one."""
    original = ('command="/usr/bin/sudo /usr/local/bin/eshu-gateway.sh",restrict '
                'ssh-ed25519 AAAAold eshu-gateway-key\n')
    proc, _, after = _run_keys(tmp_path, "1", "direct+mcp", mode="update",
                               original=original)
    assert proc.returncode == 0, proc.stderr
    assert "from=" not in after
    assert "AAAAdash eshu-dashboard" in after


def test_be4_the_install_path_does_not_use_a_planted_tmp_symlink(tmp_path):
    template = _read("dashboard/eshu-installer-template.sh")
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    victim = tmp_path / "victim"
    victim.write_text("do not touch\n")
    (home / ".ssh" / "authorized_keys.tmp").symlink_to(victim)
    block = _key_block(template, home)
    proc = _block_harness(tmp_path, 'mode="reinstall"\nREGISTER_OK="1"\n'
                                    'GATEWAY_SSH_MODE="direct+mcp"\n'
                                    'ALLOWED_SSH_FROM=""\n'
                                    'GATEWAY_PUB_KEY="ssh-ed25519 AAAAnew"\n'
                                    'DASHBOARD_SSH_PUB="ssh-ed25519 AAAAdash"\n'
                                    + block)
    assert proc.returncode == 0, proc.stderr
    assert victim.read_text() == "do not touch\n"
    text = (home / ".ssh" / "authorized_keys").read_text()
    assert "AAAAnew eshu-gateway-key" in text
    assert "AAAAdash eshu-dashboard" in text


def test_be4_no_fixed_authorized_keys_tmp_name_remains():
    src = _read("dashboard/eshu-installer-template.sh")
    assert "authorized_keys.tmp" not in src
    assert "mktemp \"$ak_dir/.authorized_keys" in src
    assert "eshu_safe_replace" in src


def test_be4_safe_replace_refuses_a_symlinked_target(tmp_path):
    src = _read("dashboard/eshu-installer-template.sh")
    start = src.index("# BE4: replace a file through a private temp")
    end = src.index("\n}\n", src.index("eshu_safe_replace()")) + 3
    helper = src[start:end]
    victim = tmp_path / "victim"
    victim.write_text("do not touch\n")
    link = tmp_path / "ak"
    link.symlink_to(victim)
    harness = tmp_path / "helper.sh"
    harness.write_text("#!/bin/bash\n" + helper +
                       '\necho "new" | eshu_safe_replace "%s"\n' % link)
    proc = subprocess.run(["bash", str(harness)], capture_output=True,
                          text=True, timeout=30, stdin=subprocess.DEVNULL)
    assert victim.read_text() == "do not touch\n"
    assert link.is_symlink()
    # And a normal file is replaced in place, keeping its mode.
    target = tmp_path / "plain"
    target.write_text("old\n")
    target.chmod(0o600)
    harness.write_text("#!/bin/bash\n" + helper +
                       '\necho "new" | eshu_safe_replace "%s"\n' % target)
    subprocess.run(["bash", str(harness)], capture_output=True, text=True,
                   timeout=30, stdin=subprocess.DEVNULL)
    assert target.read_text() == "new\n"
    assert oct(target.stat().st_mode & 0o777) == "0o600"
# ── BG1: curl 7.55 for `-H @-` ─────────────────────────────────────────────

def _run_curl_check(tmp_path, version, with_curl=True):
    """Run the real preflight function with a stub `curl --version`."""
    src = _read("dashboard/eshu-installer-template.sh")
    body = _function(src, "check_curl_version")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    if with_curl:
        _stub(bindir, "curl",
              'if [ "$1" = "--version" ]; then printf "curl %s (x86_64-pc-linux-gnu) libcurl/%s\n" "%s" "%s"; fi'
              % (version, version, version, version))
        path = "%s:%s" % (bindir, os.environ.get("PATH", "/usr/bin:/bin"))
    else:
        # Only the two text tools the check uses, and no curl at all.
        for tool in ("bash", "head", "grep"):
            (bindir / tool).symlink_to(shutil.which(tool))
        path = str(bindir)
    harness = tmp_path / "harness.sh"
    harness.write_text(body + "\ncheck_curl_version\n")
    env = {"PATH": path}
    return subprocess.run(["bash", str(harness)], capture_output=True,
                          text=True, env=env, timeout=60)


def test_bg1_curl_755_passes(tmp_path):
    proc = _run_curl_check(tmp_path, "7.55.0")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_bg1_a_newer_curl_passes(tmp_path):
    proc = _run_curl_check(tmp_path, "8.5.0")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_bg1_curl_752_is_refused_with_the_version_and_a_reason(tmp_path):
    """BG1: older curl silently drops a `-H @-` header, so every call 401s.

    The installer must stop before touching the host, and say what it found.
    """
    proc = _run_curl_check(tmp_path, "7.52.0")
    assert proc.returncode == 1, proc.stdout
    assert "requires curl 7.55 or newer" in proc.stdout
    assert "7.52.0" in proc.stdout
    assert "Nothing on this host has been changed" in proc.stdout


def test_bg1_curl_750_is_refused(tmp_path):
    proc = _run_curl_check(tmp_path, "7.50.1")
    assert proc.returncode == 1
    assert "7.50.1" in proc.stdout


def test_bg1_curl_754_is_refused(tmp_path):
    """The security reviewer's exact case: 7.54 gets a clear refusal."""
    proc = _run_curl_check(tmp_path, "7.54.0")
    assert proc.returncode == 1, proc.stdout
    assert "7.54.0" in proc.stdout


def test_bg1_no_write_can_run_before_the_check():
    """The check runs before anything on the host can change.

    Security's condition was that a 7.54 host leaves every installed file
    byte-identical. This proves the stronger property: the script has no
    top-level write before the check, so reaching it is the only way past
    it. Function definitions are skipped, because defining
    `eshu_safe_replace`/`write_gateway` writes nothing.
    """
    src = _read("dashboard/eshu-installer-template.sh")
    idx = src.index("if ! check_curl_version; then exit 1; fi")
    depth = 0
    for line in src[:idx].splitlines():
        stripped = line.strip()
        if depth == 0:
            if stripped.startswith("#"):
                continue
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\(\)\s*\{", stripped):
                depth = max(0, stripped.count("{") - stripped.count("}"))
                continue
            for primitive in ("mkdir ", "useradd ", "chown ", "chmod ", "rm ",
                              "mv ", "cp ", "install -", "systemctl enable",
                              "systemctl start", "systemctl daemon-reload",
                              "eshu_safe_replace ", "write_gateway;", "cat >"):
                assert primitive not in line, (primitive, line)
        else:
            depth = max(0, depth + stripped.count("{") - stripped.count("}"))


def test_bg1_a_missing_curl_is_refused(tmp_path):
    proc = _run_curl_check(tmp_path, "", with_curl=False)
    assert proc.returncode == 1
    assert "curl was not found" in proc.stdout


def test_bg1_the_check_runs_in_install_reinstall_and_update_before_changes():
    """The version check sits in the preflight, exempting only --uninstall."""
    src = _read("dashboard/eshu-installer-template.sh")
    check = 'if [ "$UNINSTALL" != "yes" ]; then\n  if ! check_curl_version; then exit 1; fi\nfi'
    assert check in src
    # Before the uninstall branch (the first thing that could change a host
    # is after both), and before any write.
    assert src.index("check_curl_version; then exit 1") < src.index(
        "# Handle --uninstall immediately before any other logic")
    assert src.index("check_curl_version; then exit 1") < src.index(
        "useradd -m -s /bin/bash")
    # The mode flags are parsed above it, so install, reinstall, update and
    # migrate all pass through it.
    assert src.index('if [[ "${1:-}" == "--update" ]]') < src.index(
        "check_curl_version; then exit 1")
