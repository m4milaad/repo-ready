"""Safe inspection of Python release artifacts.

Artifacts are inspected in place.  They are never extracted, imported, or
executed.  The inspector supports wheels (ZIP) and source distributions
(tar/tar.gz) and can optionally compare a wheel with a source checkout.
"""

from __future__ import annotations

import re
import tarfile
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .context import RepoContext


ARTIFACT_SCHEMA_VERSION = 1
MAX_MEMBER_COUNT = 100_000
MAX_MEMBER_BYTES = 64 * 1024 * 1024
_VERSION = re.compile(r"(?m)^Version:\s*(\S+)\s*$")


@dataclass(frozen=True)
class ArtifactMember:
    path: str
    kind: str
    size: int
    safe: bool

    def as_dict(self) -> dict[str, object]:
        return {"path": self.path, "kind": self.kind, "size": self.size, "safe": self.safe}


@dataclass(frozen=True)
class ArtifactFinding:
    code: str
    severity: str
    detail: str
    member: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {"code": self.code, "severity": self.severity, "detail": self.detail, "member": self.member}


@dataclass(frozen=True)
class ArtifactReport:
    version: int
    artifact: str
    format: str
    safe: bool
    members: tuple[ArtifactMember, ...]
    findings: tuple[ArtifactFinding, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "artifact": self.artifact,
            "format": self.format,
            "safe": self.safe,
            "members": [member.as_dict() for member in self.members],
            "findings": [finding.as_dict() for finding in self.findings],
        }


def _safe_member_path(value: str) -> bool:
    pure = PurePosixPath(value.replace("\\", "/"))
    return bool(value) and not pure.is_absolute() and ".." not in pure.parts and "\x00" not in value


def _finding(findings: list[ArtifactFinding], code: str, severity: str, detail: str, member: str | None = None) -> None:
    item = ArtifactFinding(code, severity, detail, member)
    if item not in findings:
        findings.append(item)


def _format_for(path: Path) -> str:
    name = path.name.casefold()
    if name.endswith(".whl") or name.endswith(".zip"):
        return "wheel"
    if name.endswith((".tar.gz", ".tgz", ".tar", ".tar.bz2", ".tar.xz")):
        return "sdist"
    return "unknown"


def _metadata_version(text: str) -> str | None:
    match = _VERSION.search(text)
    return match.group(1) if match else None


def _project_version(project: Path) -> str | None:
    try:
        document = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return None
    value = document.get("project") if isinstance(document, dict) else None
    version = value.get("version") if isinstance(value, dict) else None
    return version if isinstance(version, str) else None


def _wheel_members(path: Path, findings: list[ArtifactFinding]) -> tuple[list[ArtifactMember], dict[str, bytes]]:
    members: list[ArtifactMember] = []
    contents: dict[str, bytes] = {}
    seen: set[str] = set()
    try:
        archive = zipfile.ZipFile(path)
        infos = archive.infolist()
    except (OSError, zipfile.BadZipFile) as error:
        _finding(findings, "AR001", "error", f"wheel archive could not be read: {error}")
        return members, contents
    if len(infos) > MAX_MEMBER_COUNT:
        _finding(findings, "AR002", "error", "artifact contains too many members")
    total = 0
    for info in infos[:MAX_MEMBER_COUNT]:
        name = info.filename.replace("\\", "/")
        safe = _safe_member_path(name)
        kind = "file"
        if info.is_dir():
            kind = "directory"
        elif (info.external_attr >> 16) & 0o170000 == 0o120000:
            kind = "symlink"
            safe = False
        if not safe:
            _finding(findings, "AR003", "error", "artifact member path is unsafe", name)
        if name in seen:
            _finding(findings, "AR004", "error", "artifact contains a duplicate member", name)
        seen.add(name)
        total += info.file_size
        if total > MAX_MEMBER_BYTES:
            _finding(findings, "AR005", "error", "artifact members exceed the size limit", name)
            break
        members.append(ArtifactMember(name, kind, info.file_size, safe))
        if safe and kind == "file" and info.file_size <= MAX_MEMBER_BYTES:
            try:
                contents[name] = archive.read(info)
            except (OSError, zipfile.BadZipFile):
                _finding(findings, "AR006", "error", "artifact member could not be read", name)
    archive.close()
    return members, contents


