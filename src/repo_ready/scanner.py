"""Compatibility module for callers that name the operation a scan."""

from .context import ConfigError, FileEntry, FileIndex, RepoContext
from .scoring import build_report, report, scan, scan_repository

__all__ = [
    "ConfigError",
    "FileEntry",
    "FileIndex",
    "RepoContext",
    "build_report",
    "report",
    "scan",
    "scan_repository",
]
