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
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterable, Iterator


MAX_FILE_BYTES = 1 * 1024 * 1024

DEFAULT_EXCLUDES: tuple[str, ...] = (
    ".git", ".hg", ".svn", ".venv", "venv", "env", ".tox", ".nox",
    "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".hypothesis", "dist", "build", "coverage",
)


class ConfigError(ValueError):
    """Raised when the root ``.repo-ready.toml`` is invalid."""


@dataclass(frozen=True)
class _RepoConfig:
    excludes: tuple[str, ...] = ()
    min_score: int | None = None


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


def _normalize_pattern(pattern: str) -> str:
    pattern = _posix_path(pattern)
    while pattern.startswith("./"):
        pattern = pattern[2:]
    if pattern.endswith("/**"):
        pattern = pattern[:-3].rstrip("/")
    return pattern


def _pattern_matches(path: str, pattern: str) -> bool:
    """Match both directory prefixes and gitignore-like glob patterns."""

    return _pattern_matches_normalized(path, _normalize_pattern(pattern))


def _pattern_matches_normalized(path: str, pattern: str) -> bool:
    """Match a path against a pattern that has already been normalized."""

    if not pattern:
        return False
    if path == pattern or path.startswith(pattern + "/"):
        return True
    if not any(character in pattern for character in "*?["):
        return "/" not in pattern and pattern in path.split("/")
    pure = PurePosixPath(path)
    if pure.match(pattern) or fnmatch.fnmatchcase(path, pattern):
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
    binary and are likewise skipped by text reads. Optional file-count and
    total-byte budgets stop traversal deterministically and expose the reason
    through :attr:`truncation_reasons`.
    """

    def __init__(
        self,
        root: str | os.PathLike[str] | Path,
        *,
        excludes: Iterable[str] = (),
        max_file_bytes: int = MAX_FILE_BYTES,
        max_files: int | None = None,
        max_total_bytes: int | None = None,
    ) -> None:
        candidate = Path(root).expanduser()
        try:
            candidate = candidate.absolute()
        except OSError:
            candidate = Path(os.path.abspath(os.fspath(candidate)))
        self.root = candidate
        self.max_file_bytes = max(0, int(max_file_bytes))
        self.max_files = None if max_files is None else max(0, int(max_files))
        self.max_total_bytes = None if max_total_bytes is None else max(0, int(max_total_bytes))
        self._truncated = False
        self._truncation_reasons: set[str] = set()
        self._observed_files = 0
        self._observed_bytes = 0
        config = self._read_config()
        self.min_score = config.min_score
        self.config_excludes = config.excludes
        self.excludes = tuple(
            dict.fromkeys((*DEFAULT_EXCLUDES, *self.config_excludes, *excludes))
        )
        self._exclude_patterns = tuple(_normalize_pattern(pattern) for pattern in self.excludes)
        self._component_excludes = frozenset(
            pattern
            for pattern in self._exclude_patterns
            if pattern and "/" not in pattern and not any(character in pattern for character in "*?[")
        )
        self._prefix_excludes = tuple(
            pattern
            for pattern in self._exclude_patterns
            if pattern and "/" in pattern and not any(character in pattern for character in "*?[")
        )
        self._glob_excludes = tuple(
            pattern
            for pattern in self._exclude_patterns
            if any(character in pattern for character in "*?[")
        )
        self._small_file_cache: dict[str, tuple[int, int, int, bytes]] = {}
        self._entries = self._build_index()
        self._by_path = {entry.path: entry for entry in self._entries}

    def _read_config(self) -> _RepoConfig:
        config = self.root / ".repo-ready.toml"
        if not _safe_regular_file(config):
            return _RepoConfig()
        try:
            text = config.read_text(encoding="utf-8")
        except UnicodeError as error:
            raise ConfigError(".repo-ready.toml: configuration must be valid UTF-8.") from error
        except OSError as error:
            raise ConfigError(".repo-ready.toml: configuration could not be read.") from error
        try:
            document = tomllib.loads(text)
        except tomllib.TOMLDecodeError as error:
            line = getattr(error, "lineno", None)
            column = getattr(error, "colno", None)
            location = f" at line {line}, column {column}" if line is not None and column is not None else ""
            raise ConfigError(f".repo-ready.toml: invalid TOML{location}.") from error

        values = document.get("exclude")
        if values is not None:
            if not isinstance(values, list):
                raise ConfigError(
                    ".repo-ready.toml: 'exclude' must be a list of non-empty relative path/glob strings."
                )
            excludes = []
            for index, value in enumerate(values):
                if not isinstance(value, str) or not value.strip():
                    raise ConfigError(
                        f".repo-ready.toml: exclude[{index}] must be a non-empty string."
                    )
                path_error = _exclude_path_error(value)
                if path_error is not None:
                    raise ConfigError(
                        f".repo-ready.toml: exclude[{index}] {path_error}"
                    )
                excludes.append(value)
        else:
            excludes = []

        min_score = document.get("min_score")
        if min_score is not None and (
            isinstance(min_score, bool)
            or not isinstance(min_score, int)
            or not 0 <= min_score <= 100
        ):
            raise ConfigError(
                ".repo-ready.toml: 'min_score' must be an integer from 0 to 100; booleans are not allowed."
            )
        return _RepoConfig(tuple(excludes), min_score)

    def is_excluded(self, relative: str | Path) -> bool:
        path = _posix_path(relative)
        parts = set(path.split("/"))
        if parts & self._component_excludes:
            return True
        if any(path == pattern or path.startswith(pattern + "/") for pattern in self._prefix_excludes):
            return True
        return any(_pattern_matches_normalized(path, pattern) for pattern in self._glob_excludes)

    def _build_index(self) -> tuple[FileEntry, ...]:
        if not _safe_directory(self.root):
            return ()
        found: list[FileEntry] = []
        self._walk(self.root, "", found)
        found.sort(key=lambda entry: entry.path)
        return tuple(found)

    def _mark_truncated(self, reason: str) -> None:
        self._truncated = True
        self._truncation_reasons.add(reason)

    def _walk(self, directory: Path, prefix: str, found: list[FileEntry]) -> None:
        if self._truncated:
            return
        try:
            with os.scandir(directory) as handle:
                entries = sorted(handle, key=lambda item: item.name)
        except OSError:
            return
        for item in entries:
            if self._truncated:
                return
            relative = f"{prefix}/{item.name}" if prefix else item.name
            relative = _posix_path(relative)
            if self.is_excluded(relative):
                continue
            try:
                if item.is_symlink():
                    continue
                information = item.stat(follow_symlinks=False)
                if stat.S_ISDIR(information.st_mode):
                    self._walk(Path(item.path), relative, found)
                    continue
                if not stat.S_ISREG(information.st_mode):
                    continue
            except OSError:
                continue
            size = information.st_size
            if self.max_files is not None and self._observed_files >= self.max_files:
                self._mark_truncated("max_files")
                return
            if self.max_total_bytes is not None and self._observed_bytes + size > self.max_total_bytes:
                self._mark_truncated("max_total_bytes")
                return
            too_large = size > self.max_file_bytes
            binary = False
            if not too_large:
                try:
                    with Path(item.path).open("rb") as source:
                        sample = source.read(8192)
                        binary = b"\x00" in sample
                        if not binary and size <= 8192:
                            self._small_file_cache[relative] = (
                                size,
                                information.st_mtime_ns,
                                information.st_ctime_ns,
                                sample,
                            )
                except OSError:
                    continue
            found.append(FileEntry(relative, Path(item.path), size, too_large, binary))
            self._observed_files += 1
            self._observed_bytes += size

    @property
    def truncated(self) -> bool:
        """Whether an optional index budget stopped traversal early."""

        return self._truncated

    @property
    def truncation_reasons(self) -> tuple[str, ...]:
        return tuple(sorted(self._truncation_reasons))

    @property
    def observed_files(self) -> int:
        return self._observed_files

    @property
    def observed_bytes(self) -> int:
        return self._observed_bytes

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
        cached = self._small_file_cache.get(entry.path)
        if cached is not None:
            cached_size, cached_mtime_ns, cached_ctime_ns, cached_data = cached
            try:
                information = entry.absolute.stat(follow_symlinks=False)
                if (
                    stat.S_ISREG(information.st_mode)
                    and information.st_size == cached_size
                    and information.st_mtime_ns == cached_mtime_ns
                    and information.st_ctime_ns == cached_ctime_ns
                ):
                    return cached_data
            except (OSError, ValueError):
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
            if len(data) <= 8192 and b"\x00" not in data[:8192]:
                self._small_file_cache[entry.path] = (
                    information.st_size,
                    getattr(information, "st_mtime_ns", 0),
                    getattr(information, "st_ctime_ns", 0),
                    data,
                )
        except (OSError, ValueError):
            return None
        finally:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        if len(data) > self.max_file_bytes or b"\x00" in data[:8192]:
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


def _exclude_path_error(value: str) -> str | None:
    normalized = value.replace("\\", "/")
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(value)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or bool(windows.drive)
        or bool(windows.root)
    ):
        return "must be a relative path/glob; absolute paths are not allowed."
    if ".." in posix.parts:
        return "must not contain '..' path traversal."
    return None


__all__ = [
    "ConfigError",
    "DEFAULT_EXCLUDES",
    "MAX_FILE_BYTES",
    "FileEntry",
    "FileIndex",
    "RepoContext",
]
