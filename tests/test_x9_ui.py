"""X9 UI: the mixed-generation and http:// warnings, and the 1.3 switch."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
JS = (REPO / "dashboard" / "static" / "app.js").read_text(encoding="utf-8")
HTML = (REPO / "dashboard" / "static" / "index.html").read_text(encoding="utf-8")
CSS = (REPO / "dashboard" / "static" / "style.css").read_text(encoding="utf-8")


def test_the_badge_warns_about_a_mixed_generation():
    assert "files_mixed" in JS
    assert "mixed files" in JS
    assert "gw-ch-warn" in JS and ".gw-ch-warn" in CSS
    assert "not running one generation" in JS


def test_an_http_dashboard_is_flagged():
    assert "dashboard_scheme" in JS
    assert "Dashboard URL is http://" in JS


def test_the_legacy_switch_is_offered_from_the_dev_panel():
    assert "legacy-1-3-row" in HTML
    assert "retireLegacyRoutes" in JS
    assert "/api/dev/legacy-retire" in JS
