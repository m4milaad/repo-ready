from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from repo_ready import __version__
from repo_ready.checks import CHECK_TITLES, CHECK_WEIGHTS, environment_reads
from repo_ready.cli import main
from repo_ready.context import RepoContext
from repo_ready.detector import detect_ecosystems
from repo_ready.fixer import plan_fixes
from repo_ready.scoring import report


FIXTURES = Path(__file__).parent / "fixtures"
CASES = tuple(sorted(path.name for path in FIXTURES.iterdir() if path.is_dir() and (path / "repo").is_dir()))
RULE_IDS = tuple(CHECK_WEIGHTS)


def fixture_repo(case: str) -> Path:
    return FIXTURES / case / "repo"


def expected(case: str) -> dict[str, object]:
    return json.loads((FIXTURES / case / "expected.json").read_text(encoding="utf-8"))


def copy_fixture(tmp_path: Path, case: str) -> Path:
    destination = tmp_path / case / "repo"
    shutil.copytree(fixture_repo(case), destination, symlinks=True)
    return destination


def checks(value: dict[str, object]) -> dict[str, dict[str, object]]:
    return {str(item["id"]): item for item in value["checks"] if isinstance(item, dict)}


def compact(value: dict[str, object]) -> dict[str, object]:
    return {
        "score": value["score"],
        "max": value["max"],
        "grade": value["grade"],
        "ecosystems": value["ecosystems"],
        "checks": {
            key: {"earned": value["earned"], "status": value["status"]}
            for key, value in checks(value).items()
        },
    }


