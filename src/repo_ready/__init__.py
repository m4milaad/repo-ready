"""Offline repository readiness scoring."""

__version__ = "0.1.0"

from .context import ConfigError, FileEntry, RepoContext
from .contracts import ContractDiagnostic, ContractEdge, ContractGraph, Evidence, diagnose
from .baseline import ReportComparison, compare_reports, compare_repository
from .artifact import ArtifactFinding, ArtifactMember, ArtifactReport, inspect_artifact
from .tracked import TrackedFile, TrackedReport, inspect_tracked
from .models import CheckResult, Finding, ScanReport
from .scoring import build_report, report, scan, scan_repository

__all__ = [
    "CheckResult",
    "ConfigError",
    "ArtifactFinding",
    "ArtifactMember",
    "ArtifactReport",
    "ContractDiagnostic",
    "ContractEdge",
    "ContractGraph",
    "Evidence",
    "FileEntry",
    "Finding",
    "ReportComparison",
    "RepoContext",
    "ScanReport",
    "build_report",
    "compare_reports",
    "compare_repository",
    "diagnose",
    "inspect_artifact",
    "TrackedFile",
    "TrackedReport",
    "inspect_tracked",
    "report",
    "scan",
    "scan_repository",
]
