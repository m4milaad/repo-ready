from __future__ import annotations

import builtins
import io
import os
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from repo_ready.context import FileEntry, RepoContext
from repo_ready.contracts import build_contract_graph
from repo_ready.detector import detect_ecosystems
from repo_ready.overlay import OverlayContext, SnapshotContext, snapshot_context
from repo_ready.scoring import report


def _repository(root: Path, files: dict[str, str | bytes]) -> Path:
    repository = root / "repository"
    repository.mkdir()
    for relative, contents in files.items():
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents.encode("utf-8") if isinstance(contents, str) else contents)
    return repository


def _codes(context: RepoContext) -> set[str]:
    return {diagnostic.code for diagnostic in build_contract_graph(context).diagnostics}


def test_score_and_contract_graph_respond_to_virtual_changes(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        {
            "README.md": "# Example\n\n## Test\n\n```sh\npython -m pytest -q\n```\n",
            "Makefile": "test:\n\tnpm test\n",
            "pyproject.toml": "[project]\nname='example'\nversion='1.0'\nrequires-python='>=3.12'\n",
            ".python-version": "3.12\n",
            "Dockerfile": 'FROM python:3.11-slim\nCMD ["python", "-m", "pytest"]\n',
        },
    )
    base = RepoContext(repository)
    frozen = snapshot_context(base)
    before = report(repository, context=frozen)
    overlay = OverlayContext(
        frozen,
        {
            "README.md": "# Example\n\n## Setup\n\nInstall locally.\n\n## Run\n\nRun locally.\n\n"
            "## Test\n\n```sh\npython -m pytest -q\n```\n",
            "Makefile": "test:\n\tpython -m pytest -q\n",
            "Dockerfile": 'FROM python:3.12-slim\nCMD ["python", "-m", "pytest"]\n',
            "LICENSE": "MIT License\n" + "Permission is hereby granted to use this software.\n" * 4,
        },
    )

    after = report(repository, context=overlay)

    assert isinstance(overlay, RepoContext)
    assert after["score"] > before["score"]
    assert next(check for check in after["checks"] if check["id"] == "RR02")["status"] == "pass"
    assert {"CG001", "CG012"} <= _codes(frozen)
    assert not {"CG001", "CG012"} & _codes(overlay)
    assert any(item.value == "python -m pytest -q" for item in build_contract_graph(overlay).evidence)
    assert report(repository, context=frozen) == before
    assert report(repository, context=base) == before
    assert not (repository / "LICENSE").exists()


def test_ecosystem_detection_observes_additions_and_removals(tmp_path: Path) -> None:
    repository = _repository(tmp_path, {"pyproject.toml": "[project]\nname='example'\nversion='1'\n"})
    base = RepoContext(repository)
    overlay = OverlayContext(
        base,
        {"pyproject.toml": None, "package.json": '{"name":"example","version":"1.0.0"}\n'},
    )

    assert detect_ecosystems(base) == ["python"]
    assert detect_ecosystems(overlay) == ["node"]
    assert report(repository, context=overlay)["ecosystems"] == ["node"]
    assert detect_ecosystems(OverlayContext(overlay, {"package.json": None})) == []


def test_index_lookups_and_inherited_read_aliases(tmp_path: Path) -> None:
    repository = _repository(tmp_path, {"old.txt": "remove", "keep.txt": "original"})
    base = RepoContext(repository)
    overlay = OverlayContext(
        base,
        {"old.txt": None, "missing.txt": None, "z/new.txt": "café", "keep.txt": b"replacement", "empty": b""},
    )

    assert overlay.files is overlay.index is overlay.file_index
    assert tuple(overlay) == tuple(overlay.iter_files()) == overlay.files
    assert [entry.path for entry in overlay] == ["empty", "keep.txt", "z/new.txt"]
    assert overlay.entry("old.txt") is None
    assert not overlay.has_file("old.txt")
    assert overlay.read_bytes("missing.txt") is None
    assert overlay.read_text(Path("z") / "new.txt") == "café"
    entry = overlay.entry("z/new.txt")
    assert entry is not None
    assert entry.size == len("café".encode("utf-8"))
    assert entry.absolute == repository / "z" / "new.txt"
    assert overlay.entry(entry) is entry
    assert overlay.read_bytes(entry) == "café".encode("utf-8")
    assert overlay.read(entry) == overlay.read_file(entry) == "café"
    assert overlay.has_file("empty", readable=True)
    assert overlay.read_bytes("empty") == b""
    assert overlay.observed_files == 3
    assert overlay.observed_bytes == sum(item.size for item in overlay)
    assert base.read_text("keep.txt") == "original"
    assert base.read_text("old.txt") == "remove"
    assert not base.has_file("z/new.txt")


