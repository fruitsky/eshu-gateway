"""X5 — the six fixes from the 1.2 test round.

These are UI fixes, so most of the checks are structural: the markup moved,
the stylesheet carries the rule, and a computed contrast floor for the chip
that was unreadable. The behaviour (which sections save, what the queue shows)
is covered by the F2/F3/X2 suites.
"""
import os
import re

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
HTML = os.path.join(ROOT, "dashboard", "static", "index.html")
CSS = os.path.join(ROOT, "dashboard", "static", "style.css")
JS = os.path.join(ROOT, "dashboard", "static", "app.js")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _contrast(hex_a, hex_b):
    def lum(h):
        h = h.lstrip("#")
        channel = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
               for c in channel]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

    a, b = sorted((lum(hex_a), lum(hex_b)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def _token(css, name):
    m = re.search(r"--%s:\s*(#[0-9a-fA-F]{6})" % re.escape(name), css)
    assert m, "--%s not found" % name
    return m.group(1)


def test_x5_zero_count_chip_text_clears_4_5_to_1():
    css = _read(CSS)
    muted = _token(css, "text-muted")
    for surface in ("bg-surface", "bg-base", "bg-hover"):
        ratio = _contrast(muted, _token(css, surface))
        assert ratio >= 4.5, (surface, ratio)
    # The chip is ink on the surface now; the bucket colour is a dot.
    assert ".appr-chip.zero { color: var(--text-muted)" in css
    assert '.appr-chip .appr-dot' in css
    js = _read(JS)
    assert 'class="appr-dot appr-' in js
    # The old shape took the whole chip's fill from the bucket colour.
    assert "'<span class=\"appr-chip appr-' + key" not in js
    assert "opacity: .45" not in css.split(".appr-chip")[1][:200]


def test_x5_session_modal_scrolls_in_the_card_not_out_of_it():
    html = _read(HTML)
    card = html[html.index('<div id="session-modal"'):]
    card = card[:card.index('<div class="sel-hint"')]
    # One scrolling body holds the conclusion and the commands; the footer
    # (the id) is outside it and stays pinned.
    body = card.index('class="session-modal-body"')
    conclusion = card.index('id="sm-conclusion"')
    commands = card.index('id="sm-commands"')
    footer = card.index('class="session-modal-footer"')
    assert body < conclusion < commands < footer
    css = _read(CSS)
    block = css[css.index(".session-modal-body {"):]
    block = block[:block.index("}")]
    assert "overflow-y: auto" in block
    assert "min-height: 0" in block
    card_css = css[css.index(".session-modal-card {"):]
    card_css = card_css[:card_css.index("}")]
    assert "overflow: hidden" in card_css
    # Opening a session starts at the top of the new body.
    assert "if (bodyEl) bodyEl.scrollTop = 0;" in _read(JS)


def test_x5_plans_render_in_the_jit_list_not_above_the_starfield():
    html = _read(HTML)
    jit = html.index('id="jit-pending-widget"')
    tickets = html.index('id="jit-tickets"')
    plans = html.index('id="plans-widget"')
    assert jit < tickets < plans, "plans must sit inside the JIT widget"
    # And they are no longer a top-level widget before the pending list: the
    # next pending block after the plans is outside the feed.
    feed = html[html.index('id="cc-feed-scroll"'):html.index("<!-- Right: Context Panel -->")]
    assert feed.index('id="jit-pending-widget"') < feed.index('id="plans-widget"')
    css = _read(CSS)
    assert "#view-home .plans-in-jit" in css
    assert "border-top: 1px solid var(--rule-weak)" in css


def test_x5_runbook_steps_are_cards_with_a_readable_host_and_outcome():
    css = _read(CSS)
    step = css[css.index(".rb-step {"):]
    step = step[:step.index("}")]
    assert "border: 1px solid var(--rule-weak)" in step
    assert "margin-bottom: 10px" in step
    assert "--text-faint" not in step
    host = css[css.index(".rb-step-host {"):]
    host = host[:host.index("}")]
    assert "color: var(--text-main)" in host
    assert "var(--text-faint)" not in host
    note = css[css.index(".rb-step-note {"):]
    note = note[:note.index("}")]
    # UI pass 2: a solid rule; the dashed one read as an empty input.
    assert "border-top: 1px solid var(--rule-weak)" in note
    assert "color: var(--text-dim)" in note


def test_x5_freeze_dialog_says_fleet_wide_and_points_at_the_alternative():
    js = _read(JS)
    call = js[js.index("async function triggerFreeze()"):]
    call = call[:call.index("var btn")]
    assert "Freeze is fleet-wide" in call
    assert "There is no per-gateway freeze" in call
    assert "Zero-Trust" in call and "Remove it" in call
    html = _read(HTML)
    assert "Freeze is fleet-wide (sidebar)" in html


def test_x5_gateways_table_keeps_actions_reachable():
    js = _read(JS)
    # Seven columns: enrollment and policy folded into the gateway cell, and
    # the fallback row agrees.
    assert 'colspan="7" class="px-4 py-3 text-muted">No gateways registered.' in js
    html = _read(HTML)
    table = html[html.index('id="gateways-table-body"') - 2000:]
    table = table[:table.index("</table>")]
    assert table.count("<th>") + table.count("<th ") == 7
    # The read-lane and capture selects are a disclosure now, not two more
    # controls in the row.
    assert 'class="gw-modes-extra"' in js
    assert js.index('class="gw-modes-extra"') > 0
    assert 'gw-modes-sum' in js
    css = _read(CSS)
    assert "#view-gateways .table-wrap" in css
    sticky = css[css.index("#view-gateways table th:last-child,"):]
    sticky = sticky[:sticky.index("}")]
    assert "position: sticky" in sticky
    assert "right: 0" in sticky
    # The fade that hid the last column is gone for this table.
    mask = css[css.index("#view-gateways .table-wrap {"):]
    mask = mask[:mask.index("}")]
    assert "mask-image: none" in mask
