from __future__ import annotations

import json
from pathlib import Path

from repo_ready.formats import render_markdown, render_sarif
from repo_ready.scoring import report


def _bad_report() -> dict[str, object]:
    return report(Path(__file__).parent / "fixtures" / "python-bad" / "repo")


def test_markdown_renderer_has_stable_summary_and_table() -> None:
    rendered = render_markdown(_bad_report())

    assert rendered.startswith("# Repository readiness:")
    assert "| ID | Check | Status | Score | Message |" in rendered
    assert "RR01" in rendered


def test_sarif_renderer_contains_failed_check_locations() -> None:
    value = json.loads(render_sarif(_bad_report()))

    assert value["version"] == "2.1.0"
    results = value["runs"][0]["results"]
    assert results
    assert any(result["ruleId"] == "RR11" for result in results)
    assert all("message" in result for result in results)