def test_removed_stale_forged_and_foreign_entries_cannot_bypass_reads(tmp_path: Path) -> None:
    repository = _repository(tmp_path, {"keep.txt": "original", "old.txt": "remove"})
    base = RepoContext(repository)
    frozen = snapshot_context(base)
    first = OverlayContext(frozen, {"keep.txt": "first"})
    overlay = OverlayContext(first, {"old.txt": None, "keep.txt": "second"})
    sibling = OverlayContext(frozen, {"keep.txt": "sibling"})
    own = overlay.entry("keep.txt")
    assert own is not None
    foreign_root = tmp_path / "foreign"
    foreign_root.mkdir()
    foreign = RepoContext(_repository(foreign_root, {"keep.txt": "foreign"}))
    # Equal metadata is insufficient: entries are capabilities owned by a view.
    rejected = (
        base.entry("keep.txt"),
        frozen.entry("keep.txt"),
        first.entry("keep.txt"),
        first.entry("old.txt"),
        sibling.entry("keep.txt"),
        foreign.entry("keep.txt"),
        replace(own),
        FileEntry("keep.txt", tmp_path / "outside-secret", 6),
        FileEntry("old.txt", repository / "old.txt", 6),
    )
    for entry in rejected:
        assert entry is not None
        assert overlay.entry(entry) is None
        assert overlay.read_bytes(entry) is None
        assert overlay.read_text(entry) is None
        assert overlay.read(entry) is None
        assert overlay.read_file(entry) is None
    assert overlay.read_text(own) == "second"
    assert frozen.entry(base.entry("keep.txt")) is None
    assert frozen.read_bytes(replace(frozen.entry("keep.txt"))) is None


def test_repo_context_rejects_foreign_and_forged_entries(tmp_path: Path) -> None:
    repository = _repository(tmp_path, {"keep.txt": "inside"})
    outside_root = tmp_path / "outside-root"
    outside_root.mkdir()
    foreign = RepoContext(_repository(outside_root, {"keep.txt": "outside"}))
    base = RepoContext(repository)
    foreign_entry = foreign.entry("keep.txt")
    forged_entry = FileEntry("keep.txt", outside_root / "secret.txt", 6)

    assert foreign_entry is not None
    for entry in (foreign_entry, forged_entry, replace(base.entry("keep.txt"))):
        assert base.entry(entry) is None
        assert not base.has_file(entry)
        assert base.read_bytes(entry) is None
        assert base.read_text(entry) is None

    assert base.read_text("keep.txt") == "inside"


def test_virtual_and_snapshot_binary_size_and_utf8_guards(tmp_path: Path) -> None:
    repository = _repository(tmp_path, {"binary": b"a\x00b", "large": b"x" * 9, "valid": "ok"})
    base = RepoContext(repository, max_file_bytes=8)
    frozen = snapshot_context(base)
    overlay = OverlayContext(
        frozen,
        {
            "virtual-binary": b"a\x00b",
            "virtual-large": "é" * 5,
            "invalid-utf8": b"\xff",
            "boundary": b"x" * 8,
            "large": "small",
        },
    )

    for name in ("binary", "large"):
        assert frozen.entry(name) == base.entry(name)
        assert frozen.has_file(name)
        assert not frozen.has_file(name, readable=True)
        assert frozen.read_bytes(name) is None
    for name in ("binary", "virtual-binary", "virtual-large"):
        assert overlay.has_file(name)
        assert not overlay.has_file(name, readable=True)
        assert overlay.read_bytes(name) is None
        assert overlay.read_text(overlay.entry(name)) is None
    assert overlay.entry("virtual-binary").binary
    assert overlay.entry("virtual-large").too_large
    assert overlay.entry("virtual-large").size == 10
    assert overlay.read_bytes("boundary") == b"x" * 8
    assert overlay.read_text("large") == "small"
    assert overlay.read_text("invalid-utf8") == "\ufffd"
    assert overlay.read("invalid-utf8", errors="strict") is None
    assert overlay.read_file("invalid-utf8", errors="strict") is None
    assert overlay.read_text("invalid-utf8", errors="unknown-handler") is None
    assert frozen.limitations == ()


