"""F27 UI: the Allowances page and its harness view."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HTML = (REPO / "dashboard" / "static" / "index.html").read_text(encoding="utf-8")
JS = (REPO / "dashboard" / "static" / "app.js").read_text(encoding="utf-8")
HARNESS = (REPO / "tests" / "ui" / "ui_views.cjs").read_text(encoding="utf-8")


def test_the_page_is_a_nav_item_and_a_view():
    assert 'id="nav-allowances"' in HTML
    assert 'id="view-allowances"' in HTML
    assert "'allowances'" in (REPO / "dashboard" / "static" / "state.js").read_text(encoding="utf-8")


def test_the_ceiling_copy_and_controls_are_there():
    assert "The ceiling is the outer limit" in HTML
    assert "What agents may ever be allowed to do" in HTML
    assert "Destructive words" in HTML and "Never allowable" in JS


def test_the_page_calls_the_engine_and_confirms_risk():
    for path in ("/api/allowances/ceiling", "/api/allowances", "/api/allowances/profiles"):
        assert path in JS
    assert "risk_ack_required" in JS
    assert "Resume" in JS and "Revoke" in JS


def test_the_harness_measures_the_page_at_both_widths():
    assert "'allowances'" in HARNESS
    assert "allowances: '#view-allowances'" in HARNESS