def _sdist_members(path: Path, findings: list[ArtifactFinding]) -> tuple[list[ArtifactMember], dict[str, bytes]]:
    members: list[ArtifactMember] = []
    contents: dict[str, bytes] = {}
    seen: set[str] = set()
    try:
        archive = tarfile.open(path, mode="r:*")
        infos = archive.getmembers()
    except (OSError, tarfile.TarError) as error:
        _finding(findings, "AR001", "error", f"source archive could not be read: {error}")
        return members, contents
    if len(infos) > MAX_MEMBER_COUNT:
        _finding(findings, "AR002", "error", "artifact contains too many members")
    total = 0
    for info in infos[:MAX_MEMBER_COUNT]:
        name = info.name.replace("\\", "/")
        safe = _safe_member_path(name) and not (info.issym() or info.islnk())
        kind = "directory" if info.isdir() else "file"
        if info.issym() or info.islnk():
            kind = "symlink"
        if not safe:
            _finding(findings, "AR003", "error", "artifact member path or link is unsafe", name)
        if name in seen:
            _finding(findings, "AR004", "error", "artifact contains a duplicate member", name)
        seen.add(name)
        total += max(0, info.size)
        if total > MAX_MEMBER_BYTES:
            _finding(findings, "AR005", "error", "artifact members exceed the size limit", name)
            break
        members.append(ArtifactMember(name, kind, max(0, info.size), safe))
        if safe and kind == "file" and info.size <= MAX_MEMBER_BYTES:
            stream = archive.extractfile(info)
            if stream is not None:
                contents[name] = stream.read(MAX_MEMBER_BYTES + 1)
    archive.close()
    return members, contents


def _compare_project(
    project: Path,
    artifact_format: str,
    members: list[ArtifactMember],
    contents: dict[str, bytes],
    findings: list[ArtifactFinding],
) -> None:
    expected_version = _project_version(project)
    metadata = next((contents[item.path].decode("utf-8", "replace") for item in members if item.path.endswith(".dist-info/METADATA")), None)
    actual_version = _metadata_version(metadata) if metadata is not None else None
    if expected_version and actual_version and expected_version != actual_version:
        _finding(findings, "AR010", "error", f"artifact version {actual_version!r} differs from project version {expected_version!r}")
    if artifact_format == "wheel":
        required = [
            "repo_ready/__init__.py",
            "repo_ready/cli.py",
        ]
        member_names = {item.path for item in members}
        for path in required:
            if path not in member_names:
                _finding(findings, "AR011", "error", f"wheel is missing required package file {path!r}", path)
        source_root = project / "src" / "repo_ready"
        if source_root.is_dir():
            context = RepoContext(project)
            source_files = {
                entry.path.removeprefix("src/")
                for entry in context
                if entry.path.startswith("src/repo_ready/") and entry.path.endswith(".py")
            }
            artifact_files = {item.path for item in members if item.path.startswith("repo_ready/") and item.path.endswith(".py")}
            for missing in sorted(source_files - artifact_files):
                _finding(findings, "AR012", "error", f"wheel is missing source module {missing!r}", missing)


def inspect_artifact(path: str | Path, *, project: str | Path | None = None) -> ArtifactReport:
    """Inspect a wheel or source distribution without extracting it."""

    candidate = Path(path)
    artifact_format = _format_for(candidate)
    findings: list[ArtifactFinding] = []
    if artifact_format == "wheel":
        members, contents = _wheel_members(candidate, findings)
    elif artifact_format == "sdist":
        members, contents = _sdist_members(candidate, findings)
    else:
        members, contents = [], {}
        _finding(findings, "AR000", "error", "artifact format is not a supported wheel or source distribution")

    names = {item.path for item in members}
    if artifact_format == "wheel" and members:
        for suffix in (".dist-info/METADATA", ".dist-info/WHEEL", ".dist-info/RECORD"):
            if not any(name.endswith(suffix) for name in names):
                _finding(findings, "AR007", "error", f"wheel is missing required metadata file {suffix}")
    elif artifact_format == "sdist" and members:
        if not any(name.endswith("/pyproject.toml") or name == "pyproject.toml" for name in names):
            _finding(findings, "AR008", "warning", "source distribution has no pyproject.toml")

    if project is not None:
        _compare_project(Path(project), artifact_format, members, contents, findings)

    members = sorted(members, key=lambda item: (item.path, item.kind, item.size))
    findings = sorted(findings, key=lambda item: (item.code, item.member or "", item.detail))
    return ArtifactReport(
        version=ARTIFACT_SCHEMA_VERSION,
        artifact=str(candidate),
        format=artifact_format,
        safe=not any(item.severity == "error" for item in findings),
        members=tuple(members),
        findings=tuple(findings),
    )


__all__ = [
    "ARTIFACT_SCHEMA_VERSION",
    "ArtifactFinding",
    "ArtifactMember",
    "ArtifactReport",
    "inspect_artifact",
]
