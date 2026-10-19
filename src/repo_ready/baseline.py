"""Baseline comparison for readiness reports.

Comparison deliberately operates on report data rather than filesystem paths.
That makes it safe to compare reports from temporary checkouts and prevents a
checkout location from appearing as a readiness regression.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .context import RepoContext
from .scoring import report


BASELINE_SCHEMA_VERSION = 1
_REQUIRED_REPORT_KEYS = {"tool", "version", "path", "ecosystems", "score", "max", "grade", "checks"}
_REQUIRED_CHECK_KEYS = {"id", "title", "weight", "earned", "status", "message", "fix_hint", "fixable", "findings"}
_STATUS_RANK = {"fail": 0, "partial": 1, "pass": 2, "na": 2}


class BaselineError(ValueError):
    """Raised when a baseline is missing or does not match the report contract."""


@dataclass(frozen=True)
class ReportChange:
    """One semantic difference between two readiness reports."""

    kind: str
    identifier: str
    direction: str
    detail: str
    before: object = None
    after: object = None

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "id": self.identifier,
            "direction": self.direction,
            "detail": self.detail,
            "before": self.before,
            "after": self.after,
        }


@dataclass(frozen=True)
class ReportComparison:
    """Versioned, machine-readable report comparison."""

    version: int
    baseline_score: int
    current_score: int
    max_score: int
    score_delta: int
    changes: tuple[ReportChange, ...]

    @property
    def regressed(self) -> bool:
        return self.score_delta < 0 or any(change.direction == "regression" for change in self.changes)

    def as_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "baseline": {"score": self.baseline_score, "max": self.max_score},
            "current": {"score": self.current_score, "max": self.max_score},
            "score_delta": self.score_delta,
            "regressed": self.regressed,
            "changes": [change.as_dict() for change in self.changes],
        }


def _as_report(value: Mapping[str, object], label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise BaselineError(f"{label} must contain a report object")
    missing = sorted(_REQUIRED_REPORT_KEYS - set(value))
    if missing:
        raise BaselineError(f"{label} is missing report keys: {', '.join(missing)}")
    if value.get("tool") != "repo-ready":
        raise BaselineError(f"{label} was not produced by repo-ready")
    if (
        type(value["score"]) is not int or type(value["max"]) is not int
        or not 0 <= value["score"] <= value["max"] or value["max"] <= 0
    ):
        raise BaselineError(f"{label} has invalid score metadata")
    if (
        any(not isinstance(value[key], str) for key in ("version", "path", "grade"))
        or not isinstance(value["ecosystems"], list)
        or any(not isinstance(item, str) for item in value["ecosystems"])
    ):
        raise BaselineError(f"{label} has invalid report metadata")
    checks = value.get("checks")
    if not isinstance(checks, list):
        raise BaselineError(f"{label} has an invalid checks collection")
    identifiers: set[str] = set()
    for check in checks:
        if not isinstance(check, dict) or not _REQUIRED_CHECK_KEYS.issubset(check):
            raise BaselineError(f"{label} has an invalid check record")
        identifier = check["id"]
        if not isinstance(identifier, str) or not identifier.strip():
            raise BaselineError(f"{label} has an invalid check id")
        if identifier in identifiers:
            raise BaselineError(f"{label} has duplicate check id {identifier!r}")
        identifiers.add(identifier)
        if (
            type(check["earned"]) is not int or type(check["weight"]) is not int
            or not 0 <= check["earned"] <= check["weight"]
            or not isinstance(check["status"], str) or check["status"] not in _STATUS_RANK
            or any(not isinstance(check[key], str) for key in ("title", "message"))
            or (check["fix_hint"] is not None and not isinstance(check["fix_hint"], str))
            or not isinstance(check["fixable"], bool)
            or not isinstance(check["findings"], list)
        ):
            raise BaselineError(f"{label} has invalid data for check {identifier}")
        for finding in check["findings"]:
            if (
                not isinstance(finding, dict) or not {"file", "line", "detail"}.issubset(finding)
                or not isinstance(finding["file"], str) or not isinstance(finding["detail"], str)
                or (finding["line"] is not None and (type(finding["line"]) is not int or finding["line"] < 1))
            ):
                raise BaselineError(f"{label} has an invalid finding for check {identifier}")
    return dict(value)


def _check_map(report_value: Mapping[str, object]) -> dict[str, dict[str, object]]:
    checks = report_value["checks"]
    assert isinstance(checks, list)
    return {item["id"]: item for item in checks}


def _finding_map(check: dict[str, object]) -> dict[tuple[str, int | None, str], dict[str, object]]:
    return {
        (item["file"], item["line"], item["detail"]): {key: item[key] for key in ("file", "line", "detail")}
        for item in check["findings"]
    }


def _direction(before: object, after: object) -> str:
    if before == after:
        return "unchanged"
    if isinstance(before, int) and isinstance(after, int):
        return "improvement" if after > before else "regression"
    if before is None:
        return "improvement"
    if after is None:
        return "regression"
    return "changed"


def _field_direction(field: str, before: object, after: object) -> str:
    if field == "status" and _STATUS_RANK[before] != _STATUS_RANK[after]:
        return _direction(_STATUS_RANK[before], _STATUS_RANK[after])
    return _direction(before, after)


def compare_reports(baseline: Mapping[str, object], current: Mapping[str, object]) -> ReportComparison:
    """Compare semantic readiness data while ignoring report paths."""

    before = _as_report(baseline, "baseline")
    after = _as_report(current, "current")
    if before["max"] != after["max"]:
        raise BaselineError("baseline and current reports have different maximum scores")

    changes: list[ReportChange] = []
    score_delta = int(after["score"]) - int(before["score"])
    if score_delta:
        changes.append(
            ReportChange(
                "score",
                "score",
                "improvement" if score_delta > 0 else "regression",
                f"Readiness score changed by {score_delta:+d}.",
                before["score"],
                after["score"],
            )
        )

    before_checks = _check_map(before)
    after_checks = _check_map(after)
    for identifier in sorted(set(before_checks) | set(after_checks)):
        old = before_checks.get(identifier)
        new = after_checks.get(identifier)
        if old is None or new is None:
            direction = "regression" if new is None or new["status"] in {"fail", "partial"} or new["findings"] else "improvement"
            changes.append(
                ReportChange(
                    "check",
                    identifier,
                    direction,
                    f"Check {identifier} was {'added' if new is not None else 'removed'}.",
                    old,
                    new,
                )
            )
            continue
        old_earned = old.get("earned")
        new_earned = new.get("earned")
        if old_earned != new_earned:
            changes.append(
                ReportChange(
                    "check-score",
                    identifier,
                    _direction(old_earned, new_earned),
                    f"{identifier} earned score changed.",
                    old_earned,
                    new_earned,
                )
            )
        for field in ("status", "message"):
            if old.get(field) != new.get(field):
                changes.append(
                    ReportChange(
                        f"check-{field}",
                        identifier,
                        _field_direction(field, old.get(field), new.get(field)),
                        f"{identifier} {field} changed.",
                        old.get(field),
                        new.get(field),
                    )
                )

        old_findings, new_findings = _finding_map(old), _finding_map(new)
        for direction, selected, other in (
            ("regression", new_findings, old_findings),
            ("improvement", old_findings, new_findings),
        ):
            keys = selected.keys() - other.keys()
            if keys:
                findings = [selected[key] for key in sorted(keys, key=lambda key: (key[0], key[1] or 0, key[2]))]
                added = direction == "regression"
                changes.append(
                    ReportChange(
                        "check-findings", identifier, direction,
                        f"{identifier} findings {'added' if added else 'resolved'}.",
                        [] if added else findings,
                        findings if added else [],
                    )
                )

    return ReportComparison(
        version=BASELINE_SCHEMA_VERSION,
        baseline_score=int(before["score"]),
        current_score=int(after["score"]),
        max_score=int(after["max"]),
        score_delta=score_delta,
        changes=tuple(sorted(changes, key=lambda item: (item.kind, item.identifier, item.detail))),
    )


def load_report(path: str | Path) -> dict[str, object]:
    """Read and validate a JSON report without executing repository code."""

    candidate = Path(path)
    try:
        text = candidate.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise BaselineError(f"could not read baseline {candidate}: {error}") from error
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise BaselineError(f"baseline {candidate} is not valid JSON") from error
    if not isinstance(value, dict):
        raise BaselineError(f"baseline {candidate} must contain a JSON object")
    return _as_report(value, f"baseline {candidate}")


def compare_repository(
    root: str | Path,
    baseline_path: str | Path,
    *,
    context: RepoContext | None = None,
) -> ReportComparison:
    """Compare a repository report, optionally through a read-only context.

    The optional context is useful for deterministic what-if views.  The
    baseline file is still read once from the caller-supplied path, while all
    current-repository reads go through ``context`` when supplied.
    """

    return compare_reports(load_report(baseline_path), report(root, context=context))


__all__ = [
    "BASELINE_SCHEMA_VERSION",
    "BaselineError",
    "ReportChange",
    "ReportComparison",
    "compare_reports",
    "compare_repository",
    "load_report",
]
