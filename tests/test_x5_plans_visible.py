"""X5 regression — a proposed plan card must be visible and actionable.

X5 put the plans widget inside the (borderless) pending widget, after the whole
empty state. With no JITs the empty state is taller than a laptop viewport, so
the plan card opened below the fold: the operator saw no plan, and neither
Approve nor Deny could be reached, which blocks every probe and plan.

The card now lives in the pending flow: under the queue when something is
waiting, under the headline when idle, and always above recent sessions.

Most of this file is structural, so it runs everywhere. The last test renders
the real home view in headless Chromium and measures the card; it skips with a
clear reason when node/playwright is not installed (CI can provide it with
ESHU_PLAYWRIGHT_MODULES=/path/to/node_modules).
"""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
HTML = os.path.join(ROOT, "dashboard", "static", "index.html")
CSS = os.path.join(ROOT, "dashboard", "static", "style.css")
JS = os.path.join(ROOT, "dashboard", "static", "app.js")
HARNESS = os.path.join(ROOT, "tests", "ui", "home_plans_layout.cjs")


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_the_plan_slots_sit_in_the_pending_flow():
    js = _read(JS)
    empty = js[js.index("function renderEmptyState(total)"):]
    empty = empty[:empty.index("\n}\n", empty.index("return '<div class=\"cc-empty"))]
    queue = empty.index('id="cc-queue"')
    after_queue = empty.index('id="cc-plans-slot-queue"')
    headline = empty.index('class="cc-empty-headline"')
    sub = empty.index('class="cc-empty-sub"')
    after_headline = empty.index('id="cc-plans-slot-idle"')
    sessions = empty.index("sessionsHtml +", after_headline)
    assert queue < after_queue < headline < sub < after_headline < sessions
    # The slots are inside the empty state (and so inside the pending widget),
    # never above the starfield.
    assert 'class="starfield-wrap"' in empty[:queue]


def test_the_plan_widget_is_moved_into_the_slot_for_the_current_state():
    js = _read(JS)
    body = js[js.index("function renderJitTickets()"):]
    body = body[:body.index("\n}\n")]
    # Keep the widget across the innerHTML re-render: it is detached by it.
    assert "var plansEl = document.getElementById('plans-widget');" in body
    assert "container.innerHTML = renderEmptyState(total);" in body
    assert "var slot = document.getElementById(total > 0 ? 'cc-plans-slot-queue' : 'cc-plans-slot-idle');" in body
    assert "if (plansEl && slot) slot.appendChild(plansEl);" in body
    # And redraw the list now that it has a new parent.
    assert "renderPlans();" in body
    plans = js[js.index("function renderPlans()"):]
    plans = plans[:plans.index("\n}\n")]
    assert "if (slot) slot.classList.add('hidden');" in plans
    assert "if (slot) slot.classList.remove('hidden');" in plans


def test_the_plan_slot_and_header_have_their_rules():
    css = _read(CSS)
    assert "#view-home .cc-plans-slot { align-self: stretch; width: 100%; }" in css
    assert "#view-home .cc-plans-slot:empty { display: none; }" in css
    # `.widget.flat .widget-header { display:none }` is for the pending widget's
    # own header; the plan section's header must survive it.
    assert "#view-home .plans-in-jit > .widget-header { display: flex; }" in css
    # X5's intent still holds: the card is not placed above the starfield.
    assert ".plans-in-jit" in css
    assert "z-index: 2" in css[css.index("#view-home .plans-in-jit {"):][:200]


def _harness_env():
    """Return (env, skip_reason). The harness exits 2 when playwright is absent."""
    node = shutil.which("node")
    if not node:
        return None, "node is not installed, so the headless render cannot run"
    env = dict(os.environ)
    for var in ("ESHU_PLAYWRIGHT_MODULES", "PLAYWRIGHT_NODE_MODULES"):
        path = env.get(var)
        if path:
            env["NODE_PATH"] = path + (os.pathsep + env["NODE_PATH"] if env.get("NODE_PATH") else "")
            break
    return env, None


def test_a_plan_card_is_visible_and_clickable_in_a_real_browser():
    env, reason = _harness_env()
    if reason:
        pytest.skip(reason)
    proc = subprocess.run(
        [shutil.which("node"), HARNESS],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=180,
    )
    if proc.returncode == 2:
        pytest.skip("headless Chromium not available: " + proc.stderr.strip()[:200])
    assert proc.returncode == 0, proc.stderr[-2000:]
    data = json.loads(proc.stdout)

    expected_slot = {"idle": "cc-plans-slot-idle", "queue": "cc-plans-slot-queue"}
    for name in ("desktop", "phone"):
        for scenario in ("idle", "queue"):
            got = data["%s-%s" % (name, scenario)]
            label = "%s/%s" % (name, scenario)
            assert not got["planHidden"], label
            assert got["widgetDisplay"] != "none", label
            assert got["slot"] == expected_slot[scenario], label

            # Fully inside the viewport, Approve included.
            assert got["widget"]["y"] >= 0, label
            assert got["widget"]["bottom"] <= got["innerHeight"], (label, got["widget"])
            assert got["button"]["y"] >= 0, label
            assert got["button"]["bottom"] <= got["innerHeight"], (label, got["button"])

            # Not covered: the point at Approve's centre really is Approve, and
            # the starfield's box does not sit over the button.
            assert got["hitIsButton"], (label, got["hitDesc"])
            assert not got["buttonOverlapsStarfield"], (label, got["button"], got["starfield"])

            # Ordering: queue, then the plan card, then recent sessions.
            assert got["domOrder"].index("cc-queue") < got["domOrder"].index("plans-widget"), label
            assert got["domOrder"].index("plans-widget") < got["domOrder"].index("cc-sessions-host"), label

            if scenario == "queue":
                # Directly under the pending queue.
                assert got["widget"]["y"] >= got["queueBottom"] - 1, (label, got["widget"], got["queueBottom"])
                assert got["widget"]["y"] - got["queueBottom"] <= 80, (label, got["widget"], got["queueBottom"])
            else:
                # Under the empty-state headline.
                assert got["widget"]["y"] >= got["headlineBottom"] - 1, (label, got["widget"], got["headlineBottom"])
