import json
import shutil
from pathlib import Path

import pytest

from repo_ready.cli import main
from repo_ready.detector import detect_ecosystems
from repo_ready.scoring import CHECK_WEIGHTS, pinned_runtime


FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_NAMES = (
    "empty-incomplete",
    "node-complete",
    "python-complete",
    "python-minimal",
)


def _expected(fixture_name: str) -> dict[str, object]:
    expectation = FIXTURES / fixture_name / "expected.json"
    return json.loads(expectation.read_text(encoding="utf-8"))


def _json_stdout(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    captured = capsys.readouterr()
    assert captured.err == ""
    return json.loads(captured.out)


def _snapshot(repository: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(repository): path.read_bytes()
        for path in repository.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("fixture_name", FIXTURE_NAMES)
def test_check_json_matches_each_fixture_contract(
    fixture_name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    repository = FIXTURES / fixture_name

    assert main(["check", str(repository), "--json"]) == 0

    assert _json_stdout(capsys) == _expected(fixture_name)


@pytest.mark.parametrize(
    ("fixture_name", "ecosystem"),
    (("python-complete", "python"), ("node-complete", "node")),
)
def test_check_json_detects_supported_ecosystem(
    fixture_name: str,
    ecosystem: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = FIXTURES / fixture_name

    assert main(["check", str(repository), "--json"]) == 0

    report = _json_stdout(capsys)
    assert report["ecosystems"] == [ecosystem]


def test_check_json_rejects_a_runtime_created_absolute_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    absolute_path = (tmp_path / "runtime-created-target").resolve()
    absolute_path.write_text("fixture data\n", encoding="utf-8")
    (repository / "settings.env").write_text(
        f"DATA_DIR={absolute_path}\n", encoding="utf-8"
    )

    assert absolute_path.is_absolute()
    assert main(["check", str(repository), "--json"]) == 0

    report = _json_stdout(capsys)
    checks = report["checks"]
    assert checks["no_hardcoded_paths"] is False


@pytest.mark.parametrize(
    "marker",
    ("pyproject.toml", "requirements.txt", "uv.lock", "poetry.lock"),
)
def test_each_python_marker_detects_python(tmp_path: Path, marker: str) -> None:
    (tmp_path / marker).write_text("marker\n", encoding="utf-8")

    assert detect_ecosystems(tmp_path) == ["python"]


@pytest.mark.parametrize("marker", ("package.json", "package-lock.json", ".nvmrc"))
def test_each_node_marker_detects_node(tmp_path: Path, marker: str) -> None:
    (tmp_path / marker).write_text("marker\n", encoding="utf-8")

    assert detect_ecosystems(tmp_path) == ["node"]


def test_mixed_ecosystems_keep_python_priority(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (tmp_path / "package.json").write_text("{}\n", encoding="utf-8")

    assert detect_ecosystems(tmp_path) == ["python", "node"]


def test_fixed_weights_sum_to_one_hundred() -> None:
    assert sum(CHECK_WEIGHTS.values()) == 100
    assert len(CHECK_WEIGHTS) == 10


@pytest.mark.parametrize("filename, value", ((".python-version", "latest\n"), (".nvmrc", ">=20\n")))
def test_runtime_aliases_are_not_pins(
    tmp_path: Path, filename: str, value: str
) -> None:
    (tmp_path / filename).write_text(value, encoding="utf-8")

    assert pinned_runtime(tmp_path) is False


def test_fix_dry_run_leaves_minimal_repository_unchanged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repository = tmp_path / "minimal-python"
    shutil.copytree(FIXTURES / "python-minimal", repository)
    before = _snapshot(repository)

    assert main(["fix", str(repository), "--dry-run"]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "Dry run:" in captured.out
    assert _snapshot(repository) == before


def test_fix_creates_missing_scaffolding_without_overwriting_existing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repository = tmp_path / "incomplete-python"
    repository.mkdir()
    (repository / "pyproject.toml").write_text(
        '[project]\nname = "incomplete-python"\nrequires-python = ">=3.11"\n',
        encoding="utf-8",
    )
    existing_license = repository / "LICENSE"
    existing_license.write_text("existing license content\n", encoding="utf-8")

    assert main(["fix", str(repository)]) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert "Created" in captured.out
    assert existing_license.read_text(encoding="utf-8") == "existing license content\n"

    created_files = {
        "README.md",
        "CONTRIBUTING.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/ISSUE_TEMPLATE/bug_report.md",
        "requirements.lock",
        ".python-version",
        "Dockerfile",
        "Makefile",
        ".env.example",
    }
    assert all((repository / relative).is_file() for relative in created_files)
