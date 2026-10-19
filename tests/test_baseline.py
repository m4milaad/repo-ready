from __future__ import annotations

import json
from pathlib import Path

import pytest

from repo_ready.baseline import BaselineError, compare_reports, load_report
from repo_ready.scoring import report


def _fixture(case: str) -> Path:
    return Path(__file__).parent / "fixtures" / case / "repo"


def test_identical_reports_have_no_changes() -> None:
    value = report(_fixture("perfect-python"))

    comparison = compare_reports(value, dict(value))

    assert comparison.version == 1
    assert comparison.score_delta == 0
    assert comparison.regressed is False
    assert comparison.changes == ()


def test_score_regression_is_detected() -> None:
    baseline = report(_fixture("perfect-python"))
    current = report(_fixture("python-bad"))

    comparison = compare_reports(baseline, current)

    assert comparison.score_delta < 0
    assert comparison.regressed is True
    assert any(change.kind == "score" and change.direction == "regression" for change in comparison.changes)


def test_same_score_with_changed_findings_is_still_detected() -> None:
    baseline = report(_fixture("perfect-python"))
    current = json.loads(json.dumps(baseline))
    check = next(item for item in current["checks"] if item["id"] == "RR01")
    check["findings"] = [{"file": "README.md", "line": 1, "detail": "synthetic diagnostic"}]

    comparison = compare_reports(baseline, current)

    assert comparison.score_delta == 0
    assert comparison.regressed is True
    assert any(change.kind == "check-findings" for change in comparison.changes)


def test_paths_are_not_compared_as_readiness_changes() -> None:
    baseline = report(_fixture("perfect-python"))
    current = dict(baseline)
    current["path"] = "C:/different/checkout"

    comparison = compare_reports(baseline, current)

    assert comparison.changes == ()


def test_invalid_baseline_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(BaselineError):
        load_report(path)


def test_missing_report_key_is_rejected() -> None:
    with pytest.raises(BaselineError, match="missing report keys"):
        compare_reports({"tool": "repo-ready"}, report(_fixture("perfect-python")))
