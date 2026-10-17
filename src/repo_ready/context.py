"""Deterministic, guarded access to a repository's files.

The scanner deliberately has one filesystem boundary. Checks consume a
``RepoContext`` instead of walking a repository themselves, which keeps the
result independent of directory enumeration order and makes unsafe files
cheap to ignore consistently.
"""

from __future__ import annotations

import fnmatch
import os
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Iterator


MAX_FILE_BYTES = 1 * 1024 * 1024

DEFAULT_EXCLUDES: tuple[str, ...] = (
    ".git", ".hg", ".svn", ".venv", "venv", "env", ".tox", ".nox",
    "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".hypothesis", "dist", "build", "coverage",
)


@dataclass(frozen=True)
class FileEntry:
    """A regular, non-symlink file included in a repository index."""

    path: str
    absolute: Path
    size: int
    too_large: bool = False
    binary: bool = False

    @property
    def name(self) -> str:
        return PurePosixPath(self.path).name

    @property
    def relative_path(self) -> str:
        """Compatibility alias used by callers that prefer explicit naming."""

        return self.path

    @property
    def is_binary(self) -> bool:
        return self.binary

    @property
    def readable(self) -> bool:
        return not self.too_large and not self.binary


def _posix_path(value: str | Path) -> str:
    return str(value).replace("\\", "/").strip("/")


def _pattern_matches(path: str, pattern: str) -> bool:
    """Match both directory prefixes and gitignore-like glob patterns."""

    pattern = _posix_path(pattern)
    while pattern.startswith("./"):
        pattern = pattern[2:]
    if not pattern:
        return False
    if pattern.endswith("/**"):
        pattern = pattern[:-3].rstrip("/")
    if path == pattern or path.startswith(pattern + "/"):
        return True
    pure = PurePosixPath(path)
    if pure.match(pattern) or fnmatch.fnmatchcase(path, pattern):
        return True
    if "/" not in pattern and any(part == pattern for part in pure.parts):
        return True
    return False


def _safe_regular_file(path: Path) -> bool:
    try:
        return path.is_file() and not path.is_symlink()
    except OSError:
        return False


