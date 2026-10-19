from __future__ import annotations

from pathlib import Path

from repo_ready.cli import main


def test_fix_diff_is_reviewable_and_does_not_write(tmp_path: Path, capsys) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".keep").write_text("", encoding="utf-8")

    assert main(["fix", str(repository), "--dry-run", "--format", "diff"]) == 0
    output = capsys.readouterr().out

    assert "+++ README.md" in output
    assert "+# Repository" in output
    assert not (repository / "README.md").exists()