def test_binary_probe_matches_repo_context_boundary(tmp_path: Path) -> None:
    data = b"x" * 8192 + b"\x00"
    repository = _repository(tmp_path, {"late-null": data})
    base = RepoContext(repository)
    frozen = snapshot_context(base)
    overlay = OverlayContext(frozen, {"virtual": data})

    assert base.read_bytes("late-null") == frozen.read_bytes("late-null") == data
    assert overlay.read_bytes("virtual") == data


@pytest.mark.parametrize(
    "path",
    (
        "", ".", "..", "../file", "file/", "./file", "dir//file", "dir/./file", "dir/../file",
        "/absolute", "//host/share/file", "C:/file", "C:file", "C:", "C:\\file", "\\root",
        "\\\\host\\share\\file", "dir\\file", "file\x00tail", "dir/..", "dir/",
        "dir/C:/file", "dir/C:file",
    ),
)
def test_rejects_unsafe_or_non_normalized_change_keys(path: str, tmp_path: Path) -> None:
    base = RepoContext(tmp_path)
    with pytest.raises(ValueError, match="relative POSIX"):
        OverlayContext(base, {path: "content"})


@pytest.mark.parametrize("path", (7, None, Path("relative")))
def test_change_keys_must_be_strings(path: object, tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="paths must be strings"):
        OverlayContext(RepoContext(tmp_path), {path: "content"})


@pytest.mark.parametrize("value", (1, True, bytearray(b"data"), memoryview(b"data")))
def test_change_values_are_not_silently_coerced(value: object, tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="str, bytes, or None"):
        OverlayContext(RepoContext(tmp_path), {"file": value})


def test_invalid_arguments_are_rejected_before_reading_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = RepoContext(_repository(tmp_path, {"file": "must not be read"}))

    def blocked(*_args, **_kwargs):
        raise AssertionError("invalid changes must be rejected before capture")

    monkeypatch.setattr(base, "read_bytes", blocked)
    with pytest.raises(TypeError, match="mapping"):
        OverlayContext(base, [])
    with pytest.raises(ValueError):
        OverlayContext(base, {"../file": "content"})
    with pytest.raises(TypeError, match="RepoContext"):
        OverlayContext(tmp_path, {})
    with pytest.raises(TypeError, match="RepoContext"):
        snapshot_context(tmp_path)


@pytest.mark.parametrize("value", (None, "min_score=100\n", b"min_score=100\n"))
@pytest.mark.parametrize("path", (".repo-ready.toml", ".REPO-READY.TOML"))
def test_root_configuration_changes_are_explicitly_rejected(value: str | bytes | None, path: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="configuration is frozen"):
        OverlayContext(RepoContext(tmp_path), {path: value})


def test_safe_paths_are_not_normalized_into_other_files_on_read(tmp_path: Path) -> None:
    overlay = OverlayContext(RepoContext(tmp_path), {"readme": "safe", "dir/file": "nested"})
    for path in ("/readme", "./readme", "dir/../readme", "dir//file", "dir\\file", "C:readme", "readme\x00"):
        assert overlay.entry(path) is None
        assert not overlay.has_file(path)
        assert overlay.read_bytes(path) is None
    assert overlay.read_text(Path("dir") / "file") == "nested"


