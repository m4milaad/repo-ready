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

from .scoring import report


BASELINE_SCHEMA_VERSION = 1
_REQUIRED_REPORT_KEYS = {"tool", "version", "path", "ecosystems", "score", "max", "grade", "checks"}


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
    missing = sorted(_REQUIRED_REPORT_KEYS - set(value))
    if missing:
        raise BaselineError(f"{label} is missing report keys: {', '.join(missing)}")
    if value.get("tool") != "repo-ready":
        raise BaselineError(f"{label} was not produced by repo-ready")
    if not isinstance(value.get("score"), int) or not isinstance(value.get("max"), int):
        raise BaselineError(f"{label} has invalid score metadata")
    checks = value.get("checks")
    if not isinstance(checks, list) or any(not isinstance(item, dict) or "id" not in item for item in checks):
        raise BaselineError(f"{label} has an invalid checks collection")
    return dict(value)


def _check_map(report_value: Mapping[str, object]) -> dict[str, dict[str, object]]:
    checks = report_value["checks"]
    assert isinstance(checks, list)
    return {str(item["id"]): item for item in checks if isinstance(item, dict)}


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
    if field == "findings" and isinstance(before, list) and isinstance(after, list):
        if len(after) > len(before):
            return "regression"
        if len(after) < len(before):
            return "improvement"
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
            direction = "improvement" if new is not None else "regression"
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
        for field in ("status", "message", "findings"):
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
    except OSError as error:
        raise BaselineError(f"could not read baseline {candidate}: {error}") from error
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise BaselineError(f"baseline {candidate} is not valid JSON") from error
    if not isinstance(value, dict):
        raise BaselineError(f"baseline {candidate} must contain a JSON object")
    return _as_report(value, f"baseline {candidate}")


def compare_repository(root: str | Path, baseline_path: str | Path) -> ReportComparison:
    return compare_reports(load_report(baseline_path), report(root))


__all__ = [
    "BASELINE_SCHEMA_VERSION",
    "BaselineError",
    "ReportChange",
    "ReportComparison",
    "compare_reports",
    "compare_repository",
    "load_report",
]
