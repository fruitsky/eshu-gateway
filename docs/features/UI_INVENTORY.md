# UI inventory — input for the UI design pass

This lists what the feature plan added to the dashboard UI and where it lives
today, so a later UI design pass can decide what to merge, move or drop. It
also records the known rough edges reviewers noted along the way. It is not a
design; it describes the current state. Screenshots are under
`docs/screenshots/<chunk>/`.

## Surfaces

| Surface | What is there now | Added by |
|---|---|---|
| **Queue card** (JIT ticket) | Raw command; breakdown headline "N steps · R read · W write"; risky step highlighted; two-press approve for long commands with an unknown step; inner-target chip ("pct 103"); "would auto-run" chip on read-lane shadow rows | F1/F2, F7, F4 |
| **Plans card** (above the queue) | One card per proposed plan: agent, session goal, each item with host, ×uses and breakdown; untick to remove; TTL select; strict-sessions warning; broad items (`any host`, `×N`) highlighted; "replaced, review again" banner; two-press for long/unknown items | F3 |
| **Sidebar** (context panel) | Command and breakdown; "what it does"; host/IP; risk and decoded preview; status/time; Approve/Deny per request type with its own two-press label; five collapsible F18 sections: likely waiting reason, this session, seen before, like this, host | F2, X1, F18 |
| **Session modal** | Session commands with exit badge and duration; the plan with uses/max and End; (F6 summary card planned here) | F3, F5, (F6) |
| **History table** | Exit badge and duration; "show output" modal; inner-target filter chip | F5, F7 |
| **Output modal** | Escaped monospace tail, truncated note | F5 |
| **Gateways table** | Zero-Trust toggle; capture select (off / exit-only / tail); read-lane select (off / shadow / on, locked) | F5, F4 |
| **Override dialog** | Warning that a Proxmox-host override also covers its containers | F7 |
| **Statistics** | Busiest inner targets; read-lane shadow tile; (F6 "how approved" buckets planned) | F7, F4, (F6) |

## Questions for the design pass

1. **Too many places show the same command.** The queue card, the sidebar
   and the session modal each render the breakdown. Should the sidebar become
   the single place for detail, with the card only a summary?
2. **The sidebar is getting long.** Its contents are the F2 fields, then
   actions, then five F18 sections. Should the actions be pinned at the top
   or bottom, and the F18 sections collapsed by default?
3. **Plans vs queue.** Plans are a separate card above single requests. Should
   they live in the queue as a different card type, sorted in?
4. **Per-gateway settings are spreading across table columns.** These are
   Zero-Trust, capture and read lane, and later more. Should they move to a
   per-gateway settings drawer?
5. **Two-press confirmations exist in three places** (card, sidebar, plan)
   with slightly different wording. Make them one component.
6. **Statistics** collects tiles chunk by chunk. Group them into "automation"
   and "activity".

## Known rough edges (from reviews)

* The X1 sidebar's `contextApproveLabel` reads the SSH two-press state for
  integration calls too (cosmetic).
* F18 "like this" counts one-step shapes such as `cat:read`. It may be better
  to show these only for shapes of two or more steps.
* The F2 two-press on the queue card highlights a step that can sit behind
  the open sidebar.