def test_excludes_configuration_and_budget_metadata_are_preserved(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        {".repo-ready.toml": 'exclude=["ignored", "*.tmp"]\nmin_score=73\n', "keep": "base"},
    )
    base = RepoContext(repository, excludes=("custom",), max_file_bytes=256, max_files=20, max_total_bytes=4096)
    frozen = snapshot_context(base)
    overlay = OverlayContext(
        frozen,
        {
            ".git/new": "ignored", ".venv/new": "ignored", "node_modules/new": "ignored",
            "ignored/file": "ignored", "nested/ignored/file": "ignored", "a.tmp": "ignored",
            "custom/file": "ignored", "visible/new": "added", "keep": "changed",
        },
    )

    for view in (frozen, overlay):
        for attribute in ("root", "min_score", "config_excludes", "excludes", "max_file_bytes", "max_files", "max_total_bytes", "include_paths"):
            assert getattr(view, attribute) == getattr(base, attribute)
        assert view.truncated == base.truncated
        assert view.truncation_reasons == base.truncation_reasons
    assert [entry.path for entry in overlay] == [".repo-ready.toml", "keep", "visible/new"]
    assert overlay.is_excluded("ignored/file")
    assert overlay.read_text("visible/new") == "added"


def test_explicit_include_scope_is_preserved(tmp_path: Path) -> None:
    repository = _repository(tmp_path, {"included": "base", "outside": "not indexed"})
    base = RepoContext(repository, include_paths=("included", "future"))
    overlay = OverlayContext(base, {"included": "replacement", "outside": "ignored", "future": "added"})

    assert overlay.include_paths == base.include_paths
    assert [entry.path for entry in overlay] == ["future", "included"]
    assert overlay.read_text("future") == "added"


def test_repeated_overlays_are_immutable_and_leave_the_base_unchanged(tmp_path: Path) -> None:
    repository = _repository(tmp_path, {"file": "base"})
    base = RepoContext(repository)
    frozen = snapshot_context(base)
    changes = {"file": "first", "new": "one"}
    first = OverlayContext(frozen, changes)
    changes["file"] = "mutated input"
    changes.clear()
    second = OverlayContext(frozen, {"file": "second"})
    nested = OverlayContext(first, {"file": None, "new": "nested"})

    assert first.read_text("file") == "first"
    assert first.read_text("new") == "one"
    assert second.read_text("file") == "second"
    assert not second.has_file("new")
    assert not nested.has_file("file")
    assert nested.read_text("new") == "nested"
    assert base.read_text("file") == frozen.read_text("file") == "base"
    assert [entry.path for entry in base] == ["file"]
    for view in (frozen, first, second, nested):
        with pytest.raises(AttributeError, match="immutable"):
            view.root = tmp_path
        with pytest.raises(AttributeError, match="immutable"):
            del view.excludes
        with pytest.raises(AttributeError):
            view.files[0].binary = True


def test_snapshots_and_ordinary_overlays_do_not_reread_mutated_source(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        {
            ".repo-ready.toml": "min_score=41\n",
            "README.md": "# Original\n\n## Test\n\npython -m pytest -q\n",
            "Makefile": "test:\n\tpython -m pytest -q\n",
            "deleted": "still frozen",
        },
    )
    base = RepoContext(repository)
    frozen = snapshot_context(base)
    direct = OverlayContext(base, {})
    before = report(repository, context=frozen)
    graph_before = build_contract_graph(frozen)
    (repository / "README.md").write_text("changed source", encoding="utf-8")
    (repository / "Makefile").write_text("test:\n\tnpm test\n", encoding="utf-8")
    (repository / "deleted").unlink()
    (repository / "added-later").write_text("not captured", encoding="utf-8")
    (repository / ".repo-ready.toml").write_text("min_score=99\n", encoding="utf-8")

    for view in (frozen, direct, OverlayContext(frozen, {}), OverlayContext(direct, {})):
        assert view.read_text("README.md").startswith("# Original")
        assert view.read_text("deleted") == "still frozen"
        assert view.read_text(".repo-ready.toml") == "min_score=41\n"
        assert view.min_score == 41
        assert not view.has_file("added-later")
        assert report(repository, context=view) == before
        assert build_contract_graph(view) == graph_before
    assert base.read_text("README.md") == "changed source"


