"""Read the Git index without invoking Git.

This is an optional repository-scope primitive.  Normal readiness scans still
use the filesystem view.  Reading the index directly keeps tracked-tree
inspection offline and compatible with the scanner's no-subprocess boundary.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path


TRACKED_SCHEMA_VERSION = 1
MAX_INDEX_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True)
class TrackedFile:
    path: str
    stage: int

    def as_dict(self) -> dict[str, object]:
        return {"path": self.path, "stage": self.stage}


@dataclass(frozen=True)
class TrackedReport:
    version: int
    path: str
    index_version: int | None
    files: tuple[TrackedFile, ...]
    findings: tuple[dict[str, str], ...]

    @property
    def safe(self) -> bool:
        return not any(item.get("severity") == "error" for item in self.findings)

    def as_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "path": self.path,
            "index_version": self.index_version,
            "safe": self.safe,
            "files": [item.as_dict() for item in self.files],
            "findings": list(self.findings),
        }


def _git_directory(root: Path) -> Path | None:
    marker = root / ".git"
    try:
        if marker.is_dir() and not marker.is_symlink():
            return marker
        if marker.is_file():
            text = marker.read_text(encoding="utf-8").strip()
            if text.startswith("gitdir:"):
                value = text[7:].strip()
                candidate = Path(value)
                return (root / candidate).resolve(strict=False) if not candidate.is_absolute() else candidate
    except (OSError, UnicodeError, RuntimeError):
        return None
    return None


def _finding(code: str, severity: str, detail: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "detail": detail}


def _parse_index(data: bytes, findings: list[dict[str, str]]) -> tuple[int | None, list[TrackedFile]]:
    if len(data) < 12 or data[:4] != b"DIRC":
        findings.append(_finding("TR001", "error", "Git index signature is invalid."))
        return None, []
    version, count = struct.unpack(">II", data[4:12])
    if version not in {2, 3}:
        findings.append(_finding("TR002", "error", f"Git index version {version} is unsupported."))
        return version, []
    files: list[TrackedFile] = []
    offset = 12
    for _ in range(count):
        entry_start = offset
        if offset + 62 > len(data):
            findings.append(_finding("TR003", "error", "Git index ends before an entry is complete."))
            break
        offset += 40 + 20
        flags = struct.unpack(">H", data[offset : offset + 2])[0]
        offset += 2
        if version >= 3 and flags & 0x4000:
            if offset + 2 > len(data):
                findings.append(_finding("TR003", "error", "Git index extension flags are truncated."))
                break
            offset += 2
        name_length = flags & 0x0FFF
        if name_length < 0x0FFF:
            end = offset + name_length
            if end >= len(data) or data[end] != 0:
                findings.append(_finding("TR004", "error", "Git index entry path is malformed."))
                break
            raw_name = data[offset:end]
            offset = end + 1
        else:
            end = data.find(b"\0", offset)
            if end < 0:
                findings.append(_finding("TR004", "error", "Git index entry path is unterminated."))
                break
            raw_name = data[offset:end]
            offset = end + 1
        offset = entry_start + ((offset - entry_start + 7) // 8) * 8
        try:
            name = raw_name.decode("utf-8", "surrogateescape").replace("\\", "/")
        except UnicodeError:
            findings.append(_finding("TR005", "error", "Git index entry path is not decodable."))
            continue
        if not name or name.startswith("/") or ".." in Path(name).parts or "\x00" in name:
            findings.append(_finding("TR006", "error", f"Git index entry path is unsafe: {name!r}."))
            continue
        files.append(TrackedFile(name, (flags >> 12) & 0x3))
    return version, sorted(files, key=lambda item: (item.path, item.stage))


def inspect_tracked(root: str | Path) -> TrackedReport:
    """Read the repository's tracked paths from its Git index."""

    candidate = Path(root).expanduser()
    findings: list[dict[str, str]] = []
    git_directory = _git_directory(candidate)
    if git_directory is None:
        findings.append(_finding("TR000", "warning", "No readable Git directory was found."))
        return TrackedReport(TRACKED_SCHEMA_VERSION, str(candidate), None, (), tuple(findings))
    index = git_directory / "index"
    try:
        if index.stat().st_size > MAX_INDEX_BYTES:
            findings.append(_finding("TR007", "error", "Git index exceeds the size limit."))
            return TrackedReport(TRACKED_SCHEMA_VERSION, str(candidate), None, (), tuple(findings))
        data = index.read_bytes()
    except OSError as error:
        findings.append(_finding("TR008", "error", f"Git index could not be read: {error}"))
        return TrackedReport(TRACKED_SCHEMA_VERSION, str(candidate), None, (), tuple(findings))
    version, files = _parse_index(data, findings)
    return TrackedReport(TRACKED_SCHEMA_VERSION, str(candidate), version, tuple(files), tuple(findings))


__all__ = ["TRACKED_SCHEMA_VERSION", "TrackedFile", "TrackedReport", "inspect_tracked"]