def json_output(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    captured = capsys.readouterr()
    assert captured.err == ""
    return json.loads(captured.out)


@pytest.mark.parametrize("case", CASES)
def test_fixture_expected_json_contract(case: str, tmp_path: Path) -> None:
    assert compact(report(copy_fixture(tmp_path, case))) == expected(case)


@pytest.mark.parametrize("case", ("perfect-python", "perfect-node", "polyglot-mixed"))
def test_report_schema_is_stable(case: str, tmp_path: Path) -> None:
    value = report(copy_fixture(tmp_path, case))
    assert tuple(value) == ("tool", "version", "path", "ecosystems", "score", "max", "grade", "checks")
    assert value["tool"] == "repo-ready"
    assert value["version"] == __version__
    assert value["max"] == 100
    assert all(set(item) == {"id", "title", "weight", "earned", "status", "message", "fix_hint", "fixable", "findings"} for item in value["checks"])


@pytest.mark.parametrize(
    ("marker", "ecosystem"),
    (("pyproject.toml", "python"), ("requirements.txt", "python"), ("requirements-dev.txt", "python"),
     ("setup.py", "python"), ("setup.cfg", "python"), ("Pipfile", "python"), ("package.json", "node")),
)
def test_supported_manifest_markers(marker: str, ecosystem: str, tmp_path: Path) -> None:
    (tmp_path / marker).write_text("{}\n" if marker.endswith(".json") else "marker\n", encoding="utf-8")
    assert detect_ecosystems(tmp_path) == [ecosystem]


def test_mixed_ecosystems_are_sorted(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    assert detect_ecosystems(tmp_path) == ["python", "node"]


@pytest.mark.parametrize("rule_id", RULE_IDS)
def test_every_rule_has_the_fixed_weight(rule_id: str) -> None:
    assert CHECK_WEIGHTS[rule_id] == {"RR01": 14, "RR02": 6, "RR03": 6, "RR04": 4, "RR05": 4, "RR06": 10, "RR07": 8, "RR08": 12, "RR09": 12, "RR10": 6, "RR11": 8, "RR12": 4, "RR13": 6}[rule_id]


def test_weights_sum_to_exactly_100() -> None:
    assert sum(CHECK_WEIGHTS.values()) == 100
    assert tuple(CHECK_WEIGHTS) == RULE_IDS


@pytest.mark.parametrize("case", CASES)
def test_check_ids_are_sorted_and_titles_are_known(case: str, tmp_path: Path) -> None:
    value = report(copy_fixture(tmp_path, case))
    assert [item["id"] for item in value["checks"]] == list(RULE_IDS)
    assert [item["title"] for item in value["checks"]] == [CHECK_TITLES[key] for key in RULE_IDS]


@pytest.mark.parametrize("case", ("empty-repo", "python-bad", "node-bad"))
def test_bad_repositories_have_actionable_findings(case: str, tmp_path: Path) -> None:
    value = report(copy_fixture(tmp_path, case))
    failed = [item for item in value["checks"] if item["status"] in {"fail", "partial"}]
    assert failed
    assert all(item["fix_hint"] or item["findings"] for item in failed)
    assert all(not Path(finding["file"]).is_absolute() for item in failed for finding in item["findings"])


def test_perfect_fixtures_score_100(tmp_path: Path) -> None:
    assert report(copy_fixture(tmp_path, "perfect-python"))["score"] == 100
    assert report(copy_fixture(tmp_path, "perfect-node"))["score"] == 100


def test_repo_ready_scores_itself_100() -> None:
    repository = Path(__file__).resolve().parents[1]
    assert report(repository)["score"] == 100


def test_na_checks_earn_their_full_weight(tmp_path: Path) -> None:
    value = report(copy_fixture(tmp_path, "empty-repo"))
    for rule_id in ("RR10",):
        item = checks(value)[rule_id]
        assert item["status"] == "na"
        assert item["earned"] == CHECK_WEIGHTS[rule_id]


def test_mixed_ecosystem_scoring_is_proportional(tmp_path: Path) -> None:
    value = report(copy_fixture(tmp_path, "polyglot-mixed"))
    assert value["ecosystems"] == ["node", "python"]
    assert checks(value)["RR07"]["earned"] == 4


def test_config_excludes_fixture_content(tmp_path: Path) -> None:
    repository = copy_fixture(tmp_path, "config-excludes")
    context = RepoContext(repository)
    assert "ignored/leaked.py" not in {entry.path for entry in context}


def test_explicit_directory_excludes_are_deterministic(tmp_path: Path) -> None:
    (tmp_path / "cache" / "nested").mkdir(parents=True)
    (tmp_path / "cache" / "nested" / "secret.txt").write_text("x", encoding="utf-8")
    (tmp_path / "kept.txt").write_text("x", encoding="utf-8")
    assert [entry.path for entry in RepoContext(tmp_path, excludes=("cache/**",))] == ["kept.txt"]


def test_binary_and_large_files_are_skipped(tmp_path: Path) -> None:
    context = RepoContext(copy_fixture(tmp_path, "edge-binary-and-large"))
    assert context.entry("opaque.bin").binary
    assert context.entry("huge.txt").too_large
    assert context.read_text("opaque.bin") is None
    assert context.read_text("huge.txt") is None


def test_binary_nul_after_first_8k_is_text(tmp_path: Path) -> None:
    path = tmp_path / "late-null.txt"
    path.write_bytes(b"a" * 9000 + b"\0")
    assert RepoContext(tmp_path).read_text("late-null.txt") is not None


def test_symlinks_are_not_indexed(tmp_path: Path) -> None:
    repository = copy_fixture(tmp_path, "edge-symlinks")
    assert RepoContext(repository).entry("linked-secret.txt") is None


def test_invalid_utf8_is_replaced(tmp_path: Path) -> None:
    context = RepoContext(copy_fixture(tmp_path, "edge-non-utf8"))
    assert "�" in context.read_text("invalid.py")


def test_environment_reads_and_names_are_detected(tmp_path: Path) -> None:
    repository = copy_fixture(tmp_path, "edge-env-usage")
    reads = environment_reads(RepoContext(repository))
    assert {name for _, _, name in reads} == {"APP_HOST", "APP_PORT", "UNDOCUMENTED_MODE"}
    assert checks(report(repository))["RR10"]["status"] == "partial"


def test_no_environment_reads_make_rr10_na(tmp_path: Path) -> None:
    item = checks(report(copy_fixture(tmp_path, "perfect-python")))["RR10"]
    assert item["status"] == "na"
    assert item["earned"] == 6


def test_dockerfile_requires_non_latest_tag_and_command(tmp_path: Path) -> None:
    value = checks(report(copy_fixture(tmp_path, "edge-docker")))
    assert value["RR08"]["earned"] == 6
    assert value["RR08"]["status"] == "partial"


def test_human_output_includes_findings_and_top_fixes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", str(copy_fixture(tmp_path, "python-bad"))]) == 0
    output = capsys.readouterr().out
    assert "Top fixes:" in output
    assert "app.py" in output


@pytest.mark.parametrize("case", ("perfect-python", "perfect-node"))
def test_cli_json_matches_expected(case: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repository = copy_fixture(tmp_path, case)
    assert main(["check", str(repository), "--json"]) == 0
    assert compact(json_output(capsys)) == expected(case)


def test_cli_json_is_deterministic(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repository = copy_fixture(tmp_path, "polyglot-mixed")
    assert main(["check", str(repository), "--json"]) == 0
    first = json_output(capsys)
    assert main(["check", str(repository), "--json"]) == 0
    assert first == json_output(capsys)


def test_cli_default_path_and_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", "--json", "--min-score", "0"]) == 0
    assert json_output(capsys)["path"] == str(Path.cwd())
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == __version__


def test_cli_min_score_returns_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", str(copy_fixture(tmp_path, "python-bad")), "--min-score", "100", "--json"]) == 1
    json_output(capsys)


def test_config_min_score_returns_one(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repository = copy_fixture(tmp_path, "python-bad")
    (repository / ".repo-ready.toml").write_text("min_score = 100\n", encoding="utf-8")
    assert main(["check", str(repository), "--json"]) == 1
    json_output(capsys)


def test_cli_no_color_is_supported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", str(copy_fixture(tmp_path, "perfect-python")), "--no-color"]) == 0
    assert "\x1b[" not in capsys.readouterr().out


def test_cli_invalid_path_returns_two(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", "does-not-exist", "--json"]) == 2
    assert "not a directory" in capsys.readouterr().err


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file() and not path.is_symlink()}


def test_fix_dry_run_does_not_write(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repository = copy_fixture(tmp_path, "empty-repo")
    before = _snapshot(repository)
    assert main(["fix", str(repository), "--dry-run"]) == 0
    assert _snapshot(repository) == before
    assert "Dry run" in capsys.readouterr().out


def test_fix_creates_only_missing_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repository = copy_fixture(tmp_path, "empty-repo")
    existing = repository / "README.md"
    existing.write_text("keep\n", encoding="utf-8")
    assert main(["fix", str(repository), "--license", "mit", "--holder", "Test Holder"]) == 0
    assert existing.read_text(encoding="utf-8") == "keep\n"
    assert (repository / "LICENSE").is_file()
    capsys.readouterr()


def test_fix_second_run_is_noop(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repository = copy_fixture(tmp_path, "empty-repo")
    main(["fix", str(repository)])
    capsys.readouterr()
    main(["fix", str(repository)])
    assert "No safe missing files" in capsys.readouterr().out


def test_fix_does_not_use_interpreter_runtime(tmp_path: Path) -> None:
    repository = copy_fixture(tmp_path, "python-bad")
    plans = plan_fixes(repository, runtime_version="3.12")
    assert all("3.11" not in plan.content for plan in plans)


def test_fix_rejects_symlinked_parent(tmp_path: Path) -> None:
    repository = copy_fixture(tmp_path, "empty-repo")
    outside = tmp_path / "outside"
    outside.mkdir()
    (repository / ".github").symlink_to(outside, target_is_directory=True)
    main(["fix", str(repository)])
    assert not (outside / "PULL_REQUEST_TEMPLATE.md").exists()


def test_fix_plans_are_inside_root(tmp_path: Path) -> None:
    repository = copy_fixture(tmp_path, "empty-repo")
    assert all(plan.path.resolve().is_relative_to(repository.resolve()) for plan in plan_fixes(repository))


def test_offline_socket_guard_canary() -> None:
    with socket.socket() as connection, pytest.raises(AssertionError):
        connection.connect(("127.0.0.1", 9))


def test_offline_subprocess_guard_canary() -> None:
    with pytest.raises(AssertionError):
        subprocess.Popen([os.fspath(Path("not-a-real-command"))])
