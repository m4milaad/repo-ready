"""Report API aliases kept separate for discoverability."""

from .context import ConfigError
from .models import CheckResult, Finding, ScanReport
from .scoring import build_report, report, scan, scan_repository

__all__ = [
    "CheckResult",
    "ConfigError",
    "Finding",
    "ScanReport",
    "build_report",
    "report",
    "scan",
    "scan_repository",
]