@pytest.mark.parametrize("raises", (False, True))
def test_snapshot_preserves_unreadable_metadata_without_lazy_retry(
    raises: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path, {"unreadable": "contents", "binary": b"\x00", "large": b"x" * 17, "ok": "ok"})
    base = RepoContext(repository, max_file_bytes=16)
    original_read = base.read_bytes
    calls: list[str] = []

    def capture(entry: FileEntry) -> bytes | None:
        assert base.entry(entry.path) is entry
        calls.append(entry.path)
        if entry.path == "unreadable":
            if raises:
                raise PermissionError("unreadable")
            return None
        return original_read(entry)

    monkeypatch.setattr(base, "read_bytes", capture)
    frozen = snapshot_context(base)
    assert isinstance(frozen, SnapshotContext)
    assert calls == ["ok", "unreadable"]
    assert frozen.files == base.files
    assert frozen.limitations == ("unreadable:unreadable",)
    overlay = OverlayContext(frozen, {"ok": "virtual"})
    for _ in range(3):
        assert frozen.read_text("ok") == "ok"
        assert frozen.read_text("unreadable") is None
        assert frozen.has_file("unreadable")
        assert not frozen.has_file("unreadable", readable=True)
        assert overlay.read_text("unreadable") is None
    assert calls == ["ok", "unreadable"]
    assert overlay.limitations == frozen.limitations


def test_snapshot_budget_is_bounded_deterministic_and_marked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = _repository(tmp_path, {"a": "aaaa", "b": "bbb", "c": "", "d": "d"})
    base = RepoContext(repository)
    original_read = base.read_bytes
    calls: list[str] = []

    def capture(entry: FileEntry) -> bytes | None:
        calls.append(entry.path)
        return original_read(entry)

    monkeypatch.setattr(base, "read_bytes", capture)
    frozen = snapshot_context(base, max_snapshot_bytes=5)
    assert frozen.max_snapshot_bytes == 5
    assert frozen.limitations == ("max_snapshot_bytes",)
    assert frozen.files == base.files
    assert frozen.entry("b").readable  # Original metadata is intentionally retained.
    assert frozen.has_file("b")
    assert not frozen.has_file("b", readable=True)
    assert frozen.read_bytes("b") is None
    assert frozen.read_bytes("c") == b""
    assert sum(len(frozen.read_bytes(entry) or b"") for entry in frozen) == 5
    assert calls == ["a", "c", "d"]
    overlay = OverlayContext(frozen, {"b": "virtual"})
    assert overlay.max_snapshot_bytes == frozen.max_snapshot_bytes
    assert overlay.read_text("b") == "virtual"
    assert overlay.limitations == frozen.limitations
    assert calls == ["a", "c", "d"]


def test_zero_and_exact_snapshot_budgets(tmp_path: Path) -> None:
    base = RepoContext(_repository(tmp_path, {"empty": b"", "file": "ab"}))
    zero = SnapshotContext(base, max_snapshot_bytes=0)
    assert zero.read_bytes("empty") == b""
    assert zero.read_bytes("file") is None
    assert zero.limitations == ("max_snapshot_bytes",)
    exact = SnapshotContext(base, max_snapshot_bytes=2)
    assert exact.read_bytes("file") == b"ab"
    assert exact.limitations == ()


def test_source_growth_cannot_overfill_snapshot_budget(tmp_path: Path) -> None:
    repository = _repository(tmp_path, {"a": "a", "b": "b"})
    base = RepoContext(repository)
    (repository / "a").write_text("grown", encoding="utf-8")

    frozen = snapshot_context(base, max_snapshot_bytes=2)

    assert frozen.entry("a").size == 1
    assert frozen.read_bytes("a") is None
    assert frozen.read_bytes("b") == b"b"
    assert frozen.limitations == ("max_snapshot_bytes",)


@pytest.mark.parametrize("budget", (-1, True, 1.5, "10", None))
def test_snapshot_budget_validation(budget: object, tmp_path: Path) -> None:
    error = ValueError if budget == -1 else TypeError
    with pytest.raises(error, match="max_snapshot_bytes"):
        snapshot_context(RepoContext(tmp_path), max_snapshot_bytes=budget)


@pytest.mark.parametrize("budgets,reason", (({"max_files": 1}, "max_files"), ({"max_total_bytes": 1}, "max_total_bytes")))
def test_original_index_budget_limitations_are_retained(budgets: dict[str, int], reason: str, tmp_path: Path) -> None:
    base = RepoContext(_repository(tmp_path, {"a": "a", "b": "b"}), **budgets)
    frozen = snapshot_context(base)
    overlay = OverlayContext(frozen, {"new": "explicit virtual addition"})

    for view in (frozen, overlay):
        assert view.truncated
        assert view.truncation_reasons == (reason,)
        assert view.limitations == (reason,)
        assert view.max_files == base.max_files
        assert view.max_total_bytes == base.max_total_bytes
    assert overlay.read_text("new") == "explicit virtual addition"


