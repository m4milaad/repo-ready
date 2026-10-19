from __future__ import annotations

import json
from pathlib import Path

import pytest

from repo_ready.baseline import BaselineError, compare_reports, compare_repository, load_report
from repo_ready.overlay import OverlayContext, snapshot_context
from repo_ready.context import RepoContext
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


def _finding(detail: str, line: int | None = 1) -> dict[str, object]:
    return {"file": "README.md", "line": line, "detail": detail}


def _report_with_findings(findings: list[dict[str, object]]) -> dict[str, object]:
    value = report(_fixture("perfect-python"))
    value["checks"][0]["findings"] = findings
    return value


def test_equal_count_new_findings_are_regressions_and_resolutions_are_visible() -> None:
    old = _finding("previous problem")
    new = _finding("new problem")

    comparison = compare_reports(_report_with_findings([old]), _report_with_findings([new]))

    changes = [change for change in comparison.changes if change.kind == "check-findings"]
    assert comparison.score_delta == 0
    assert comparison.regressed is True
    assert len(changes) == 2
    assert any(change.direction == "regression" and change.after == [new] for change in changes)
    assert any(change.direction == "improvement" and change.before == [old] for change in changes)


def test_new_findings_regress_even_when_more_old_findings_are_resolved() -> None:
    comparison = compare_reports(
        _report_with_findings([_finding("old one"), _finding("old two")]),
        _report_with_findings([_finding("new problem")]),
    )

    assert comparison.regressed is True
    assert {change.direction for change in comparison.changes} == {"regression", "improvement"}


def test_findings_are_sets_ignoring_order_and_duplicates() -> None:
    first, second = _finding("first", None), _finding("second")

    comparison = compare_reports(
        _report_with_findings([first, second]), _report_with_findings([second, first, first]),
    )

    assert comparison.changes == ()
    assert comparison.regressed is False


def test_resolved_findings_are_improvements() -> None:
    comparison = compare_reports(_report_with_findings([_finding("resolved")]), _report_with_findings([]))

    assert comparison.regressed is False
    assert [change.direction for change in comparison.changes] == ["improvement"]


@pytest.mark.parametrize(
    ("before", "after", "direction"),
    [
        ("pass", "partial", "regression"), ("pass", "fail", "regression"),
        ("partial", "fail", "regression"), ("na", "partial", "regression"),
        ("na", "fail", "regression"), ("fail", "partial", "improvement"),
        ("partial", "pass", "improvement"), ("fail", "na", "improvement"),
    ],
)
def test_status_changes_are_classified_even_at_the_same_score(before: str, after: str, direction: str) -> None:
    baseline = _report_with_findings([])
    current = _report_with_findings([])
    baseline["checks"][0]["status"] = before
    current["checks"][0]["status"] = after

    comparison = compare_reports(baseline, current)

    assert comparison.score_delta == 0
    assert comparison.regressed is (direction == "regression")
    assert [(change.kind, change.direction) for change in comparison.changes] == [("check-status", direction)]


@pytest.mark.parametrize("status", ["fail", "partial"])
def test_new_failing_check_is_a_regression(status: str) -> None:
    baseline = _report_with_findings([])
    current = _report_with_findings([])
    new_check = dict(current["checks"][0], id="RR99", status=status)
    current["checks"].append(new_check)

    assert compare_reports(baseline, current).regressed is True


@pytest.mark.parametrize(
    ("field", "invalid"),
    [("score", True), ("max", False), ("score", -1), ("score", 101), ("max", 0), ("ecosystems", "python"), ("ecosystems", [True]), ("version", []), ("path", None), ("grade", 1)],
)
def test_invalid_report_metadata_is_rejected(field: str, invalid: object) -> None:
    baseline = _report_with_findings([])
    baseline[field] = invalid

    with pytest.raises(BaselineError):
        compare_reports(baseline, _report_with_findings([]))


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("id", []), ("id", True), ("id", ""), ("earned", True), ("earned", "14"),
        ("earned", -1), ("earned", 1000), ("weight", False), ("status", []),
        ("status", "unknown"), ("message", {}), ("findings", None), ("findings", [None]),
        ("findings", [{}]), ("findings", [_finding("bad line", True)]),
        ("findings", [_finding("bad line", 0)]), ("findings", [dict(_finding("bad file"), file=[])]),
        ("findings", [dict(_finding("bad detail"), detail={})]), ("fixable", 1), ("fix_hint", []),
    ],
)
def test_invalid_nested_check_data_is_rejected(field: str, invalid: object) -> None:
    baseline = _report_with_findings([])
    baseline["checks"][0][field] = invalid

    with pytest.raises(BaselineError):
        compare_reports(baseline, _report_with_findings([]))


def test_duplicate_check_ids_are_rejected() -> None:
    baseline = _report_with_findings([])
    baseline["checks"].append(dict(baseline["checks"][0]))

    with pytest.raises(BaselineError, match="duplicate"):
        compare_reports(baseline, _report_with_findings([]))


def test_non_object_report_is_rejected_without_crashing() -> None:
    with pytest.raises(BaselineError):
        compare_reports([], _report_with_findings([]))


def test_invalid_current_report_is_validated_too() -> None:
    current = _report_with_findings([])
    current["checks"][0]["findings"] = [dict(_finding("invalid"), line="1")]

    with pytest.raises(BaselineError, match="current"):
        compare_reports(_report_with_findings([]), current)


def test_load_report_rejects_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_bytes(b"\xff\xfe")

    with pytest.raises(BaselineError):
        load_report(path)


def test_repository_comparison_accepts_a_read_only_virtual_context(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "README.md").write_text("# Example\n", encoding="utf-8")
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(report(repository)), encoding="utf-8")

    frozen = snapshot_context(RepoContext(repository))
    virtual = OverlayContext(frozen, {"README.md": "# Example\n\n## Setup\nDocumented.\n"})

    comparison = compare_repository(repository, baseline_path, context=virtual)

    expected = compare_reports(report(repository), report(repository, context=virtual))
    assert comparison == expected
    assert (repository / "README.md").read_text(encoding="utf-8") == "# Example\n"
