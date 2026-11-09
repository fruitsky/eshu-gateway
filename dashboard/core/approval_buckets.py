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

# Every reason prefix the code writes on a request row, with the bucket it
# maps to — or None when the reason is never an approval (`encoded-payload`
# and the window sweep's explanation). A new prefix must be added here; the
# generated test scans the source and fails on anything missing, so an
# unknown reason can no longer fall through to "human" unnoticed.
#
# A key matches a reason that equals it or continues with ':' (so
# `session-plan:12:3` matches the `session-plan` key). `read-lane` and
# `read-lane-shadow` are separate keys: the first is the lane's own
# approval, the second is a tag on a row a human still cleared.
KNOWN_REASON_PREFIXES = {
    'session-plan': 'plan',
    'session-override': 'session-override',
    'override': 'host-override',
    'read-lane': 'read-lane',
    'read-lane-shadow': 'human',
    'encoded-payload': None,
    'exhausted or disabled': None,
}

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


def known_reason(reason):
    """The `KNOWN_REASON_PREFIXES` key a reason belongs to, or None.

    The longest matching key wins, so a future `read-lane:foo` cannot be
    captured by a shorter key.
    """
    reason = (reason or '').strip()
    match = None
    for prefix in KNOWN_REASON_PREFIXES:
        if reason == prefix or reason.startswith(prefix + ':'):
            if match is None or len(prefix) > len(match):
                match = prefix
    return match


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
        prefix = known_reason(reason)
        if prefix is not None:
            # A known prefix maps to its bucket, or to None when the reason
            # is not an approval at all. A reason no one has classified is
            # still a human approval — the status says the row was allowed —
            # but the generated test makes adding one without the table a
            # test failure.
            return KNOWN_REASON_PREFIXES[prefix]
        return 'human'
    return None


def empty_buckets():
    """All buckets at zero — the shape the UI and the summary always get."""
    return {key: 0 for key in BUCKET_KEYS}
