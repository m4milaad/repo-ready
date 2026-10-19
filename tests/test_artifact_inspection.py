from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from repo_ready.artifact import inspect_artifact


def _wheel(path: Path, files: dict[str, str]) -> None:
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value)


def _metadata(version: str = "0.1.0") -> str:
    return f"Metadata-Version: 2.2\nName: repo-ready\nVersion: {version}\n"


def test_valid_wheel_is_safe(tmp_path: Path) -> None:
    path = tmp_path / "repo_ready-0.1.0-py3-none-any.whl"
    _wheel(
        path,
        {
            "repo_ready/__init__.py": "__version__ = '0.1.0'\n",
            "repo_ready/cli.py": "def main(): pass\n",
            "repo_ready-0.1.0.dist-info/METADATA": _metadata(),
            "repo_ready-0.1.0.dist-info/WHEEL": "Wheel-Version: 1.0\n",
            "repo_ready-0.1.0.dist-info/RECORD": "",
        },
    )

    result = inspect_artifact(path)

    assert result.safe is True
    assert result.format == "wheel"
    assert result.findings == ()


def test_project_comparison_detects_missing_source_module(tmp_path: Path) -> None:
    project = tmp_path / "project"
    (project / "src" / "repo_ready").mkdir(parents=True)
    (project / "src" / "repo_ready" / "__init__.py").write_text("", encoding="utf-8")
    (project / "src" / "repo_ready" / "models.py").write_text("", encoding="utf-8")
    (project / "pyproject.toml").write_text(
        "[project]\nname='repo-ready'\nversion='0.1.0'\n",
        encoding="utf-8",
    )
    path = tmp_path / "repo_ready-0.1.0-py3-none-any.whl"
    _wheel(
        path,
        {
            "repo_ready/__init__.py": "",
            "repo_ready/cli.py": "",
            "repo_ready-0.1.0.dist-info/METADATA": _metadata(),
            "repo_ready-0.1.0.dist-info/WHEEL": "",
            "repo_ready-0.1.0.dist-info/RECORD": "",
        },
    )

    result = inspect_artifact(path, project=project)

    assert result.safe is False
    assert any(finding.code == "AR012" and finding.member == "repo_ready/models.py" for finding in result.findings)


def test_unsafe_archive_member_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "unsafe.whl"
    _wheel(
        path,
        {
            "../escape.py": "not extracted",
            "repo_ready-0.1.0.dist-info/METADATA": _metadata(),
            "repo_ready-0.1.0.dist-info/WHEEL": "",
            "repo_ready-0.1.0.dist-info/RECORD": "",
        },
    )

    result = inspect_artifact(path)

    assert result.safe is False
    assert any(finding.code == "AR003" and finding.member == "../escape.py" for finding in result.findings)


def test_version_mismatch_is_reported(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        "[project]\nname='repo-ready'\nversion='0.2.0'\n",
        encoding="utf-8",
    )
    path = tmp_path / "repo_ready-0.1.0-py3-none-any.whl"
    _wheel(
        path,
        {
            "repo_ready/__init__.py": "",
            "repo_ready/cli.py": "",
            "repo_ready-0.1.0.dist-info/METADATA": _metadata("0.1.0"),
            "repo_ready-0.1.0.dist-info/WHEEL": "",
            "repo_ready-0.1.0.dist-info/RECORD": "",
        },
    )

    result = inspect_artifact(path, project=project)

    assert any(finding.code == "AR010" for finding in result.findings)
