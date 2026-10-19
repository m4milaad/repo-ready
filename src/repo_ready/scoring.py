"""Scan orchestration and weighted score calculation."""

from __future__ import annotations

from pathlib import Path

from . import __version__
from .checks import CHECK_WEIGHTS, run_checks
from .context import RepoContext
from .detector import detect_ecosystems
from .models import CheckResult, ScanReport


def grade(score: int) -> str:
    if score >= 90:
        return "Ready"
    if score >= 70:
        return "Almost ready"
    if score >= 40:
        return "Needs work"
    return "Not ready"


def scan(root: str | Path, *, context: RepoContext | None = None) -> ScanReport:
    """Run one deterministic scan while retaining the caller's path string."""

    ctx = context or RepoContext(root)
    ecosystems = tuple(sorted(detect_ecosystems(ctx)))
    checks = run_checks(ctx, ecosystems)
    return ScanReport(
        tool="repo-ready",
        version=__version__,
        path=str(root),
        ecosystems=ecosystems,
        score=sum(check.earned for check in checks),
        max_score=sum(CHECK_WEIGHTS.values()),
        grade=grade(sum(check.earned for check in checks)),
        checks=checks,
    )


def report(root: str | Path, *, context: RepoContext | None = None) -> dict[str, object]:
    return scan(root, context=context).as_dict()


def build_report(root: str | Path, *, context: RepoContext | None = None) -> ScanReport:
    return scan(root, context=context)


scan_repository = scan


def evaluate_rules(root: str | Path) -> tuple[CheckResult, ...]:
    return scan(root).checks


def score_checks(checks: dict[str, object]) -> int:
    """Score current CheckResult values and the old boolean mapping API."""

    if any(key.startswith("RR") for key in checks):
        return sum(
            value.earned if isinstance(value, CheckResult) else int(value)
            for value in checks.values()
            if isinstance(value, CheckResult) or isinstance(value, (int, bool))
        )
    legacy = {
        "readme_sections": 15, "license": 10, "contributing": 10, "templates": 10,
        "lockfile": 10, "pinned_runtime": 10, "dockerfile": 10, "test_command": 10,
        "env_example": 5, "no_hardcoded_paths": 10,
    }
    return sum(weight for name, weight in legacy.items() if checks.get(name) is True)


__all__ = ["CHECK_WEIGHTS", "build_report", "evaluate_rules", "grade", "report", "scan", "scan_repository", "score_checks"]
