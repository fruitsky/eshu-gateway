"""Chunk 2 parity: dashboard blocklist/policy verdicts match the gateway.

The dashboard must never report "allowed" for a command the gateway hard-blocks
or auto-approves with a metacharacter bypass.
"""
import pytest

from core.cmd_blocklist import hard_block_match
from core.policy_eval import evaluate_policy_verdict

# Same list as tests/test_gateway_script.py's chunk 2 self-protection cases.
SELF_PROTECTION_COMMANDS = [
    "cat /run/eshu.tickets",
    "ls /etc/eshu/features.d",
    "cat /etc//eshu-exact.txt",
    "cat /etc/./eshu-exact.txt",
    "echo x > /etc/eshu/dashboard_url",
    "cat /usr/local/bin/eshu-gateway.sh",
    "cat /var/run/eshu.tickets",
    "cat eshu.db",
    "cat eshu-session-key",
    "cat /etc/sudoers.d/eshu",
    "cat /etc/eshu/features.d",
    "cat /etc/eshu/dashboard_url",
]

# Chunk R1: broad matches that must never be FATAL.
SELF_PROTECTION_JIT_COMMANDS = [
    "cat /etc/esh*",
    "cat ~/.ssh/authorized_keys",
    "ls -la ~/.ssh/authorized_keys",
    "cat /home/eshu/.ssh/authorized_keys",
]

R_FALSE_POSITIVES = [
    "systemctl status *mesh*",
    'find /home -name "*refresh*"',
    "[ -f /tmp/threshold ] && echo y",
    'curl -s "http://x/api?fresh=1"',
    "grep -r Refresh /var/log/*",
]

PLAIN_COMMANDS = ["uptime", "df -h", "cat /etc/hosts", "journalctl -n 5"]


@pytest.mark.parametrize("command", SELF_PROTECTION_COMMANDS)
def test_sec_chunk2_hard_block_parity(command):
    assert hard_block_match(command) is not None


@pytest.mark.parametrize("command", PLAIN_COMMANDS)
def test_sec_chunk2_plain_command_is_not_hard_blocked(command):
    assert hard_block_match(command) is None


def test_sec_chunk2_path_normalisation():
    from core.cmd_blocklist import normalize_command_paths
    assert normalize_command_paths("/etc//eshu-exact.txt") == "/etc/eshu-exact.txt"
    assert normalize_command_paths("/etc/./eshu-exact.txt") == "/etc/eshu-exact.txt"
    assert normalize_command_paths("cat /a//b/./c") == "cat /a/b/c"


META_COMMANDS = [
    "uptime; id",
    "uptime && id",
    "uptime | sh",
    "uptime `id`",
    "uptime $(id)",
    "uptime > meta.out",
    "uptime\nid",
    "uptime \\ id",
    "journalctl -n 1; id",
]


@pytest.mark.parametrize("command", META_COMMANDS)
def test_sec_chunk2_policy_eval_metachar_is_jit(command):
    verdict = evaluate_policy_verdict(
        command,
        exact_whitelist=command,
        regex_whitelist="^uptime" if command.startswith("uptime") else "^journalctl .*",
        regex_blacklist="",
    )
    assert verdict["action"] == "jit"
    assert verdict["detail_type"] == "shell_meta"


@pytest.mark.parametrize("command", ["uptime", "df -h"])
def test_sec_chunk2_policy_eval_plain_command_still_auto_approved(command):
    verdict = evaluate_policy_verdict(
        command,
        exact_whitelist=command,
        regex_whitelist="",
        regex_blacklist="",
    )
    assert verdict["action"] == "auto_approved"


def test_sec_chunk2_policy_eval_blocklist_still_wins_over_meta_gate():
    verdict = evaluate_policy_verdict(
        "uptime; id",
        exact_whitelist="uptime; id",
        regex_whitelist="^uptime",
        regex_blacklist="id",
    )
    assert verdict["action"] == "blocked"


# ── Chunk R1: dashboard parity for the narrowed FATAL / new JIT tier ──────
@pytest.mark.parametrize("command", SELF_PROTECTION_JIT_COMMANDS)
def test_sec_R_self_protection_jit_parity(command):
    from core.cmd_blocklist import self_protection_jit_match
    assert hard_block_match(command) is None
    assert self_protection_jit_match(command) is not None


@pytest.mark.parametrize("command", R_FALSE_POSITIVES)
def test_sec_R_false_positive_parity(command):
    from core.cmd_blocklist import self_protection_jit_match
    assert hard_block_match(command) is None
    assert self_protection_jit_match(command) is None


@pytest.mark.parametrize("command", ["cat /etc/esh*", "cat ~/.ssh/authorized_keys"])
def test_sec_R_policy_eval_self_protection_is_jit(command):
    verdict = evaluate_policy_verdict(
        command,
        exact_whitelist=command,
        regex_whitelist="^cat ",
        regex_blacklist="",
    )
    assert verdict["action"] == "jit"
    assert verdict["detail_type"] == "self_protection_jit"
