from __future__ import annotations

from pathlib import Path

import pytest

from scripts.release_check import ReleaseCheckError, validate_release


ROOT = Path(__file__).resolve().parents[1]


def test_release_metadata_is_consistent() -> None:
    messages = validate_release(ROOT, "v0.2.0")

    assert messages[0] == "version: 0.2.0"
    assert "pyproject: dynamic version configured" in messages


def test_release_metadata_rejects_wrong_expected_version() -> None:
    with pytest.raises(ReleaseCheckError, match="expected version"):
        validate_release(ROOT, "v9.9.9")


def test_release_metadata_rejects_missing_changelog_release(tmp_path: Path) -> None:
    for source in ("pyproject.toml", "package.json", ".nvmrc"):
        target = tmp_path / source
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / source).read_bytes())
    version_source = tmp_path / "src" / "repo_ready" / "_version.py"
    version_source.parent.mkdir(parents=True)
    version_source.write_bytes((ROOT / "src" / "repo_ready" / "_version.py").read_bytes())
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n", encoding="utf-8")

    with pytest.raises(ReleaseCheckError, match="CHANGELOG"):
        validate_release(tmp_path)
