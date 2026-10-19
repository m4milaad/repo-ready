from __future__ import annotations

import json
from pathlib import Path

from repo_ready.contracts import build_contract_graph, diagnose
from repo_ready.context import RepoContext
from repo_ready.scoring import report


def _repository(root: Path, files: dict[str, str]) -> Path:
    repository = root / "repository"
    repository.mkdir()
    for relative, contents in files.items():
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    return repository


def _codes(graph) -> set[str]:
    return {diagnostic.code for diagnostic in graph.diagnostics}


def test_perfect_python_contracts_are_consistent() -> None:
    repository = Path(__file__).parent / "fixtures" / "perfect-python" / "repo"
    graph = diagnose(repository)

    assert graph.version == 1
    assert graph.diagnostics == ()
    assert any(item.subject == "make-test" for item in graph.evidence)
    assert any(item.subject == "pin-python" for item in graph.evidence)
    assert all("\\" not in item.file for item in graph.evidence)


def test_readme_and_makefile_test_contract_conflict(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        {
            "README.md": "# Example\n\n## Test\n\n```text\npython -m pytest -q\n```\n",
            "Makefile": "test:\n\tnpm test\n",
            "package.json": '{"scripts": {"test": "npm test"}}\n',
        },
    )

    graph = diagnose(repository)

    assert "CG001" in _codes(graph)
    assert any(edge.status == "mismatch" for edge in graph.edges if edge.relation == "documents")


def test_runtime_and_docker_contract_conflict(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        {
            ".python-version": "3.12\n",
            "pyproject.toml": "[project]\nname='example'\nversion='1.0'\nrequires-python='>=3.12'\n",
            "README.md": "# Example\n\n## Test\n\nmake test\n",
            "Makefile": "test:\n\tpython -m pytest -q\n",
            "Dockerfile": "FROM python:3.11-slim\nCMD [\"python\", \"-m\", \"pytest\"]\n",
        },
    )

    graph = diagnose(repository)

    assert "CG012" in _codes(graph)
    assert any(item.file == "Dockerfile" for item in graph.evidence)


def test_missing_docker_copy_is_diagnostic(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        {
            "README.md": "# Example\n\n## Test\n\nmake test\n",
            "Makefile": "test:\n\tpython -m pytest -q\n",
            "pyproject.toml": "[project]\nname='example'\nversion='1.0'\n",
            "Dockerfile": "FROM python:3.12-slim\nCOPY missing.lock /app/missing.lock\nCMD [\"python\", \"-m\", \"pytest\"]\n",
        },
    )

    graph = diagnose(repository)

    assert "CG014" in _codes(graph)
    assert any(edge.status == "missing" and edge.expected == "missing.lock" for edge in graph.edges)


def test_graph_does_not_change_scoring_report(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        {
            "README.md": "# Example\n\n## Setup\n\n## Run\n\n## Test\n\nmake test\n",
            "Makefile": "test:\n\tpython -m pytest -q\n",
            "pyproject.toml": "[project]\nname='example'\nversion='1.0'\n",
        },
    )
    context = RepoContext(repository)
    before = report(repository, context=context)
    graph = build_contract_graph(context)
    after = report(repository, context=context)

    assert graph.as_dict()["version"] == 1
    assert before == after


def test_graph_serialization_is_deterministic(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        {
            "README.md": "# Example\n\n## Test\n\nmake test\n",
            "Makefile": "test:\n\tpython -m pytest -q\n",
            "pyproject.toml": "[project]\nname='example'\nversion='1.0'\n",
        },
    )

    first = json.dumps(diagnose(repository).as_dict(), sort_keys=True)
    second = json.dumps(diagnose(repository).as_dict(), sort_keys=True)

    assert first == second
