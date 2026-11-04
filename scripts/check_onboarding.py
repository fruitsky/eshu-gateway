#!/usr/bin/env python3
"""Validate the first-run walkthrough.

The walkthrough is deliberately data-shaped: ONB_STEPS and ONB_INTEGRATIONS in
app.js are the content, the markup is the shell. This checks that the content
is complete and that every renderer still exists, so an edit cannot half-ship
(empty step, missing function, a dead anchor).

Run: python3 scripts/check_onboarding.py
Exit: 0 if the walkthrough is complete, 1 otherwise.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "dashboard/static/app.js"
HTML = ROOT / "dashboard/static/index.html"
DOC = ROOT / "docs/FIRST_RUN.md"

errors = []
notes = []


def fail(msg):
    errors.append(msg)


def main():
    src = APP.read_text(encoding="utf-8")
    html = HTML.read_text(encoding="utf-8")

    # ── the modal shell ──────────────────────────────────────────────
    for anchor in ('id="onboarding-modal"', 'id="onb-rail"', 'id="onb-body"',
                   'id="onb-next"', 'id="onb-back"'):
        if anchor not in html:
            fail(f"index.html is missing {anchor}")

    if "maybeShowOnboarding();" not in src:
        fail("initDashboard() does not call maybeShowOnboarding()")
    if "document.getElementById('onboarding-modal')" not in src:
        fail("the Escape guard for the walkthrough is missing")
    if not DOC.exists():
        fail("docs/FIRST_RUN.md is missing")

    # ── steps ────────────────────────────────────────────────────────
    block = re.search(r"var ONB_STEPS = \[(.*?)\n\];", src, re.S)
    if not block:
        fail("ONB_STEPS not found in app.js")
        return report()

    steps = re.findall(
        r"\{ id: '([^']+)', label: '([^']+)', title: '([^']+)',\s*"
        r"lede: '([^']+)',\s*render: (\w+) \}",
        block.group(1), re.S)
    if len(steps) < 3:
        fail(f"ONB_STEPS has {len(steps)} steps; at least 3 are expected")

    ids = set()
    for sid, label, title, lede, render in steps:
        if not re.fullmatch(r"[a-z][a-z0-9-]*", sid):
            fail(f"step id {sid!r} is not lowercase-kebab")
        if sid in ids:
            fail(f"duplicate step id {sid!r}")
        ids.add(sid)
        if len(title) > 64:
            fail(f"step {sid!r} title is {len(title)} chars (max 64)")
        if len(lede) > 130:
            fail(f"step {sid!r} lede is {len(lede)} chars (max 130)")
        if not re.search(r"function " + re.escape(render) + r"\(", src):
            fail(f"step {sid!r} references missing renderer {render}()")
        notes.append(f"step {sid:13} {label:13} -> {render}()")

    # the renderers the machinery itself relies on
    for fn in ("openOnboarding", "closeOnboarding", "finishOnboarding",
               "skipOnboarding", "onbNext", "onbBack", "onbGo"):
        if not re.search(r"function " + fn + r"\(", src):
            fail(f"missing {fn}()")

    # ── integration cards ───────────────────────────────────────────
    iblock = re.search(r"var ONB_INTEGRATIONS = \[(.*?)\n\];", src, re.S)
    if not iblock:
        fail("ONB_INTEGRATIONS not found in app.js")
    else:
        cards = re.findall(r"\{ name: '([^']+)', kind: '([^']+)', url: '([^']+)',\s*"
                           r"blurb: '([^']+)',\s*tools: \[([^\]]+)\] \}",
                           iblock.group(1), re.S)
        if len(cards) < 2:
            fail(f"ONB_INTEGRATIONS has {len(cards)} cards; at least 2 are expected")
        for name, kind, url, blurb, tools in cards:
            if not tools.strip():
                fail(f"integration {name!r} has no example tools")
            notes.append(f"card {name:16} {kind:10} {len(blurb)} chars")

    return report()


def report():
    for n in notes:
        print("  ", n)
    if errors:
        print()
        for e in errors:
            print("  FAIL", e)
        print(f"\n{len(errors)} problem(s) in the first-run walkthrough")
        return 1
    print("\nfirst-run walkthrough: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
