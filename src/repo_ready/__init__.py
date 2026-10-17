"""Offline repository readiness scoring."""

__version__ = "0.1.0"

from .context import FileEntry, RepoContext
from .models import CheckResult, Finding, ScanReport
from .scoring import build_report, report, scan, scan_repository

__all__ = [
    "CheckResult",
    "FileEntry",
    "Finding",
    "RepoContext",
    "ScanReport",
    "build_report",
    "report",
    "scan",
    "scan_repository",
]
