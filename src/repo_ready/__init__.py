"""Offline repository readiness scoring."""

__version__ = "0.1.0"

from .context import ConfigError, FileEntry, RepoContext
from .contracts import ContractDiagnostic, ContractEdge, ContractGraph, Evidence, diagnose
from .models import CheckResult, Finding, ScanReport
from .scoring import build_report, report, scan, scan_repository

__all__ = [
    "CheckResult",
    "ConfigError",
    "ContractDiagnostic",
    "ContractEdge",
    "ContractGraph",
    "Evidence",
    "FileEntry",
    "Finding",
    "RepoContext",
    "ScanReport",
    "build_report",
    "diagnose",
    "report",
    "scan",
    "scan_repository",
]
