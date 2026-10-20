from __future__ import annotations

import json
import io
import sys
from pathlib import Path

from repo_ready.checks import rr01, rr10
from repo_ready.cli import main
from repo_ready.context import RepoContext
from repo_ready.contracts import diagnose
from repo_ready.fixer import plan_fixes
from repo_ready.scoring import report
from repo_ready.showcase import build_showcase


def _repo(root: Path, files: dict[str, str]) -> Path:
    path = root / "repo"
    path.mkdir()
    for name, contents in files.items():
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents, encoding="utf-8")
    return path


def test_readme_semantics_accept_development_heading_and_commands(tmp_path: Path) -> None:
    repository = _repo(tmp_path, {
        "README.md": (
            "# App\n\n## 🏁 Getting Started\n"
            "```sh\nnpm install\nnpm run dev\n``\n\n"
            "## Installation & Development\nUse the commands above.\n"
            "## Quality\nRun `npm test`.\n"
        ),
    })

    result = rr01(RepoContext(repository))

    assert result.status == "pass"
    assert result.earned == result.weight


def test_nested_env_examples_are_combined_and_reads_are_deduplicated(tmp_path: Path) -> None:
    repository = _repo(tmp_path, {
        "backend/app.py": "import os\nvalue = os.getenv('MODEL_BACKEND')\nother = os.getenv('MODEL_BACKEND')\n",
        "backend/.env.example": "# MODEL_BACKEND=ollama\n",
    })

    result = rr10(RepoContext(repository))

    assert result.status == "pass"
    assert result.findings == ()


def test_external_env_configuration_suppresses_platform_finding(tmp_path: Path) -> None:
    repository = _repo(tmp_path, {
        ".repo-ready.toml": 'external_env = ["DEPLOYMENT_REGION"]\n',
        "app.py": "import os\nregion = os.getenv('DEPLOYMENT_REGION')\n",
    })

    result = rr10(RepoContext(repository))

    assert result.status == "na"


def test_auto_profile_does_not_require_docker_for_native_deployment(tmp_path: Path) -> None:
    repository = _repo(tmp_path, {
        "package.json": '{"name":"web","scripts":{"test":"npm test"}}\n',
        "package-lock.json": '{"lockfileVersion":3,"packages":{"node_modules/web":{"version":"1.0.0"}}}\n',
        "vercel.json": '{}\n',
    })

    legacy = {item["id"]: item for item in report(repository, profile="legacy")["checks"]}
    automatic = {item["id"]: item for item in report(repository, profile="auto")["checks"]}

    assert legacy["RR08"]["status"] == "fail"
    assert automatic["RR08"]["status"] == "na"
    assert automatic["RR08"]["earned"] == automatic["RR08"]["weight"]


def test_workflow_dependency_reference_uses_working_directory(tmp_path: Path) -> None:
    repository = _repo(tmp_path, {
        "backend/requirements.txt": "requests==2.31.0\n",
        ".github/workflows/ci.yml": (
            "name: CI\non: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - run: pip install -r requirements.txt\n"
            "        working-directory: backend\n"
        ),
    })

    graph = diagnose(repository)

    references = [edge for edge in graph.edges if edge.relation == "references"]
    assert references
    assert all(edge.status == "satisfied" for edge in references)
    assert not any(item.code == "CG007" for item in graph.diagnostics)


def test_duplicate_workflow_keys_are_reported(tmp_path: Path) -> None:
    repository = _repo(tmp_path, {
        ".github/workflows/ci.yml": "name: CI\nname: Duplicate\non: [push]\njobs: {}\n",
    })

    graph = diagnose(repository)

    assert any(item.code == "CG015" for item in graph.diagnostics)


def test_fix_templates_use_detected_test_command(tmp_path: Path) -> None:
    repository = _repo(tmp_path, {
        "package.json": '{"name":"web","scripts":{"test":"vitest run"}}\n',
        ".nvmrc": "20\n",
    })

    plans = plan_fixes(repository)
    pull_request = next(item for item in plans if item.path.name == "PULL_REQUEST_TEMPLATE.md")
    workflow = next(item for item in plans if item.path.name == "ci.yml")

    assert "npm test" in pull_request.content
    assert "npm test" in workflow.content


def test_showcase_marks_a_noop_scenario_unavailable(tmp_path: Path) -> None:
    repository = _repo(tmp_path, {
        ".python-version": "3.12\n",
        "pyproject.toml": "[project]\nname='demo'\nversion='1.0'\n",
    })
    payload = build_showcase(repository)
    assert payload["analysis"]["repository"]["inventory"]["inventory_fingerprint"]
    scenarios = {item["id"]: item for item in payload["analysis"]["scenarios"]}
    assert scenarios["remove-test-entry-point"]["status"] == "unavailable"


def test_cli_json_keeps_scan_metadata_out_of_stdout_progress(monkeypatch, tmp_path: Path, capsys) -> None:
    repository = _repo(tmp_path, {"README.md": "# Demo\n"})
    assert main(["check", str(repository), "--json", "--no-progress"]) == 0
    captured = capsys.readouterr()
    value = json.loads(captured.out)

    assert captured.err == ""
    assert value["scan"]["files"] == 1
    assert value["scan"]["effective_scope"] == "filesystem"


def test_interactive_cli_progress_reports_scope_and_elapsed_time(monkeypatch, tmp_path: Path, capsys) -> None:
    class TTY(io.StringIO):
        def isatty(self) -> bool:
            return True

    repository = _repo(tmp_path, {"README.md": "# Demo\n"})
    error = TTY()
    monkeypatch.setattr(sys, "stderr", error)

    assert main(["check", str(repository)]) == 0

    assert "Scanning" in error.getvalue()
    assert "Scope: filesystem" in error.getvalue()
    assert "Completed in" in error.getvalue()
