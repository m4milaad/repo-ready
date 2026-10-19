from __future__ import annotations

from pathlib import Path

from repo_ready.context import RepoContext


def test_max_file_budget_stops_deterministically(tmp_path: Path) -> None:
    for name in ("a.txt", "b.txt", "c.txt"):
        (tmp_path / name).write_text(name, encoding="utf-8")

    context = RepoContext(tmp_path, max_files=2)

    assert [entry.path for entry in context] == ["a.txt", "b.txt"]
    assert context.truncated is True
    assert context.truncation_reasons == ("max_files",)
    assert context.observed_files == 2


def test_total_byte_budget_stops_before_exceeding_limit(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(b"1234")
    (tmp_path / "b.txt").write_bytes(b"5678")

    context = RepoContext(tmp_path, max_total_bytes=5)

    assert [entry.path for entry in context] == ["a.txt"]
    assert context.truncated is True
    assert context.truncation_reasons == ("max_total_bytes",)
    assert context.observed_bytes == 4


def test_unbounded_context_preserves_existing_behavior(tmp_path: Path) -> None:
    (tmp_path / "value.txt").write_text("value", encoding="utf-8")

    context = RepoContext(tmp_path)

    assert context.truncated is False
    assert context.truncation_reasons == ()
    assert context.observed_files == 1
