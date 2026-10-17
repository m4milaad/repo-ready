"""Public scan, scoring, and compatibility helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Mapping

from . import __version__
from .checks import CHECK_MESSAGES, CHECK_WEIGHTS, run_checks
from .context import RepoContext
from .detector import detect_ecosystems
from .models import CheckResult, ScanReport


def _grade(score: int, maximum: int) -> str:
    if maximum <= 0:
        return "N/A"
    percentage = score * 100 / maximum
    if percentage >= 90:
        return "A"
    if percentage >= 75:
        return "B"
    if percentage >= 60:
        return "C"
    if percentage >= 40:
        return "D"
    return "F"


def scan(root: str | Path, *, context: RepoContext | None = None) -> ScanReport:
    """Run the deterministic readiness scan for *root*."""

    ctx = context or RepoContext(root)
    ecosystems = tuple(detect_ecosystems(ctx))
    checks = run_checks(ctx, ecosystems)
    score = sum(check.score for check in checks)
    maximum = sum(check.max_score for check in checks)
    return ScanReport(
        tool="repo-ready",
        version=__version__,
        path=str(ctx.root),
        ecosystems=ecosystems,
        score=score,
        max_score=maximum,
        grade=_grade(score, maximum),
        checks=checks,
    )


def report(root: str | Path, *, context: RepoContext | None = None) -> dict[str, object]:
    """Return the stable JSON-compatible report mapping."""

    return scan(root, context=context).as_dict()


def build_report(root: str | Path, *, context: RepoContext | None = None) -> ScanReport:
    """Model-returning spelling for library callers."""

    return scan(root, context=context)


scan_repository = scan


def evaluate_rules(root: str | Path) -> tuple[CheckResult, ...]:
    """Evaluate the new RR rules in ID order."""

    return scan(root).checks


def score_checks(checks: Mapping[str, object]) -> int:
    """Score either new check models or the old boolean mapping API."""

    total = 0
    legacy_weights = {
        "readme_sections": 15,
        "license": 10,
        "contributing": 10,
        "templates": 10,
        "lockfile": 10,
        "pinned_runtime": 10,
        "dockerfile": 10,
        "test_command": 10,
        "env_example": 5,
        "no_hardcoded_paths": 10,
    }
    for rule_id, weight in CHECK_WEIGHTS.items():
        value = checks.get(rule_id)
        if isinstance(value, CheckResult):
            total += value.score
        elif isinstance(value, bool) and value:
            total += weight
        elif isinstance(value, (int, float)) and value:
            total += min(weight, max(0, int(value)))
    if any(name in checks for name in legacy_weights):
        return sum(weight for name, weight in legacy_weights.items() if checks.get(name) is True)
    return total


# ---------------------------------------------------------------------------
# Compatibility entry points from the initial ten-check public module.
# They remain pure functions and now use the shared indexed context.


def _legacy(root: str | Path, rule_id: str) -> bool:
    return next(check for check in scan(root).checks if check.id == rule_id).status == "pass"


def readme_sections(root: str | Path) -> bool:
    return _legacy(root, "RR01")


def license(root: str | Path) -> bool:
    return _legacy(root, "RR02")


def contributing(root: str | Path) -> bool:
    return _legacy(root, "RR03")


def templates(root: str | Path) -> bool:
    return _legacy(root, "RR04")


def lockfile(root: str | Path) -> bool:
    return _legacy(root, "RR06")


def pinned_runtime(root: str | Path) -> bool:
    return _legacy(root, "RR07")


def dockerfile(root: str | Path) -> bool:
    return _legacy(root, "RR08")


def test_command(root: str | Path) -> bool:
    return _legacy(root, "RR09")


def env_example(root: str | Path) -> bool:
    return _legacy(root, "RR10")


def no_hardcoded_paths(root: str | Path) -> bool:
    return _legacy(root, "RR12")


# Names retained for callers of the original scoring module. New consumers
# should use ``CHECK_WEIGHTS`` and the RR models; this mapping still offers the
# original boolean-callable surface without bypassing RepoContext.
Check = Callable[[Path], bool]
CHECKS: dict[str, Check] = {
    "readme_sections": readme_sections,
    "license": license,
    "contributing": contributing,
    "templates": templates,
    "lockfile": lockfile,
    "pinned_runtime": pinned_runtime,
    "dockerfile": dockerfile,
    "test_command": test_command,
    "env_example": env_example,
    "no_hardcoded_paths": no_hardcoded_paths,
}


def evaluate_legacy_checks(root: str | Path) -> dict[str, bool]:
    return {name: check(root) for name, check in CHECKS.items()}


def evaluate_checks(root: str | Path) -> dict[str, bool]:
    """Backward-compatible boolean evaluation entry point."""

    return evaluate_legacy_checks(root)


__all__ = [
    "CHECK_MESSAGES",
    "CHECKS",
    "CHECK_WEIGHTS",
    "build_report",
    "contributing",
    "dockerfile",
    "env_example",
    "evaluate_checks",
    "evaluate_legacy_checks",
    "evaluate_rules",
    "license",
    "lockfile",
    "no_hardcoded_paths",
    "pinned_runtime",
    "readme_sections",
    "report",
    "scan",
    "scan_repository",
    "score_checks",
    "templates",
    "test_command",
]
