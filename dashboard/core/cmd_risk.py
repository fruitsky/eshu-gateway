"""Static "what could go wrong" risk hints for the dashboard.

`get_cmd_risk(cmd)` returns a one-line human-readable risk hint for a command,
or None. `get_dry_run_suggestion(cmd)` returns a dry-run variant of the command
when a safe one is known (apt-get/npm/pip), or None.

These are static, curated, word-boundary aware rules — no model.
"""

import re

RISK_RULES = [
    (r'\bsystemctl\s+(restart|stop)\b', 'Restarts/stops a service — brief outage'),
    (r'\bdocker\s+(rm|rmi|volume\s+rm)\b', 'Removes containers/images — data loss'),
    (r'journalctl\s+--vacuum', 'Rotates/truncates logs'),
    (r'\btruncate\b', 'Rotates/truncates logs'),
    (r'\b(apt-get|apt|dnf|yum)\s+install\b', 'Installs packages — modifies the system'),
    (r'\b(pip|pip3|npm|yarn)\s+install\b', 'Installs packages — modifies the system'),
    (r'\brm\b', 'Deletes files — irreversible'),
    (r'\bkill\s+-9\b', 'Force-kills a process — no graceful shutdown'),
    (r'\bchmod\s+(-R\s+)?777\b', 'Makes files world-writable — security exposure'),
    (r'\buserdel\b', 'Deletes a user account'),
    (r'\bfind\s+\S+\s+.*\s+-delete\b', 'Recursively deletes files'),
]

DRY_RUN_SUGGESTIONS = [
    (r'\bapt-get\s+install\b', lambda c: re.sub(r'\bapt-get\s+install\b', 'apt-get --dry-run install', c)),
    (r'\bnpm\s+install\b', lambda c: re.sub(r'\bnpm\s+install\b', 'npm install --dry-run', c)),
    (r'\bpip3?\s+install\b', lambda c: re.sub(r'\bpip3?\s+install\b', 'pip install --dry-run', c)),
]


def get_cmd_risk(cmd: str):
    """Return a one-line risk hint for the command, or None if it looks safe.
    Commands run with --dry-run don't modify anything, so they get no hint."""
    if not cmd or '--dry-run' in cmd:
        return None
    for pattern, hint in RISK_RULES:
        if re.search(pattern, cmd):
            return hint
    return None


def get_dry_run_suggestion(cmd: str):
    """Return a safe dry-run variant of the command when one is known, else None.
    Never suggests a double --dry-run."""
    if not cmd or '--dry-run' in cmd:
        return None
    for pattern, transform in DRY_RUN_SUGGESTIONS:
        if re.search(pattern, cmd):
            return transform(cmd)
    return None
