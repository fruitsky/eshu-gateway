# Known Issues

Tracked defects and parked fixes. Each entry records what's wrong, where it lives,
severity, and status. Work on a parked item happens on its own branch.

## Open

## Fixed

_KI-1 — "Statistics filtered by gateway returns HTTP 500" — was fixed: every
`FROM requests` in `get_statistics` is aliased `r`, so the `AND r.target_ip IN (…)`
filter resolves for the basic and extended responses. Regression tests:
`tests/test_statistics_filter.py`._

_AW-1 — "gateway with zero active windows silently falls through to JIT" — was
fixed: the poller now always keeps the `approved_windows.sh` handler deployed and
window-less hosts hard-reject presented tokens. See CHANGELOG._
