"""F27/X11 UI: the Allowances page is gone; Agents carries the two tabs."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HTML = (REPO / "dashboard" / "static" / "index.html").read_text(encoding="utf-8")
JS = (REPO / "dashboard" / "static" / "app.js").read_text(encoding="utf-8")
X11 = (REPO / "dashboard" / "static" / "x11.js").read_text(encoding="utf-8")
STATE = (REPO / "dashboard" / "static" / "state.js").read_text(encoding="utf-8")
HARNESS = (REPO / "tests" / "ui" / "ui_views.cjs").read_text(encoding="utf-8")


def test_the_old_allowances_page_and_nav_item_are_removed():
    assert 'id="nav-allowances"' not in HTML
    assert 'id="view-allowances"' not in HTML
    assert "'allowances'" not in STATE
    assert "// ── F27: Allowances" not in JS


def test_agents_is_the_one_place_and_home_wide_lives_in_home_assistant():
    assert 'id="x11-root"' in HTML
    for word in ("Agents", "Home-wide", "Protected devices",
                 "Limits for all agents", "Never allowed",
                 "Applies to every agent"):
        assert word in X11
    assert "Rules for all agents" not in X11
    assert "/api/agents/" in X11
    assert "/api/allowances/protected" in X11
    assert "/api/allowances/ceiling" in X11


def test_saves_are_check_preview_apply_with_the_hash_and_acks():
    assert "preview_hash" in X11
    assert "risk_ack" in X11 and "resume_ack" in X11
    assert "preview_changed" in X11 and "config_changed" in X11
    assert "changed since you opened it" in X11
    assert "changed since the preview" in X11


def test_the_harness_measures_agents_not_allowances():
    assert "'allowances'" not in HARNESS
    assert "agents: '#view-agents'" in HARNESS