def test_snapshot_and_scenarios_never_write_copy_or_use_temp_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository = _repository(tmp_path, {"README.md": "# Example\n", "Makefile": "test:\n\tpython -m pytest -q\n"})
    base = RepoContext(repository)
    before = {entry.path: entry.absolute.read_bytes() for entry in base}
    original_read = base.read_bytes
    source_reads: list[str] = []

    def capture(entry: FileEntry) -> bytes | None:
        assert base.entry(entry.path) is entry
        source_reads.append(entry.path)
        return original_read(entry)

    def blocked(*_args, **_kwargs):
        raise AssertionError("filesystem writes, copies, temporary paths, and scenario IO are forbidden")

    real_os_open = os.open
    real_open = builtins.open
    real_io_open = io.open

    def readonly_os_open(path, flags, *args, **kwargs):
        assert not flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
        return real_os_open(path, flags, *args, **kwargs)

    def readonly_open(path, mode="r", *args, **kwargs):
        assert not any(flag in mode for flag in "wax+")
        return real_open(path, mode, *args, **kwargs)

    def readonly_io_open(path, mode="r", *args, **kwargs):
        assert not any(flag in mode for flag in "wax+")
        return real_io_open(path, mode, *args, **kwargs)

    with monkeypatch.context() as guard:
        guard.setattr(base, "read_bytes", capture)
        guard.setattr(os, "open", readonly_os_open)
        guard.setattr(builtins, "open", readonly_open)
        guard.setattr(io, "open", readonly_io_open)
        for name in ("write_text", "write_bytes", "mkdir", "touch", "unlink", "rename", "replace"):
            guard.setattr(Path, name, blocked)
        for name in ("TemporaryDirectory", "NamedTemporaryFile", "TemporaryFile", "mkdtemp", "mkstemp"):
            guard.setattr(tempfile, name, blocked)
        for name in ("copy", "copy2", "copyfile", "copytree"):
            guard.setattr(shutil, name, blocked)
        frozen = snapshot_context(base)
        assert source_reads == ["Makefile", "README.md"]

        # From here even reads/stats/walks are forbidden, including source reads.
        guard.setattr(base, "read_bytes", blocked)
        guard.setattr(builtins, "open", blocked)
        guard.setattr(io, "open", blocked)
        guard.setattr(os, "open", blocked)
        guard.setattr(os, "scandir", blocked)
        for name in ("open", "stat", "resolve"):
            guard.setattr(Path, name, blocked)
        recaptured = snapshot_context(frozen)
        assert recaptured.read_text("README.md") == "# Example\n"
        for index in range(3):
            overlay = OverlayContext(frozen, {"LICENSE": "virtual license " * 12, "new/file": str(index)})
            assert overlay.read_text("new/file") == str(index)
            assert report(repository, context=overlay)["score"] > 0
            assert build_contract_graph(overlay).version == 1
            assert detect_ecosystems(overlay) == []
            assert OverlayContext(overlay, {"new/file": None}).read_text("new/file") is None

    assert {path.relative_to(repository).as_posix(): path.read_bytes() for path in repository.rglob("*") if path.is_file()} == before


def test_virtual_paths_do_not_follow_filesystem_symlinks(tmp_path: Path) -> None:
    repository = _repository(tmp_path, {"safe": "base"})
    outside = tmp_path / "outside-secret"
    outside.write_text("secret", encoding="utf-8")
    link = repository / "link"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("creating symlinks is unavailable on this platform")
    base = RepoContext(repository)
    assert base.entry("link") is None

    overlay = OverlayContext(base, {"link": "virtual"})

    assert overlay.read_text("link") == "virtual"
    assert overlay.read_text(overlay.entry("link")) == "virtual"
    assert outside.read_text(encoding="utf-8") == "secret"
    assert link.is_symlink()
