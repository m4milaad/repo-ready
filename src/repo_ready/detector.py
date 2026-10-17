"""Ecosystem detection for local repositories.

Detection is intentionally based on marker files only.  It does not import
project metadata, execute package managers, or inspect the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class EcosystemDetector(Protocol):
    """The common interface implemented by all ecosystem detectors."""

    name: str

    def detect(self, root: Path) -> bool:
        """Return whether *root* contains a marker for this ecosystem."""


@dataclass(frozen=True)
class MarkerFileDetector:
    """Detect an ecosystem when at least one marker file exists."""

    name: str
    marker_files: tuple[str, ...]

    def detect(self, root: Path) -> bool:
        return any((root / marker).is_file() for marker in self.marker_files)

    def detects(self, root: Path) -> bool:
        """Backward-compatible spelling for the detector operation."""

        return self.detect(root)


# Keep this sequence ordered: Python is deliberately detected before Node.
ECOSYSTEM_DETECTORS: tuple[EcosystemDetector, ...] = (
    MarkerFileDetector(
        "python",
        ("pyproject.toml", "requirements.txt", "uv.lock", "poetry.lock"),
    ),
    MarkerFileDetector(
        "node",
        ("package.json", "package-lock.json", ".nvmrc"),
    ),
)


def detect_ecosystems(root: Path) -> list[str]:
    """Return detected ecosystem names in stable priority order."""

    root = Path(root)
    return [detector.name for detector in ECOSYSTEM_DETECTORS if detector.detect(root)]


def detect(root: Path) -> list[str]:
    """Compatibility alias for callers that prefer a shorter API name."""

    return detect_ecosystems(root)
