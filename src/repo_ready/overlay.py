r"""Immutable, in-memory repository snapshots and what-if views.

Public API::

    SnapshotContext(base: RepoContext, *, max_snapshot_bytes: int = 16 * 1024 * 1024)
    snapshot_context(base: RepoContext, *, max_snapshot_bytes: int = 16 * 1024 * 1024) -> SnapshotContext
    OverlayContext(base: RepoContext, changes: Mapping[str, str | bytes | None])

Both contexts subclass ``RepoContext`` and work directly with
``report(root, context=view)``, ``detect_ecosystems(view)``, and
``build_contract_graph(view)``. A snapshot captures each eligible file at most
once, in index order, exclusively through ``base.read_bytes(index_entry)``.
It retains the original metadata even for unavailable contents; later reads
never consult the source. This is a bounded capture, not an atomic filesystem
snapshot of files being edited concurrently during construction.

``limitations: tuple[str, ...]`` contains inherited index truncation reasons,
``"max_snapshot_bytes"`` if readable contents could not fit, and
``"unreadable:<POSIX path>"`` for otherwise-readable entries whose capture
failed. Binary and oversized entries remain guarded metadata, as in
``RepoContext``. Skipped contents are never fetched lazily.

An overlay UTF-8 encodes ``str`` values, uses ``bytes`` verbatim, and removes
files for ``None``. Keys must already be normalized, relative POSIX file paths;
invalid keys raise ``ValueError`` and invalid key/value types raise
``TypeError``. Root ``.repo-ready.toml`` edits (including removal and case
variants) raise ``ValueError`` because configuration is frozen. Excluded paths
and paths outside an explicit ``include_paths`` scope are ignored. Virtual
files obey the base's per-file size and binary guards; index traversal budgets
are retained as metadata, not reapplied to explicit changes.

Passing an ordinary ``RepoContext`` to ``OverlayContext`` first captures it
with the default snapshot budget. Passing a snapshot or another overlay reuses
its immutable bytes without accessing the filesystem. Reuse one explicit
snapshot for multiple scenarios, especially with a custom capture budget::

    frozen = snapshot_context(base, max_snapshot_bytes=32 * 1024 * 1024)
    proposed = OverlayContext(frozen, {"README.md": "# Proposed\n", "old.py": None})

Files/index aliases, iteration, lookups, read aliases, and configuration/budget
metadata retain the ``RepoContext`` interface. ``observed_files`` and
``observed_bytes`` describe the resulting index. ``has_file(readable=True)``
requires captured, guarded bytes, including for empty files. Only a view's own
``FileEntry`` objects are accepted by its lookup/read methods: stale, removed,
forged, and other-context entries return ``None``. Path-based reads may also
use relative ``Path`` objects. No context here opens paths or creates files.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import MappingProxyType

from .context import FileEntry, RepoContext


DEFAULT_MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024


def _validate_path(path: str) -> str:
    if not isinstance(path, str):
        raise TypeError("overlay paths must be strings")
    windows = PureWindowsPath(path)
    if (
        not path
        or "\x00" in path
        or "\\" in path
        or PurePosixPath(path).is_absolute()
        or windows.drive
        or windows.root
        or any(part in {"", ".", ".."} for part in path.split("/"))
        or any(PureWindowsPath(part).drive for part in path.split("/"))
    ):
        raise ValueError("overlay paths must be normalized relative POSIX file paths")
    return path


def _guarded(data: bytes, max_file_bytes: int) -> bool:
    # Match RepoContext's bounded binary probe, rather than scanning all bytes.
    return len(data) <= max_file_bytes and b"\x00" not in data[:8192]


class _FrozenContext(RepoContext):
    """Shared storage and safe overrides; never run RepoContext.__init__."""

    limitations: tuple[str, ...]

    def __setattr__(self, name: str, value: object) -> None:
        if self.__dict__.get("_frozen", False):
            raise AttributeError(f"{type(self).__name__} is immutable")
        super().__setattr__(name, value)

    def __delattr__(self, name: str) -> None:
        if self.__dict__.get("_frozen", False):
            raise AttributeError(f"{type(self).__name__} is immutable")
        super().__delattr__(name)

    def _inherit_metadata(self, base: RepoContext) -> None:
        self.root = base.root
        self.max_file_bytes = base.max_file_bytes
        self.max_files = base.max_files
        self.max_total_bytes = base.max_total_bytes
        self.include_paths = base.include_paths
        self.min_score = base.min_score
        self.config_excludes = base.config_excludes
        self.excludes = base.excludes
        self._exclude_patterns = base._exclude_patterns
        self._component_excludes = base._component_excludes
        self._prefix_excludes = base._prefix_excludes
        self._glob_excludes = base._glob_excludes
        self._truncated = base.truncated
        self._truncation_reasons = frozenset(base.truncation_reasons)

    def _freeze(
        self,
        entries: Mapping[str, FileEntry],
        contents: Mapping[str, bytes],
        limitations: tuple[str, ...],
    ) -> None:
        # Fresh entries establish ownership even when their metadata is equal.
        self._entries = tuple(replace(entries[path]) for path in sorted(entries))
        self._by_path = MappingProxyType({entry.path: entry for entry in self._entries})
        self._contents = MappingProxyType(dict(contents))
        self._observed_files = len(self._entries)
        self._observed_bytes = sum(entry.size for entry in self._entries)
        self.limitations = tuple(sorted(set(limitations)))
        self._frozen = True

    def entry(self, path: str | Path | FileEntry) -> FileEntry | None:
        if isinstance(path, FileEntry):
            return path if self._by_path.get(path.path) is path else None
        if isinstance(path, Path):
            path = path.as_posix()
        try:
            relative = _validate_path(path)
        except (TypeError, ValueError):
            return None
        return self._by_path.get(relative)

    def has_file(self, path: str | Path, *, readable: bool = False) -> bool:
        entry = self.entry(path)
        return entry is not None and (not readable or self.read_bytes(entry) is not None)

    def read_bytes(self, path: str | Path | FileEntry) -> bytes | None:
        entry = self.entry(path)
        if entry is None or not entry.readable:
            return None
        return self._contents.get(entry.path)


class SnapshotContext(_FrozenContext):
    """Freeze indexed bytes once under a non-negative integer memory budget.

    ``max_snapshot_bytes`` defaults to 16 MiB. Negative values raise
    ``ValueError``; non-integers (including booleans) raise ``TypeError``.
    Whole files that do not fit are skipped while later, smaller files may
    still fit. Metadata and original index truncation flags are preserved;
    capture incompleteness is reported separately through ``limitations``.
    """

    def __init__(
        self,
        base: RepoContext,
        *,
        max_snapshot_bytes: int = DEFAULT_MAX_SNAPSHOT_BYTES,
    ) -> None:
        if not isinstance(base, RepoContext):
            raise TypeError("base must be a RepoContext")
        if isinstance(max_snapshot_bytes, bool) or not isinstance(max_snapshot_bytes, int):
            raise TypeError("max_snapshot_bytes must be a non-negative integer")
        if max_snapshot_bytes < 0:
            raise ValueError("max_snapshot_bytes must be non-negative")
        self._inherit_metadata(base)
        self.max_snapshot_bytes = max_snapshot_bytes
        entries = {entry.path: entry for entry in base.files}
        contents: dict[str, bytes] = {}
        limitations = set(getattr(base, "limitations", ())) | set(base.truncation_reasons)
        remaining = max_snapshot_bytes
        for entry in sorted(entries.values(), key=lambda item: item.path):
            if not entry.readable:
                continue
            if entry.size > remaining:
                limitations.add("max_snapshot_bytes")
                continue
            try:
                data = base.read_bytes(entry)
            except OSError:
                data = None
            if data is None or not _guarded(data, self.max_file_bytes):
                limitations.add(f"unreadable:{entry.path}")
                continue
            if len(data) > remaining:
                limitations.add("max_snapshot_bytes")
                continue
            contents[entry.path] = data
            remaining -= len(data)
        self._freeze(entries, contents, tuple(limitations))


def snapshot_context(
    base: RepoContext,
    *,
    max_snapshot_bytes: int = DEFAULT_MAX_SNAPSHOT_BYTES,
) -> SnapshotContext:
    """Return a new immutable :class:`SnapshotContext`, never a live view."""

    return SnapshotContext(base, max_snapshot_bytes=max_snapshot_bytes)


class OverlayContext(_FrozenContext):
    """Apply immutable virtual changes to a frozen repository view.

    Neither the supplied mapping nor the base is mutated. Unchanged byte
    objects are shared; virtual replacements are never written to disk.
    """

    def __init__(self, base: RepoContext, changes: Mapping[str, str | bytes | None]) -> None:
        if not isinstance(base, RepoContext):
            raise TypeError("base must be a RepoContext")
        if not isinstance(changes, Mapping):
            raise TypeError("changes must be a mapping")
        normalized: dict[str, bytes | None] = {}
        # Validate everything before capturing a live source.
        for path, value in changes.items():
            _validate_path(path)
            if path.casefold() == ".repo-ready.toml":
                raise ValueError("virtual .repo-ready.toml changes are not supported; configuration is frozen")
            if value is not None and not isinstance(value, (str, bytes)):
                raise TypeError("overlay values must be str, bytes, or None")
            normalized[path] = value.encode("utf-8") if isinstance(value, str) else value

        source = base if isinstance(base, _FrozenContext) else snapshot_context(base)
        self._inherit_metadata(source)
        self.max_snapshot_bytes = source.max_snapshot_bytes
        entries = {entry.path: entry for entry in source.files}
        contents = dict(source._contents)
        for path, data in normalized.items():
            if self.is_excluded(path) or (self.include_paths is not None and path not in self.include_paths):
                continue
            contents.pop(path, None)
            if data is None:
                entries.pop(path, None)
                continue
            size = len(data)
            too_large = size > self.max_file_bytes
            binary = not too_large and b"\x00" in data[:8192]
            entries[path] = FileEntry(path, self.root.joinpath(*path.split("/")), size, too_large, binary)
            if not too_large and not binary:
                contents[path] = data
        self._freeze(entries, contents, source.limitations)


__all__ = [
    "DEFAULT_MAX_SNAPSHOT_BYTES",
    "OverlayContext",
    "SnapshotContext",
    "snapshot_context",
]
