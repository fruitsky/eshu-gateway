"""F4: the read-only lane — off by default, shadow first.

A JIT request is eligible when the gateway has the lane in shadow/on, the fleet
is not frozen, the gateway is not Zero-Trust, the command is ≤ 8 kB, the policy
verdict is `auto_approved` or a `jit` with no `detail_type` or `shell_meta`
(SC7, round 7 Q1), and `read_policy.is_read_ssh` proves every step only reads.
It holds no read rules of its own: the verdict comes from
`core/read_policy.py` (§ 2.6a).
"""
from core import read_policy

READ_LANE_MODES = ('off', 'shadow', 'on')
MAX_COMMAND_BYTES = 8192


def eligible(breakdown, verdict, command: str = '', mode: str = 'shadow',
             frozen: bool = False, zero_trust: bool = False):
    """(bool, reason). The reason is a stable token for tests and the audit."""
    if mode not in ('shadow', 'on'):
        return False, 'off'
    if frozen:
        return False, 'frozen'
    if zero_trust:
        return False, 'zero-trust'
    if len((command or '').encode('utf-8')) > MAX_COMMAND_BYTES:
        return False, 'too-long'
    action = (verdict or {}).get('action')
    if action == 'blocked' or (verdict or {}).get('tier') == 'fatal':
        return False, 'blocked'
    # SC7 (round 7 Q1): `auto_approved`, or a `jit` with no detail type or
    # `shell_meta`. `shell_meta` only means the gateway cannot vouch for the
    # literal string; the lane parses it step by step, and SC9/S* still apply
    # to every step. Anything else — `self_protection_jit` today, and anything
    # added later — needs a human and wins over the read rules.
    if action not in ('jit', 'auto_approved'):
        return False, 'verdict:' + str(action or '-')
    if action == 'jit' and (verdict or {}).get('detail_type') not in (None,
                                                                     'shell_meta'):
        return False, 'verdict:' + str((verdict or {}).get('detail_type'))
    return read_policy.is_read_ssh(breakdown)