class RepoContext:
    """An immutable view of the readable files in a local repository.

    ``RepoContext`` performs no execution and follows no links. Files larger
    than :data:`MAX_FILE_BYTES` remain in the index as metadata, but guarded
    text/byte reads return ``None`` for them. NUL-containing files are marked
    binary and are likewise skipped by text reads.
    """

    def __init__(
        self,
        root: str | os.PathLike[str] | Path,
        *,
        excludes: Iterable[str] = (),
        max_file_bytes: int = MAX_FILE_BYTES,
    ) -> None:
        candidate = Path(root).expanduser()
        try:
            candidate = candidate.absolute()
        except OSError:
            candidate = Path(os.path.abspath(os.fspath(candidate)))
        self.root = candidate
        self.max_file_bytes = max(0, int(max_file_bytes))
        self.config_excludes = self._read_config_excludes()
        self.excludes = tuple(
            dict.fromkeys((*DEFAULT_EXCLUDES, *self.config_excludes, *excludes))
        )
        self._entries = self._build_index()
        self._by_path = {entry.path: entry for entry in self._entries}

    def _read_config_excludes(self) -> tuple[str, ...]:
        config = self.root / ".repo-ready.toml"
        if not _safe_regular_file(config):
            return ()
        try:
            if config.stat().st_size > self.max_file_bytes:
                return ()
            raw = config.read_bytes()
            if b"\x00" in raw:
                return ()
            document = tomllib.loads(raw.decode("utf-8", errors="replace"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError):
            return ()
        values = document.get("exclude") if isinstance(document, dict) else None
        if not isinstance(values, list):
            return ()
        return tuple(value for value in values if isinstance(value, str) and value.strip())

    def is_excluded(self, relative: str | Path) -> bool:
        path = _posix_path(relative)
        return any(_pattern_matches(path, pattern) for pattern in self.excludes)

    def _build_index(self) -> tuple[FileEntry, ...]:
        if not _safe_directory(self.root):
            return ()
        found: list[FileEntry] = []
        self._walk(self.root, "", found)
        found.sort(key=lambda entry: entry.path)
        return tuple(found)

    def _walk(self, directory: Path, prefix: str, found: list[FileEntry]) -> None:
        try:
            with os.scandir(directory) as handle:
                entries = sorted(handle, key=lambda item: item.name)
        except OSError:
            return
        for item in entries:
            relative = f"{prefix}/{item.name}" if prefix else item.name
            relative = _posix_path(relative)
            if self.is_excluded(relative):
                continue
            try:
                if item.is_symlink():
                    continue
                if item.is_dir(follow_symlinks=False):
                    self._walk(Path(item.path), relative, found)
                    continue
                if not item.is_file(follow_symlinks=False):
                    continue
                size = item.stat(follow_symlinks=False).st_size
            except OSError:
                continue
            too_large = size > self.max_file_bytes
            binary = False
            if not too_large:
                try:
                    binary = b"\x00" in Path(item.path).read_bytes()
                except OSError:
                    continue
            found.append(FileEntry(relative, Path(item.path), size, too_large, binary))

    @property
    def files(self) -> tuple[FileEntry, ...]:
        """Files in stable POSIX-relative order."""

        return self._entries

    @property
    def index(self) -> tuple[FileEntry, ...]:
        """Stable file-index alias."""

        return self._entries

    @property
    def file_index(self) -> tuple[FileEntry, ...]:
        return self._entries

    def __iter__(self) -> Iterator[FileEntry]:
        return iter(self._entries)

    def iter_files(self) -> Iterator[FileEntry]:
        return iter(self._entries)

    def entry(self, path: str | Path | FileEntry) -> FileEntry | None:
        if isinstance(path, FileEntry):
            return path
        return self._by_path.get(_posix_path(path))

    def has_file(self, path: str | Path, *, readable: bool = False) -> bool:
        entry = self.entry(path)
        if entry is None:
            return False
        return not readable or not entry.too_large

    def read_bytes(self, path: str | Path | FileEntry) -> bytes | None:
        """Read an indexed file, returning ``None`` for unsafe/unreadable data."""

        entry = self.entry(path)
        if entry is None or entry.too_large or entry.binary:
            return None
        flags = os.O_RDONLY
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        flags |= nofollow
        try:
            descriptor = os.open(entry.absolute, flags)
        except (OSError, ValueError, TypeError):
            return None
        try:
            information = os.fstat(descriptor)
            if not stat.S_ISREG(information.st_mode) or information.st_size > self.max_file_bytes:
                return None
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                data = handle.read(self.max_file_bytes + 1)
        except (OSError, ValueError):
            return None
        finally:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        if len(data) > self.max_file_bytes or b"\x00" in data:
            return None
        return data

    def read_text(
        self,
        path: str | Path | FileEntry,
        *,
        errors: str = "replace",
    ) -> str | None:
        """Read UTF-8 text with replacement by default.

        Invalid UTF-8 is retained as replacement characters so a malformed
        source file cannot hide a finding. Binary and oversized files are
        skipped before decoding. Passing ``errors='strict'`` gives callers a
        strict-read option while retaining the same guarded filesystem path.
        """

        data = self.read_bytes(path)
        if data is None:
            return None
        try:
            return data.decode("utf-8", errors=errors)
        except (UnicodeDecodeError, LookupError):
            return None

    def read(self, path: str | Path | FileEntry, *, errors: str = "replace") -> str | None:
        """Short compatibility alias for :meth:`read_text`."""

        return self.read_text(path, errors=errors)

    def read_file(self, path: str | Path | FileEntry, *, errors: str = "replace") -> str | None:
        return self.read_text(path, errors=errors)

    def relative(self, path: Path) -> str:
        try:
            return _posix_path(path.relative_to(self.root))
        except ValueError:
            return _posix_path(path)


def _safe_directory(path: Path) -> bool:
    try:
        return path.is_dir() and not path.is_symlink()
    except OSError:
        return False


FileIndex = tuple[FileEntry, ...]


__all__ = ["DEFAULT_EXCLUDES", "MAX_FILE_BYTES", "FileEntry", "FileIndex", "RepoContext"]
