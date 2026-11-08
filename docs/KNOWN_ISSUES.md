# Known Issues

Tracked defects and parked fixes. Each entry records what's wrong, where it lives,
severity, and status. Work on a parked item happens on its own branch.

## Open

### KI-1 — Statistics filtered by gateway returns HTTP 500

* **Where:** `dashboard/main.py`, `get_statistics`. `ip_filter` is built as
  `AND r.target_ip IN (…)`, but several of its queries read `FROM requests`
  without the `r` alias, so SQLite raises `no such column: r.target_ip`
  whenever `gateway_ip` / `gateway_ips` is passed.
* **Effect:** the Statistics view fails when narrowed to one or more gateways;
  the unfiltered view works.
* **Severity:** low (display only). **Found:** 2026-11-08 by the F7 implementer.
* **Fix:** alias every `FROM requests` in `get_statistics` as `r` (or build the
  filter without the alias), plus a test that calls `/api/statistics` with
  `gateway_ips` for both the basic and `extended` responses.
* **Status:** open. Small enough to bundle into the next dashboard chunk.

## Fixed

_AW-1 — "gateway with zero active windows silently falls through to JIT" — was
fixed: the poller now always keeps the `approved_windows.sh` handler deployed and
window-less hosts hard-reject presented tokens. See CHANGELOG._
