from __future__ import annotations

import copy
import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from repo_ready import __version__
from repo_ready.checks import CHECK_MESSAGES, CHECK_WEIGHTS
from repo_ready.context import RepoContext
from repo_ready.detector import detect_ecosystems
from repo_ready.fixer import plan_fixes
from repo_ready.scoring import report
from repo_ready.cli import main


FIXTURES = Path(__file__).parent / "fixtures"
CASES = (
    "perfect-python",
    "perfect-node",
    "empty-repo",
    "python-bad",
    "node-bad",
    "polyglot-mixed",
    "edge-readme-variants",
    "edge-binary-and-large",
    "edge-symlinks",
    "edge-env-usage",
    "edge-docker",
    "edge-non-utf8",
    "config-excludes",
)
RULE_IDS = tuple(f"RR{number:02d}" for number in range(1, 14))


def _fixture_repo(case: str) -> Path:
    return FIXTURES / case / "repo"


def _expected(case: str) -> dict[str, object]:
    return json.loads((FIXTURES / case / "expected.json").read_text(encoding="utf-8"))


def _copy_fixture(tmp_path: Path, case: str) -> Path:
    destination = tmp_path / case / "repo"
    shutil.copytree(_fixture_repo(case), destination, symlinks=True)
    return destination


def _normalise_report(value: dict[str, object]) -> dict[str, object]:
    result = copy.deepcopy(value)
    result["path"] = "<REPO>"
    return result


def _checks(value: dict[str, object]) -> dict[str, dict[str, object]]:
    checks = value["checks"]
    if isinstance(checks, list):
        return {str(item["id"]): item for item in checks if isinstance(item, dict)}
    return {str(key): item for key, item in checks.items()}  # type: ignore[union-attr]


