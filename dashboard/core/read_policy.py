"""§2.6a — one notion of "read" for integration calls (F9, F14).

Everything that proposes to auto-run an integration call without an approval
asks **this module**, never the underlying checks: `is_read_integration_call`
answers "is this call a read?" from the tool's own declaration plus Chunk H's
read-only passthrough rules (`_read_only_refusal`).

**Scope of this file on this branch.** F4's branch holds the full module —
the same integration wrapper plus `is_read_ssh` and the reviewed SSH verb
table. F4 is not merged into `feature-roadmap` yet, so this file is
deliberately only the integration half; it will be **merged with F4's version**
(the SSH half is additive and does not change this function). One rule matters
at merge time: the `read_only` guard below must stay. F4's wrapper alone treats
a curated write tool as a read, because `_read_only_refusal` only inspects
generic passthrough tools.

F9 uses it to tell a read from a write while Emergency Freeze is on: reads keep
working, writes are refused. F14 uses it before every inventory source read.
"""
from core.tool_runner import _read_only_refusal


def is_read_integration_call(tool, transport, args):
    """(ok, reason). True when this call is a read.

    * the tool must be declared read-only (a curated write tool is a write even
      if it takes no arguments);
    * a generic read-only passthrough tool must not be asked to send a write
      (POST/PUT/…, or a WS command outside the reviewed read set).
    """
    if not (tool or {}).get('read_only'):
        return False, 'write_tool'
    refusal = _read_only_refusal(tool, transport, args)
    if refusal:
        return False, refusal[0]
    return True, ''
