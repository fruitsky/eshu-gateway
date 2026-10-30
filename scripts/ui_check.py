#!/usr/bin/env python3
"""Design-token gate for the dashboard stylesheet.

The UI cohesion work is easy to undo by accident: a stray hex colour, a 9px
label, another !important. This checks the rules that are mechanically
checkable, so the discipline survives future edits.

Run:  python3 scripts/ui_check.py [--verbose]
Exit: 0 clean, 1 one or more rules broken.

Checks
  FAIL  every var(--x) used in style.css is defined
  FAIL  no font-size below 11px
  FAIL  !important budget (see IMPORTANT_BUDGET)
  WARN  hex colours outside :root / @font-face
  INFO  how much amber the stylesheet spends, and on what

The amber count is deliberately a warning, not a gate: amber is the signal
colour and the count is meant to fall as views move to the ink language.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = ROOT / "dashboard" / "static" / "style.css"
IMPORTANT_BUDGET = 6

# Custom properties set from JavaScript on the element itself (inline
# `style="--i:3"`), not declared in the stylesheet.
ALLOW_INLINE = {"--i"}


def strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def main() -> int:
    if not CSS.exists():
        print(f"ui_check: {CSS} not found")
        return 2
    raw = CSS.read_text(encoding="utf-8")
    css = strip_comments(raw)
    failures: list[str] = []
    warnings: list[str] = []

    # 1. every custom property used must exist
    defined = set(re.findall(r"(--[a-z0-9-]+)\s*:", css))
    used = set(re.findall(r"var\(\s*(--[a-z0-9-]+)", css))
    missing = sorted(used - defined - ALLOW_INLINE)
    if missing:
        counts = {m: len(re.findall(r"var\(\s*" + re.escape(m) + r"\b", css)) for m in missing}
        detail = ", ".join(f"{m} (x{n})" for m, n in counts.items())
        failures.append(f"undefined custom properties: {detail}")

    # 2. type floor
    small = []
    for m in re.finditer(r"font-size:\s*([0-9.]+)px", css):
        if float(m.group(1)) < 11:
            # show the declaration, not a line number: stripping comments above
            # shifts offsets, so line numbers are not reliable here
            start = max(0, m.start() - 40)
            small.append(css[start:m.end()].split("\n")[-1].strip())
    if small:
        failures.append(f"{len(small)} font-size declarations under 11px: " + " | ".join(small[:5]))

    # 3. !important budget — the reduced-motion reset is accessibility, not debt
    motion = re.search(r"@media\s*\(prefers-reduced-motion[^{]*\{[\s\S]*?\n\s*\}", css)
    scoped = css.replace(motion.group(0), "") if motion else css
    n_important = len(re.findall(r"!important", scoped))
    if n_important > IMPORTANT_BUDGET:
        failures.append(f"{n_important} !important declarations (budget {IMPORTANT_BUDGET})")

    # 4. raw hex colours outside :root and @font-face
    root_block = re.search(r":root\s*\{[^}]*\}", css, flags=re.S)
    body = css.replace(root_block.group(0), "") if root_block else css
    body = re.sub(r"@font-face\s*\{[^}]*\}", "", body)
    hexes = re.findall(r"#[0-9a-fA-F]{3,8}\b", body)
    if hexes:
        uniq = sorted(set(h.lower() for h in hexes))
        warnings.append(f"{len(hexes)} literal hex colours outside :root ({len(uniq)} distinct)")

    # 5. amber budget (informational)
    amber = len(re.findall(r"#f59e0b|rgba\(\s*245\s*,\s*158\s*,\s*11", css))
    print(f"ui_check: {n_important} !important · {amber} amber literals · "
          f"{len(used)} custom properties in use")

    for w in warnings:
        print(f"  WARN  {w}")
    for f in failures:
        print(f"  FAIL  {f}")
    if failures:
        return 1
    print("  OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
