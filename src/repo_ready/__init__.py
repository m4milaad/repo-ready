"""Offline repository readiness scoring."""

__version__ = "0.1.0"

from .context import ConfigError, FileEntry, RepoContext
from .models import CheckResult, Finding, ScanReport
from .scoring import build_report, report, scan, scan_repository

__all__ = [
    "CheckResult",
    "ConfigError",
    "FileEntry",
    "Finding",
    "RepoContext",
    "ScanReport",
    "build_report",
    "report",
    "scan",
    "scan_repository",
]
