from __future__ import annotations

import json
from pathlib import Path

import pytest

from repo_ready.cli import main
from repo_ready.context import ConfigError, DEFAULT_EXCLUDES, RepoContext


def _config(repository: Path, text: str) -> None:
    (repository / ".repo-ready.toml").write_text(text, encoding="utf-8")


def test_valid_config_loads_custom_excludes_and_keeps_default_excludes(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "generated").mkdir()
    (repository / "generated" / "secret.py").write_text("secret\n", encoding="utf-8")
    (repository / "reports").mkdir()
    (repository / "reports" / "summary.json").write_text("{}\n", encoding="utf-8")
    (repository / "kept.txt").write_text("kept\n", encoding="utf-8")
    _config(repository, 'exclude = ["generated", "reports/*.json"]\n')

    context = RepoContext(repository)

    assert context.config_excludes == ("generated", "reports/*.json")
    assert set(DEFAULT_EXCLUDES).issubset(context.excludes)
    assert context.entry("generated/secret.py") is None
    assert context.entry("reports/summary.json") is None
    assert context.entry("kept.txt") is not None


def test_default_excludes_are_not_indexed(tmp_path: Path) -> None:
    for directory in DEFAULT_EXCLUDES:
        path = tmp_path / directory / "hidden.txt"
        path.parent.mkdir(parents=True)
        path.write_text("hidden\n", encoding="utf-8")
    (tmp_path / "visible.txt").write_text("visible\n", encoding="utf-8")

    paths = {entry.path for entry in RepoContext(tmp_path)}

    assert "visible.txt" in paths
    assert all(f"{directory}/hidden.txt" not in paths for directory in DEFAULT_EXCLUDES)


@pytest.mark.parametrize("minimum", (0, 100))
def test_min_score_boundary_values_are_accepted(minimum: int, tmp_path: Path) -> None:
    _config(tmp_path, f"min_score = {minimum}\n")

    assert RepoContext(tmp_path).min_score == minimum


def test_cli_min_score_takes_precedence_over_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _config(tmp_path, "min_score = 100\n")

    assert main(["check", str(tmp_path), "--json", "--min-score", "0"]) == 0
    first = capsys.readouterr()
    assert first.err == ""
    json.loads(first.out)

    _config(tmp_path, "min_score = 0\n")

    assert main(["check", str(tmp_path), "--json", "--min-score", "100"]) == 1
    second = capsys.readouterr()
    assert second.err == ""
    json.loads(second.out)


@pytest.mark.parametrize(
    ("text", "message"),
    (
        (
            'exclude = "generated"\n',
            ".repo-ready.toml: 'exclude' must be a list of non-empty relative path/glob strings.",
        ),
        (
            "exclude = [1]\n",
            ".repo-ready.toml: exclude[0] must be a non-empty string.",
        ),
        (
            "exclude = [true]\n",
            ".repo-ready.toml: exclude[0] must be a non-empty string.",
        ),
        (
            'exclude = [""]\n',
            ".repo-ready.toml: exclude[0] must be a non-empty string.",
        ),
        (
            'exclude = ["   "]\n',
            ".repo-ready.toml: exclude[0] must be a non-empty string.",
        ),
    ),
)
def test_invalid_exclude_types_and_items_have_deterministic_errors(
    text: str,
    message: str,
    tmp_path: Path,
) -> None:
    _config(tmp_path, text)

    with pytest.raises(ConfigError) as error:
        RepoContext(tmp_path)

    assert str(error.value) == message


ABSOLUTE_PATTERNS = (
    "/" + "tmp/cache",
    "C" + ":" + "\\" + "Users" + "\\" + "runner" + "\\" + "cache",
    "\\" + "\\" + "server" + "\\" + "share" + "\\" + "cache",
    "\\" + "cache",
)


@pytest.mark.parametrize("pattern", ABSOLUTE_PATTERNS)
def test_absolute_posix_and_windows_exclude_paths_are_rejected(pattern: str, tmp_path: Path) -> None:
    _config(tmp_path, f"exclude = [{json.dumps(pattern)}]\n")

    with pytest.raises(ConfigError) as error:
        RepoContext(tmp_path)

    assert str(error.value) == (
        ".repo-ready.toml: exclude[0] must be a relative path/glob; absolute paths are not allowed."
    )


@pytest.mark.parametrize("pattern", ("../outside", "nested/../outside", r"..\outside"))
def test_exclude_path_traversal_is_rejected(pattern: str, tmp_path: Path) -> None:
    _config(tmp_path, f"exclude = [{json.dumps(pattern)}]\n")

    with pytest.raises(ConfigError) as error:
        RepoContext(tmp_path)

    assert str(error.value) == ".repo-ready.toml: exclude[0] must not contain '..' path traversal."


@pytest.mark.parametrize("value", ("true", "-1", "101", "80.5", '"80"'))
def test_invalid_min_score_values_have_deterministic_errors(value: str, tmp_path: Path) -> None:
    _config(tmp_path, f"min_score = {value}\n")

    with pytest.raises(ConfigError) as error:
        RepoContext(tmp_path)

    assert str(error.value) == (
        ".repo-ready.toml: 'min_score' must be an integer from 0 to 100; booleans are not allowed."
    )


def test_malformed_toml_has_a_deterministic_config_error(tmp_path: Path) -> None:
    _config(tmp_path, "exclude = [\n")

    messages = []
    for _ in range(2):
        with pytest.raises(ConfigError) as error:
            RepoContext(tmp_path)
        messages.append(str(error.value))

    assert messages == [".repo-ready.toml: invalid TOML."] * 2


def test_cli_config_error_returns_two_on_stderr_without_traceback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _config(tmp_path, "exclude = [\n")

    assert main(["check", str(tmp_path), "--json"]) == 2
    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "repo-ready: configuration error: .repo-ready.toml: invalid TOML.\n"
    assert "Traceback" not in captured.err
