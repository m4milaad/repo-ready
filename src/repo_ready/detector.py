"""Ecosystem detection for local repositories.

Detection is intentionally based on marker files only.  It does not import
project metadata, execute package managers, or inspect the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .context import RepoContext


class EcosystemDetector(Protocol):
    """The common interface implemented by all ecosystem detectors."""

    name: str

    def detect(self, root: Path | RepoContext) -> bool:
        """Return whether *root* contains a marker for this ecosystem."""


@dataclass(frozen=True)
class MarkerFileDetector:
    """Detect an ecosystem when at least one marker file exists."""

    name: str
    marker_files: tuple[str, ...]

    def detect(self, root: Path | RepoContext) -> bool:
        context = root if isinstance(root, RepoContext) else RepoContext(root)
        return any(context.has_file(marker) for marker in self.marker_files)

    def detects(self, root: Path | RepoContext) -> bool:
        """Backward-compatible spelling for the detector operation."""

        return self.detect(root)


# Keep this sequence ordered: Python is deliberately detected before Node.
ECOSYSTEM_DETECTORS: tuple[EcosystemDetector, ...] = (
    MarkerFileDetector(
        "python",
        (
            "pyproject.toml",
            "requirements.txt",
            "requirements.lock",
            "setup.py",
            "setup.cfg",
            "Pipfile",
            "uv.lock",
            "poetry.lock",
        ),
    ),
    MarkerFileDetector(
        "node",
        (
            "package.json",
            "package-lock.json",
            "npm-shrinkwrap.json",
            "yarn.lock",
            "pnpm-lock.yaml",
            ".nvmrc",
        ),
    ),
)


def detect_ecosystems(root: Path | RepoContext) -> list[str]:
    """Return detected ecosystem names in stable priority order."""

    context = root if isinstance(root, RepoContext) else RepoContext(root)
    return [detector.name for detector in ECOSYSTEM_DETECTORS if detector.detect(context)]


def detect(root: Path | RepoContext) -> list[str]:
    """Compatibility alias for callers that prefer a shorter API name."""

    return detect_ecosystems(root)
