"""Offline repository readiness scoring."""

from ._version import __version__

from .context import ConfigError, FileEntry, RepoContext
from .overlay import OverlayContext, SnapshotContext, snapshot_context
from .contracts import ContractDiagnostic, ContractEdge, ContractGraph, Evidence, diagnose
from .baseline import ReportComparison, compare_reports, compare_repository
from .artifact import ArtifactFinding, ArtifactMember, ArtifactReport, inspect_artifact
from .tracked import TrackedFile, TrackedReport, inspect_tracked
from .registry import RuleDescriptor, rule_descriptors
from .models import CheckResult, Finding, ScanReport
from .scoring import build_report, report, resolve_profile, scan, scan_repository
from .inventory import scan_inventory
from .integrity import inspect_integrity
from .showcase import SHOWCASE_SCHEMA_VERSION, analysis_fingerprint, build_showcase
from .showcase_html import render_showcase
from .ecosystems import ECOSYSTEM_PROFILES, EcosystemProfile

__all__ = [
    "CheckResult",
    "ConfigError",
    "ArtifactFinding",
    "ArtifactMember",
    "ArtifactReport",
    "ContractDiagnostic",
    "ContractEdge",
    "ContractGraph",
    "ECOSYSTEM_PROFILES",
    "EcosystemProfile",
    "Evidence",
    "FileEntry",
    "OverlayContext",
    "Finding",
    "ReportComparison",
    "RepoContext",
    "SnapshotContext",
    "ScanReport",
    "SHOWCASE_SCHEMA_VERSION",
    "build_report",
    "compare_reports",
    "compare_repository",
    "analysis_fingerprint",
    "build_showcase",
    "diagnose",
    "inspect_artifact",
    "TrackedFile",
    "TrackedReport",
    "inspect_tracked",
    "RuleDescriptor",
    "rule_descriptors",
    "report",
    "resolve_profile",
    "scan_inventory",
    "inspect_integrity",
    "render_showcase",
    "scan",
    "scan_repository",
    "snapshot_context",
]
