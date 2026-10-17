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
from repo_ready.fixer import PlannedFile, apply_fix_plan, plan_fixes
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


def normalized_report(value: dict[str, object]) -> dict[str, object]:
    normalized = dict(value)
    normalized["path"] = "<REPO>"
    return normalized


def json_output(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    captured = capsys.readouterr()
    assert captured.err == ""
    return json.loads(captured.out)


@pytest.mark.parametrize("case", CASES)
def test_fixture_expected_json_contract(case: str, tmp_path: Path) -> None:
    assert normalized_report(report(copy_fixture(tmp_path, case))) == expected(case)


@pytest.mark.parametrize("case", CASES)
def test_report_schema_is_stable(case: str, tmp_path: Path) -> None:
    value = report(copy_fixture(tmp_path, case))
    assert tuple(value) == ("tool", "version", "path", "ecosystems", "score", "max", "grade", "checks")
    assert value["tool"] == "repo-ready"
    assert value["version"] == __version__
    assert type(value["path"]) is str
    assert type(value["ecosystems"]) is list
    assert all(type(ecosystem) is str for ecosystem in value["ecosystems"])
    assert type(value["score"]) is int
    assert value["max"] == 100
    assert type(value["max"]) is int
    assert type(value["grade"]) is str
    assert type(value["checks"]) is list
    for item in value["checks"]:
        assert tuple(item) == ("id", "title", "weight", "earned", "status", "message", "fix_hint", "fixable", "findings")
        assert type(item["id"]) is str
        assert type(item["title"]) is str
        assert type(item["weight"]) is int
        assert type(item["earned"]) is int
        assert item["status"] in {"pass", "partial", "fail", "na"}
        assert type(item["message"]) is str
        assert item["fix_hint"] is None or type(item["fix_hint"]) is str
        assert type(item["fixable"]) is bool
        assert type(item["findings"]) is list
        for finding in item["findings"]:
            assert tuple(finding) == ("file", "line", "detail")
            assert type(finding["file"]) is str
            assert finding["line"] is None or type(finding["line"]) is int
            assert type(finding["detail"]) is str


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
def test_check_and_finding_order_is_stable(case: str, tmp_path: Path) -> None:
    value = report(copy_fixture(tmp_path, case))
    assert [item["id"] for item in value["checks"]] == list(RULE_IDS)
    assert [item["title"] for item in value["checks"]] == [CHECK_TITLES[key] for key in RULE_IDS]
    for item in value["checks"]:
        findings = item["findings"]
        assert findings == sorted(findings, key=lambda finding: (finding["file"], finding["line"] or 0, finding["detail"]))


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
    for case in CASES:
        value = report(copy_fixture(tmp_path, case))
        for item in value["checks"]:
            if item["status"] == "na":
                assert item["earned"] == item["weight"] == CHECK_WEIGHTS[item["id"]]


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
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("password=do-not-scan-this\n", encoding="utf-8")
    (repository / "linked-secret.txt").symlink_to(outside)
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


def test_empty_dockerfile_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("\n", encoding="utf-8")
    item = checks(report(tmp_path))["RR08"]
    assert item["earned"] == 0
    assert item["status"] == "fail"
    assert item["findings"] == [{
        "file": "Dockerfile",
        "line": None,
        "detail": "Root Dockerfile or Containerfile is empty or unreadable.",
    }]


@pytest.mark.parametrize(
    ("manifest", "manifest_text", "lockfile"),
    (
        ("package.json", '{"name":"fixture","dependencies":{"fixture-lib":"1.0.0"}}\n', "package-lock.json"),
        ("Pipfile", "[packages]\nfixture-lib = \"*\"\n", "Pipfile.lock"),
    ),
)
def test_malformed_lockfiles_are_rejected(
    manifest: str,
    manifest_text: str,
    lockfile: str,
    tmp_path: Path,
) -> None:
    (tmp_path / manifest).write_text(manifest_text, encoding="utf-8")
    (tmp_path / lockfile).write_text("{not valid JSON\n", encoding="utf-8")
    item = checks(report(tmp_path))["RR06"]
    assert item["earned"] == 0
    assert item["status"] == "fail"
    assert item["findings"] == [{
        "file": ".",
        "line": None,
        "detail": f"{'Node' if manifest == 'package.json' else 'Python'} project does not satisfy this check.",
    }]


def test_human_output_includes_findings_and_top_fixes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["check", str(copy_fixture(tmp_path, "python-bad"))]) == 0
    output = capsys.readouterr().out
    assert "Top fixes:" in output
    assert "app.py" in output