def _json_stdout(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    captured = capsys.readouterr()
    assert captured.err == ""
    return json.loads(captured.out)


def _help_text(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    with pytest.raises(SystemExit) as raised:
        main(argv)
    captured = capsys.readouterr()
    return int(raised.value.code), captured.out + captured.err


def _supports_option(
    argv: list[str], option: str, capsys: pytest.CaptureFixture[str]
) -> bool:
    _code, text = _help_text(argv, capsys)
    return option in text


@pytest.mark.parametrize("case", CASES)
def test_copied_fixture_report_matches_expected_contract(
    case: str, tmp_path: Path
) -> None:
    repository = _copy_fixture(tmp_path, case)

    actual = _normalise_report(report(repository))

    assert actual == _expected(case)


@pytest.mark.parametrize("case", ("perfect-python", "perfect-node", "polyglot-mixed"))
def test_report_has_stable_top_level_json_schema(case: str, tmp_path: Path) -> None:
    value = report(_copy_fixture(tmp_path, case))

    assert set(value) == {"tool", "version", "path", "ecosystems", "score", "max", "grade", "checks"}
    assert value["tool"] == "repo-ready"
    assert value["version"] == __version__
    assert isinstance(value["path"], str)
    assert isinstance(value["ecosystems"], list)
    assert isinstance(value["checks"], list)


@pytest.mark.parametrize(
    ("marker", "ecosystem"),
    (
        ("pyproject.toml", "python"),
        ("requirements.txt", "python"),
        ("requirements.lock", "python"),
        ("setup.py", "python"),
        ("setup.cfg", "python"),
        ("Pipfile", "python"),
        ("uv.lock", "python"),
        ("poetry.lock", "python"),
        ("package.json", "node"),
        ("package-lock.json", "node"),
        ("npm-shrinkwrap.json", "node"),
        ("yarn.lock", "node"),
        ("pnpm-lock.yaml", "node"),
        (".nvmrc", "node"),
    ),
)
def test_each_supported_detector_marker_is_publicly_detectable(
    tmp_path: Path, marker: str, ecosystem: str
) -> None:
    (tmp_path / marker).write_text("marker\n", encoding="utf-8")

    assert detect_ecosystems(tmp_path) == [ecosystem]


def test_detector_returns_stable_python_then_node_order(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    assert detect_ecosystems(tmp_path) == ["python", "node"]


@pytest.mark.parametrize(
    ("rule_id", "case", "status"),
    (
        ("RR01", "empty-repo", "fail"),
        ("RR02", "empty-repo", "fail"),
        ("RR03", "empty-repo", "fail"),
        ("RR04", "empty-repo", "fail"),
        ("RR05", "python-bad", "fail"),
        ("RR06", "python-bad", "fail"),
        ("RR07", "python-bad", "fail"),
        ("RR08", "python-bad", "fail"),
        ("RR09", "python-bad", "fail"),
        ("RR10", "empty-repo", "fail"),
        ("RR11", "python-bad", "fail"),
        ("RR12", "python-bad", "fail"),
        ("RR13", "python-bad", "fail"),
    ),
)
def test_each_rr_boundary_has_a_failing_case(
    rule_id: str, case: str, status: str, tmp_path: Path
) -> None:
    check = _checks(report(_copy_fixture(tmp_path, case)))[rule_id]

    assert check["status"] == status
    assert check["id"] == rule_id
    assert check["message"] == CHECK_MESSAGES[rule_id]


@pytest.mark.parametrize("rule_id", RULE_IDS)
def test_each_rr_rule_has_weight_and_bounded_score(rule_id: str, tmp_path: Path) -> None:
    check = _checks(report(_copy_fixture(tmp_path, "perfect-python")))[rule_id]

    assert check["weight"] == CHECK_WEIGHTS[rule_id]
    assert 0 <= check["score"] <= check["max"] <= check["weight"]
    assert isinstance(check["findings"], list)


def test_partial_and_na_scoring_are_distinct(tmp_path: Path) -> None:
    mixed = _checks(report(_copy_fixture(tmp_path / "mixed", "polyglot-mixed")))
    empty = _checks(report(_copy_fixture(tmp_path / "empty", "empty-repo")))

    assert mixed["RR05"]["status"] == "partial"
    assert mixed["RR05"]["score"] == 2
    assert mixed["RR06"]["status"] == "partial"
    assert mixed["RR07"]["status"] == "partial"
    assert empty["RR05"]["status"] == "na"
    assert empty["RR05"]["max"] == 0
    assert empty["RR13"]["status"] == "na"


def test_multi_ecosystem_credit_is_proportional_and_ordered(tmp_path: Path) -> None:
    value = report(_copy_fixture(tmp_path, "polyglot-mixed"))
    checks = _checks(value)

    assert value["ecosystems"] == ["python", "node"]
    assert checks["RR05"]["score"] == round(CHECK_WEIGHTS["RR05"] / 2)
    assert checks["RR06"]["score"] == round(CHECK_WEIGHTS["RR06"] / 2)
    assert checks["RR07"]["score"] == round(CHECK_WEIGHTS["RR07"] / 2)
    assert checks["RR08"]["score"] == 11


def test_all_check_weights_sum_to_one_hundred() -> None:
    assert sum(CHECK_WEIGHTS.values()) == 100
    assert tuple(CHECK_WEIGHTS) == RULE_IDS


def test_config_excludes_are_loaded_and_keep_findings_out(tmp_path: Path) -> None:
    repository = _copy_fixture(tmp_path, "config-excludes")
    context = RepoContext(repository)
    paths = {entry.path for entry in context}

    assert "ignored/leaked.py" not in paths
    assert "ignored.txt" not in paths
    assert _checks(report(repository))["RR11"]["status"] == "pass"
    assert _checks(report(repository))["RR12"]["status"] == "pass"


def test_explicit_excludes_support_directory_globs(tmp_path: Path) -> None:
    (tmp_path / "cache" / "nested").mkdir(parents=True)
    (tmp_path / "cache" / "nested" / "secret.txt").write_text("secret\n", encoding="utf-8")
    (tmp_path / "kept.txt").write_text("kept\n", encoding="utf-8")

    context = RepoContext(tmp_path, excludes=("cache/**",))

    assert [entry.path for entry in context] == ["kept.txt"]


def test_binary_and_oversized_files_are_indexed_but_not_read(tmp_path: Path) -> None:
    repository = _copy_fixture(tmp_path, "edge-binary-and-large")
    context = RepoContext(repository)

    binary = context.entry("opaque.bin")
    large = context.entry("huge.txt")
    assert binary is not None and binary.binary and not binary.readable
    assert large is not None and large.too_large and not large.readable
    assert context.read_text("opaque.bin") is None
    assert context.read_text("huge.txt") is None


def test_binary_and_oversized_content_cannot_change_report(tmp_path: Path) -> None:
    value = _normalise_report(report(_copy_fixture(tmp_path, "edge-binary-and-large")))

    assert value == _expected("edge-binary-and-large")
    assert _checks(value)["RR11"]["status"] == "pass"


def test_symlink_is_not_followed_or_indexed(tmp_path: Path) -> None:
    repository = _copy_fixture(tmp_path, "edge-symlinks")
    link = repository / "linked-secret.txt"
    context = RepoContext(repository)

    assert link.is_symlink()
    assert context.entry("linked-secret.txt") is None
    assert _checks(report(repository))["RR11"]["status"] == "pass"


def test_malformed_utf8_is_replaced_without_aborting_scan(tmp_path: Path) -> None:
    repository = _copy_fixture(tmp_path, "edge-non-utf8")
    context = RepoContext(repository)

    text = context.read_text("invalid.py")
    assert text is not None
    assert "�" in text
    assert _normalise_report(report(repository)) == _expected("edge-non-utf8")


def test_environment_reads_report_documented_and_missing_keys(tmp_path: Path) -> None:
    check = _checks(report(_copy_fixture(tmp_path, "edge-env-usage")))["RR13"]

    assert check["status"] == "partial"
    assert check["score"] == 4
    assert {finding["file"] for finding in check["findings"]} == {"app.py"}
    assert "UNDOCUMENTED_MODE" in check["findings"][0]["detail"]


def test_no_environment_reads_are_na(tmp_path: Path) -> None:
    check = _checks(report(_copy_fixture(tmp_path, "perfect-python")))["RR13"]

    assert check["status"] == "na"
    assert check["max"] == 0


def test_docker_tags_and_cmd_are_reflected_in_rr08(tmp_path: Path) -> None:
    repository = _copy_fixture(tmp_path, "edge-docker")
    dockerfile = (repository / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.12.4-slim" in dockerfile
    assert 'CMD ["python", "-m", "http.server", "8080"]' in dockerfile
    assert _checks(report(repository))["RR08"]["status"] == "pass"


def test_node_docker_tag_is_checked_for_the_node_ecosystem(tmp_path: Path) -> None:
    repository = _copy_fixture(tmp_path, "perfect-node")
    dockerfile = (repository / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM node:20.11-slim" in dockerfile
    assert _checks(report(repository))["RR08"]["score"] == CHECK_WEIGHTS["RR08"]


@pytest.mark.parametrize("case", ("perfect-python", "perfect-node"))
def test_cli_json_matches_expected_report(case: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repository = _copy_fixture(tmp_path, case)

    assert main(["check", str(repository), "--json"]) == 0

    assert _normalise_report(_json_stdout(capsys)) == _expected(case)


def test_cli_json_is_deterministic_across_repeated_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repository = _copy_fixture(tmp_path, "polyglot-mixed")

    assert main(["check", str(repository), "--json"]) == 0
    first = _normalise_report(_json_stdout(capsys))
    assert main(["check", str(repository), "--json"]) == 0
    second = _normalise_report(_json_stdout(capsys))

    assert first == second


def test_cli_json_has_sorted_rule_ids_and_relative_findings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repository = _copy_fixture(tmp_path, "python-bad")

    assert main(["check", str(repository), "--json"]) == 0
    value = _json_stdout(capsys)
    checks = value["checks"]
    assert [check["id"] for check in checks] == list(RULE_IDS)
    assert all(not Path(finding["file"]).is_absolute() for check in checks for finding in check["findings"])


def test_cli_invalid_path_returns_two(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", "does-not-exist", "--json"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "not a directory" in captured.err


def test_cli_help_is_available_and_successful(capsys: pytest.CaptureFixture[str]) -> None:
    code, text = _help_text(["--help"], capsys)

    assert code == 0
    assert "repo-ready" in text
    assert "check" in text
    assert "fix" in text


def test_cli_version_flag_when_exposed(capsys: pytest.CaptureFixture[str]) -> None:
    if not _supports_option(["--help"], "--version", capsys):
        pytest.skip("version flag is not in the current CLI")

    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == __version__


def test_cli_default_path_when_exposed(capsys: pytest.CaptureFixture[str]) -> None:
    try:
        result = main(["check", "--json"])
    except SystemExit as raised:
        capsys.readouterr()
        pytest.skip("check path is required by the current CLI")
    assert result == 0
    value = _json_stdout(capsys)
    assert value["path"] == str(Path.cwd().absolute())


def test_cli_min_score_exit_code_when_exposed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    if not _supports_option(["check", "--help"], "--min-score", capsys):
        pytest.skip("min-score flag is not in the current CLI")

    repository = _copy_fixture(tmp_path, "python-bad")
    assert main(["check", str(repository), "--json", "--min-score", "100"]) == 1
    _json_stdout(capsys)


def test_cli_human_output_is_no_color_by_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["check", str(_copy_fixture(tmp_path, "perfect-python"))]) == 0
    output = capsys.readouterr().out
    assert "\x1b[" not in output


def test_cli_no_color_option_when_exposed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    if not _supports_option(["check", "--help"], "--no-color", capsys):
        pytest.skip("no-color flag is not in the current CLI")

    assert main(["check", str(_copy_fixture(tmp_path, "perfect-python")), "--no-color"]) == 0
    assert "\x1b[" not in capsys.readouterr().out


def _snapshot(root: Path) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            snapshot[str(path.relative_to(root))] = path.read_bytes()
    return snapshot


def test_fix_dry_run_does_not_modify_a_copied_fixture(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repository = _copy_fixture(tmp_path, "empty-repo")
    before = _snapshot(repository)

    assert main(["fix", str(repository), "--dry-run"]) == 0

    assert "Dry run:" in capsys.readouterr().out
    assert _snapshot(repository) == before


def test_fix_does_not_overwrite_an_existing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repository = _copy_fixture(tmp_path, "empty-repo")
    license_file = repository / "LICENSE"
    license_file.write_text("keep this exact content\n", encoding="utf-8")

    assert main(["fix", str(repository)]) == 0

    capsys.readouterr()
    assert license_file.read_text(encoding="utf-8") == "keep this exact content\n"


def test_fix_second_run_has_no_remaining_plans(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repository = _copy_fixture(tmp_path, "empty-repo")

    assert main(["fix", str(repository)]) == 0
    capsys.readouterr()
    assert main(["fix", str(repository)]) == 0

    assert "No missing files" in capsys.readouterr().out


def test_fix_plans_stay_inside_the_resolved_repository(tmp_path: Path) -> None:
    repository = _copy_fixture(tmp_path, "empty-repo")
    root = repository.resolve()

    plans = plan_fixes(repository / "nested" / "..")

    assert plans
    assert all(plan.path.resolve().is_relative_to(root) for plan in plans)


def test_fix_does_not_write_through_a_repository_symlink(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repository = _copy_fixture(tmp_path, "empty-repo")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside content\n", encoding="utf-8")
    (repository / "README.md").symlink_to(outside)

    assert main(["fix", str(repository)]) == 0
    capsys.readouterr()
    assert outside.read_text(encoding="utf-8") == "outside content\n"


def test_offline_guard_blocks_socket_connections() -> None:
    with socket.socket() as connection, pytest.raises(AssertionError, match="network"):
        connection.connect(("127.0.0.1", 9))


def test_offline_guard_blocks_subprocess_popen() -> None:
    with pytest.raises(AssertionError, match="subprocess"):
        subprocess.Popen([os.fspath(Path("not-a-real-command"))])


def test_report_path_is_absolute_only_in_runtime_and_never_in_findings(tmp_path: Path) -> None:
    value = report(_copy_fixture(tmp_path, "python-bad"))

    assert Path(value["path"]).is_absolute()
    assert all(
        not Path(finding["file"]).is_absolute()
        for check in value["checks"]
        for finding in check["findings"]
    )
