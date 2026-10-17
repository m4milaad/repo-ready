"""Stable data models for readiness reports."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Finding:
    """One deterministic, relative-path explanation for a check result."""

    file: str
    line: int | None
    detail: str

    def as_dict(self) -> dict[str, object]:
        return {"file": self.file, "line": self.line, "detail": self.detail}

    to_dict = as_dict


@dataclass(frozen=True)
class CheckResult:
    """The public result for one RR01--RR13 check."""

    id: str
    title: str
    weight: int
    earned: int
    status: str
    message: str
    fix_hint: str | None
    fixable: bool
    findings: tuple[Finding, ...] = field(default_factory=tuple)

    @property
    def score(self) -> int:
        """Compatibility alias for callers of the initial MVP API."""

        return self.earned

    @property
    def max_score(self) -> int:
        return self.weight

    @property
    def max(self) -> int:
        return self.weight

    def as_dict(self) -> dict[str, object]:
        findings = sorted(self.findings, key=lambda item: (item.file, item.line or 0, item.detail))
        return {
            "id": self.id,
            "title": self.title,
            "weight": self.weight,
            "earned": self.earned,
            "status": self.status,
            "message": self.message,
            "fix_hint": self.fix_hint,
            "fixable": self.fixable,
            "findings": [finding.as_dict() for finding in findings],
        }

    to_dict = as_dict


@dataclass(frozen=True)
class ScanReport:
    """The complete machine-readable report."""

    tool: str
    version: str
    path: str
    ecosystems: tuple[str, ...]
    score: int
    max_score: int
    grade: str
    checks: tuple[CheckResult, ...]

    @property
    def max(self) -> int:
        return self.max_score

    def as_dict(self) -> dict[str, object]:
        return {
            "tool": self.tool,
            "version": self.version,
            "path": self.path,
            "ecosystems": sorted(self.ecosystems),
            "score": self.score,
            "max": self.max_score,
            "grade": self.grade,
            "checks": [check.as_dict() for check in sorted(self.checks, key=lambda item: item.id)],
        }

    to_dict = as_dict

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]


__all__ = ["CheckResult", "Finding", "ScanReport"]
