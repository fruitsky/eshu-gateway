"""Policy verdict evaluation shared by /api/policies/test and the policy
what-if preview.

Returns a simple verdict — what the gateway/dashboard would do with a command
under a given (exact allow, regex allow, block) policy — plus the reason.

Matching order mirrors the gateway stages:
  1. hard block (self-protection + evasion) -> blocked / fatal
  2. blocklist (literal substring, ^/$ anchors stripped) -> blocked
  3. exact allowlist -> auto_approved
  4. regex allowlist -> auto_approved
  else -> jit
"""
import re

from core.cmd_blocklist import hard_block_match, blocklist_substring_match


def _lines(text):
    return [l for l in (text or '').split('\n') if l.strip()]


def evaluate_policy_verdict(command, exact_whitelist, regex_whitelist, regex_blacklist):
    """Return a verdict dict: {action, tier, detail_type, matched_pattern, reason}.

    action is one of 'blocked' | 'auto_approved' | 'jit'. tier is 'fatal' for
    the non-relaxable self-protection/evasion block, else None.
    """
    fatal = hard_block_match(command)
    if fatal:
        return {
            "action": "blocked",
            "tier": "fatal",
            "detail_type": "hard_blocklist",
            "matched_pattern": fatal,
            "reason": f"Hardcoded core blocklist (pattern: {fatal})",
        }

    for pattern in _lines(regex_blacklist):
        if blocklist_substring_match(pattern, command):
            return {
                "action": "blocked",
                "tier": None,
                "detail_type": "regex_blacklist",
                "matched_pattern": pattern,
                "reason": f"Blocklist match (pattern: {pattern})",
            }

    exact_lines = _lines(exact_whitelist)
    if command in exact_lines:
        return {
            "action": "auto_approved",
            "tier": None,
            "detail_type": "exact_whitelist",
            "matched_pattern": command,
            "reason": "Exact allowlist match",
        }

    for pattern in _lines(regex_whitelist):
        try:
            if re.search(pattern, command):
                return {
                    "action": "auto_approved",
                    "tier": None,
                    "detail_type": "regex_whitelist",
                    "matched_pattern": pattern,
                    "reason": f"Regex allowlist match (pattern: {pattern})",
                }
        except re.error:
            pass

    return {
        "action": "jit",
        "tier": None,
        "detail_type": None,
        "matched_pattern": None,
        "reason": "No policy matched — requires JIT approval",
    }
