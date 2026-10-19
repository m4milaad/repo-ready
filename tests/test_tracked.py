from __future__ import annotations

import struct
from pathlib import Path

from repo_ready.tracked import inspect_tracked


def _index(paths: tuple[str, ...], *, version: int = 2) -> bytes:
    entries = []
    for name in paths:
        raw = name.encode("utf-8")
        body = b"\0" * 40 + b"\0" * 20 + struct.pack(">H", len(raw)) + raw + b"\0"
        body += b"\0" * ((-len(body)) % 8)
        entries.append(body)
    return b"DIRC" + struct.pack(">II", version, len(entries)) + b"".join(entries) + b"\0" * 20


def test_reads_git_index_without_git_process(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "index").write_bytes(_index(("README.md", "src/repo_ready/__init__.py")))

    result = inspect_tracked(tmp_path)

    assert result.safe is True
    assert result.index_version == 2
    assert [item.path for item in result.files] == ["README.md", "src/repo_ready/__init__.py"]


def test_supports_worktree_git_file(tmp_path: Path) -> None:
    actual_git = tmp_path / "gitdir"
    actual_git.mkdir()
    (actual_git / "index").write_bytes(_index(("README.md",)))
    (tmp_path / ".git").write_text(f"gitdir: {actual_git}\n", encoding="utf-8")

    result = inspect_tracked(tmp_path)

    assert result.safe is True
    assert [item.path for item in result.files] == ["README.md"]


def test_missing_git_index_is_explicit(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()

    result = inspect_tracked(tmp_path)

    assert result.safe is False
    assert any(item["code"] == "TR008" for item in result.findings)
