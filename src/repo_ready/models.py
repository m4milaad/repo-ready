"""Stable public report models used by the CLI and library callers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Finding:
    """A deterministic explanation for a check result."""

    file: str
    line: int | None
    detail: str

    def as_dict(self) -> dict[str, object]:
        return {"file": self.file, "line": self.line, "detail": self.detail}

    to_dict = as_dict


@dataclass(frozen=True)
class CheckResult:
    """One readiness rule and its weighted outcome."""

    id: str
    message: str
    weight: int
    status: str
    score: int
    max_score: int
    findings: tuple[Finding, ...] = field(default_factory=tuple)

    @property
    def max(self) -> int:
        return self.max_score

    def as_dict(self) -> dict[str, object]:
        findings = sorted(self.findings, key=lambda item: (item.file, item.line or 0))
        return {
            "id": self.id,
            "message": self.message,
            "weight": self.weight,
            "status": self.status,
            "score": self.score,
            "max": self.max_score,
            "findings": [finding.as_dict() for finding in findings],
        }

    to_dict = as_dict


@dataclass(frozen=True)
class ScanReport:
    """The complete machine-readable scan result."""

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
        checks = sorted(self.checks, key=lambda check: check.id)
        return {
            "tool": self.tool,
            "version": self.version,
            "path": self.path,
            "ecosystems": list(self.ecosystems),
            "score": self.score,
            "max": self.max_score,
            "grade": self.grade,
            "checks": [check.as_dict() for check in checks],
        }

    to_dict = as_dict

    def __getitem__(self, key: str) -> Any:
        return self.as_dict()[key]


__all__ = ["CheckResult", "Finding", "ScanReport"]
