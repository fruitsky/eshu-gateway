"""Chunk R5: the architecture docs must warn about argument-level execution."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_sec_R_docs_safe_allowlist_rules_present():
    text = (REPO / "docs" / "ARCHITECTURE.md").read_text()
    assert "Writing safe allowlist rules" in text
    assert "-exec" in text
    assert "xargs" in text
    assert "BEGIN{system" in text
