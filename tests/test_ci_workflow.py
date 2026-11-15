"""The CI workflow installs both optional toolchains.

Without them two groups skip in CI and a red suite would never notice: F11's
BusyBox script tests (no `busybox`) and X5's browser measurement (no
Playwright). A developer machine may still skip both, with a reason; CI may
not.
"""
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def test_ci_installs_busybox_for_the_f11_scripts():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "busybox-static" in text
    # In the same job as the suite, not a separate never-run job.
    tests_job = text.split("browser:", 1)[0]
    assert "busybox-static" in tests_job


def test_ci_installs_chromium_for_the_x5_browser_test():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "playwright@1.65.0" in text
    assert "install --with-deps chromium" in text
    assert "ESHU_PLAYWRIGHT_MODULES" in text
    # The browser job runs the measuring test, not just the structural half.
    assert "pytest tests/test_x5_plans_visible.py" in text


def test_the_browser_download_is_cached():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "actions/cache@v4" in text
    assert "path: ~/.cache/ms-playwright" in text