@pytest.mark.parametrize("case", ("perfect-python", "perfect-node"))
def test_cli_json_matches_expected(case: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repository = copy_fixture(tmp_path, case)
    assert main(["check", str(repository), "--json"]) == 0
    value = json_output(capsys)
    assert normalized_report(value) == expected(case)


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


def test_fix_plan_operations_are_inspectable(tmp_path: Path) -> None:
    repository = copy_fixture(tmp_path, "empty-repo")
    (repository / "README.md").write_bytes(b"keep these exact bytes\r\n")

    plans = plan_fixes(repository)

    assert {operation.action for operation in plans} == {"create", "skip-existing", "manual"}
    assert all(operation.status == operation.action for operation in plans)
    assert all(operation.reason for operation in plans)
    readme = next(operation for operation in plans if operation.path.name == "README.md")
    assert readme.action == "skip-existing"
    assert "already exists" in readme.reason
    assert readme.as_dict(repository) == {
        "action": "skip-existing",
        "path": "README.md",
        "reason": readme.reason,
    }
    assert any(operation.action == "create" and operation.reason == "contributor guide" for operation in plans)


def test_fix_creates_only_missing_files_and_preserves_existing_bytes(tmp_path: Path) -> None:
    repository = copy_fixture(tmp_path, "empty-repo")
    existing = repository / "README.md"
    existing.write_bytes(b"keep\r\nexactly\x00")
    before = _snapshot(repository)
    outcomes = apply_fix_plan(
        plan_fixes(repository, license_name="mit", holder="Test Holder"),
        root=repository,
    )

    after = _snapshot(repository)
    assert all(after[path] == content for path, content in before.items())
    assert any(operation.action == "skip-existing" and operation.path == existing for operation in outcomes)
    assert (repository / "LICENSE").is_file()
    assert any(operation.action == "create" and operation.path == repository / "LICENSE" for operation in outcomes)


def test_fix_human_summary_lists_created_existing_and_manual_items(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = copy_fixture(tmp_path, "empty-repo")
    (repository / "README.md").write_text("keep\n", encoding="utf-8")

    assert main(["fix", str(repository)]) == 0
    output = capsys.readouterr().out

    assert "Created:" in output
    assert "Skipped existing files:" in output
    assert "README.md" in output
    assert "Manual items:" in output
    assert "lockfiles (manual)" in output
    assert "tests (manual)" in output
    assert "machine-specific paths (manual)" in output


def test_fix_json_summary_lists_created_existing_and_manual_items(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = copy_fixture(tmp_path, "empty-repo")
    (repository / "README.md").write_text("keep\n", encoding="utf-8")

    assert main(["fix", str(repository), "--json"]) == 0
    value = json_output(capsys)

    assert value["dry_run"] is False
    assert value["created"]
    assert value["would_create"] == []
    assert any(item["path"] == "README.md" and item["action"] == "skip-existing" for item in value["skipped_existing"])
    manual_paths = {item["path"] for item in value["manual"]}
    assert {"lockfiles (manual)", "tests (manual)", "machine-specific paths (manual)"} <= manual_paths
    assert {item["action"] for item in value["operations"]} >= {"create", "skip-existing", "manual"}
    assert all(item["reason"] for item in value["operations"])


def test_fix_dry_run_json_does_not_write(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repository = copy_fixture(tmp_path, "empty-repo")
    before = _snapshot(repository)

    assert main(["fix", str(repository), "--dry-run", "--json"]) == 0
    value = json_output(capsys)

    assert value["dry_run"] is True
    assert value["created"] == []
    assert value["would_create"]
    assert any(item["action"] == "create" for item in value["operations"])
    assert any(item["action"] == "manual" for item in value["operations"])
    assert _snapshot(repository) == before


def test_fix_second_run_is_noop(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repository = copy_fixture(tmp_path, "empty-repo")
    assert main(["fix", str(repository), "--json"]) == 0
    json_output(capsys)
    after_first = _snapshot(repository)

    assert main(["fix", str(repository), "--json"]) == 0
    value = json_output(capsys)

    assert _snapshot(repository) == after_first
    assert value["created"] == []
    assert value["would_create"] == []
    assert value["skipped_existing"]
    assert value["manual"]
    assert all(item["action"] != "create" for item in value["operations"])


@pytest.mark.parametrize(
    ("manifest", "contents", "runtime_path"),
    (
        ("pyproject.toml", "[project]\nname = 'unknown-runtime'\n", ".python-version"),
        ("package.json", '{"name": "unknown-runtime"}\n', ".nvmrc"),
    ),
)
def test_unknown_runtime_is_manual_not_taken_from_interpreter(
    manifest: str,
    contents: str,
    runtime_path: str,
    tmp_path: Path,
) -> None:
    (tmp_path / manifest).write_text(contents, encoding="utf-8")

    plans = plan_fixes(tmp_path)

    runtime = next(operation for operation in plans if operation.path.name == runtime_path)
    docker = next(operation for operation in plans if operation.path.name == "Dockerfile")
    assert runtime.action == "manual"
    assert runtime.content == ""
    assert "unknown" in runtime.reason.lower()
    assert docker.action == "manual"
    assert "known runtime" in docker.reason
    assert not (tmp_path / runtime_path).exists()


def test_lockfile_test_and_hardcoded_path_work_remain_manual(tmp_path: Path) -> None:
    repository = copy_fixture(tmp_path, "python-bad")

    manual = {
        operation.path.name: operation
        for operation in plan_fixes(repository)
        if operation.action == "manual"
    }

    assert {
        "lockfiles (manual)",
        "tests (manual)",
        "machine-specific paths (manual)",
    } <= manual.keys()
    assert "Lockfiles are never created automatically" in manual["lockfiles (manual)"].reason
    assert "Tests and test commands are never created automatically" in manual["tests (manual)"].reason
    assert "Machine-specific absolute paths are never rewritten automatically" in manual["machine-specific paths (manual)"].reason
    assert all(operation.content == "" for operation in manual.values())


def test_fix_does_not_use_interpreter_runtime(tmp_path: Path) -> None:
    repository = copy_fixture(tmp_path, "python-bad")
    plans = plan_fixes(repository, runtime_version="3.12")
    assert all("3.11" not in plan.content for plan in plans)


def test_fix_rejects_symlinked_parent(tmp_path: Path) -> None:
    repository = copy_fixture(tmp_path, "empty-repo")
    outside = tmp_path / "outside"
    outside.mkdir()
    (repository / ".github").symlink_to(outside, target_is_directory=True)

    plans = plan_fixes(repository)
    unsafe = [operation for operation in plans if operation.action == "skip-unsafe"]
    outcomes = apply_fix_plan(plans, root=repository)

    assert unsafe
    assert all("parent path is a symlink" in operation.reason for operation in unsafe)
    assert any(operation.action == "skip-unsafe" for operation in outcomes)
    assert not (outside / "PULL_REQUEST_TEMPLATE.md").exists()
    assert not any(outside.rglob("*"))


def test_apply_fix_plan_rejects_targets_outside_root(tmp_path: Path) -> None:
    repository = copy_fixture(tmp_path, "empty-repo")
    outside = tmp_path / "outside"
    outside.mkdir()
    operation = PlannedFile(outside / "escape.txt", "must not be written", "test containment")

    outcomes = apply_fix_plan([operation], root=repository)

    assert len(outcomes) == 1
    assert outcomes[0].action == "skip-unsafe"
    assert "escapes the repository root" in outcomes[0].reason
    assert not (outside / "escape.txt").exists()


def test_fix_plans_are_inside_root(tmp_path: Path) -> None:
    repository = copy_fixture(tmp_path, "empty-repo")
    assert all(plan.path.resolve().is_relative_to(repository.resolve()) for plan in plan_fixes(repository))


def test_offline_socket_guard_canary() -> None:
    with socket.socket() as connection, pytest.raises(AssertionError):
        connection.connect(("127.0.0.1", 9))


def test_offline_subprocess_guard_canary() -> None:
    with pytest.raises(AssertionError):
        subprocess.Popen([os.fspath(Path("not-a-real-command"))])
