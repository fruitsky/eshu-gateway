"""F6: one mapping from a request's status/reason to "how it was approved".

The dashboard records *why* a command was allowed in two columns: `status`
(what happened) and `reason` (who or what allowed it). They grew one feature
at a time, so the same fact can arrive as a status (`window-approved`), a
reason (`session-plan:<plan>:<item>`, `read-lane`) or a bare flag
(`override`). Everything that shows "how approved" reads this module, so the
session card, the statistics series and the MCP `session_summary` cannot
disagree.

The read-lane reasons are matched **by string**, not by importing F4: F4 is
not merged on every branch this runs on, and a bucket must never depend on
another chunk's module being importable.

Refusals map to `None` — they are not "approvals". A read-lane *shadow* row
is pending, and if a human approves it the bucket is `human`: the lane did not
clear it, the operator did.
"""

# (key, label) in display order: what most often clears work first, and the
# overrides last, because the override share is the number F3/F4 aim to lower.
BUCKETS = (
    ('allowlist', 'allowlist'),
    ('human', 'human'),
    ('plan', 'plan'),
    ('read-lane', 'read lane'),
    ('session-override', 'session override'),
    ('host-override', 'host override'),
    ('window', 'window'),
    ('fleet', 'fleet run'),
)

BUCKET_KEYS = tuple(key for key, _label in BUCKETS)
BUCKET_LABELS = dict(BUCKETS)

# The two buckets whose share is the headline "can we stop overriding?" number.
OVERRIDE_BUCKETS = ('session-override', 'host-override')

APPROVED_STATUSES = frozenset({
    'auto-approved', 'approved', 'consumed', 'abandoned', 'window-approved',
    'fleet-run',
})

# Values the code writes that are deliberately not an SSH approval bucket:
# integration calls are counted as MCP calls, and the rest are refusals.
NOT_AN_APPROVAL = frozenset({
    '', 'pending', 'denied', 'blocked', 'expired', 'frozen',
    'session-required', 'window-rejected', 'window-requested',
    'integration-approved', 'integration-denied',
})


def bucket_for(status, reason=''):
    """The approval bucket for one request row, or None if it is not one.

    Status wins where the status alone is the answer (`auto-approved`,
    `window-approved`, `fleet-run`): the gateway writes a free-text `reason`
    on allowlist rows, and a reason string must never move a row between
    buckets.
    """
    status = (status or '').strip()
    reason = (reason or '').strip()
    if status == 'auto-approved':
        return 'allowlist'
    if status == 'window-approved':
        return 'window'
    if status == 'fleet-run':
        return 'fleet'
    if status in ('approved', 'consumed', 'abandoned'):
        if reason.startswith('session-plan:'):
            return 'plan'
        if reason.startswith('session-override:'):
            return 'session-override'
        if reason == 'override':
            return 'host-override'
        if reason == 'read-lane':
            return 'read-lane'
        # `read-lane-shadow` is tagged but still waits for a human; if it is
        # approved here, the human cleared it.
        return 'human'
    return None


def empty_buckets():
    """All buckets at zero — the shape the UI and the summary always get."""
    return {key: 0 for key in BUCKET_KEYS}
