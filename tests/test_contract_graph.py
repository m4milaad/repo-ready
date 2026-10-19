from __future__ import annotations

import json
from pathlib import Path

import pytest

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


@pytest.mark.parametrize(
    ("ecosystem", "pin", "constraint", "status"),
    [
        ("python", "3.13", ">=3.12,<3.13", "mismatch"),
        ("python", "3.12", ">=3.12,<3.13", "satisfied"),
        ("python", "3.12.9", ">=3.12,<=3.12.9", "satisfied"),
        ("python", "3.12.10", ">=3.12,<=3.12.9", "mismatch"),
        ("python", "3.12", ">=3.12.4,<3.13", "unknown"),
        ("python", "3.12", ">=3.12,!=3.12.1", "unknown"),
        ("python", "3.12", "~=3.12", "unknown"),
        ("python", "3.12", ">=3.12rc1", "unknown"),
        ("node", "21", ">=20 <21", "mismatch"),
        ("node", "20", ">=20 <21", "satisfied"),
        ("node", "20", ">20", "mismatch"),
        ("node", "20.12.1", "<=20", "satisfied"),
        ("node", "20.12.1", "^20.11.0", "satisfied"),
        ("node", "0.3.0", "^0.2.1", "mismatch"),
        ("node", "0.0.4", "^0.0.3", "mismatch"),
        ("node", "20", "20 || 22", "unknown"),
        ("node", "20", "20.x", "unknown"),
    ],
)
def test_runtime_constraints_are_compared_as_complete_numeric_intervals(
    tmp_path: Path, ecosystem: str, pin: str, constraint: str, status: str,
) -> None:
    files = (
        {".python-version": pin, "pyproject.toml": f"[project]\nrequires-python={constraint!r}\n"}
        if ecosystem == "python" else
        {".nvmrc": pin, "package.json": json.dumps({"engines": {"node": constraint}})}
    )
    graph = diagnose(_repository(tmp_path, files))

    assert [edge.status for edge in graph.edges if edge.relation == "matches"] == [status]
    assert ("CG013" in _codes(graph)) == (status == "mismatch")


@pytest.mark.parametrize(
    ("pin", "constraint", "status"),
    [("3.13", ">=3.12,<3.13", "mismatch"), ("3.12", ">=3.12,!=3.12.1", "unknown"), ("3.12", "  >=3.12, <3.13  ", "satisfied")],
)
def test_readme_runtime_ranges_do_not_use_only_the_first_version(
    tmp_path: Path, pin: str, constraint: str, status: str,
) -> None:
    graph = diagnose(_repository(tmp_path, {
        ".python-version": pin, "pyproject.toml": "[project]\n",
        "README.md": f"# Example\n\n## Requirements\n\n- Python {constraint}\n",
    }))

    assert [edge.status for edge in graph.edges if edge.relation == "matches"] == [status]


@pytest.mark.parametrize(
    ("ecosystem", "pin", "patch"),
    [("python", "3.12", "3.12.9"), ("python", "3.12.9", "3.12"), ("node", "20.11", "20.11.1")],
)
def test_runtime_prefix_pins_agree_across_docker_and_ci(
    tmp_path: Path, ecosystem: str, pin: str, patch: str,
) -> None:
    files = {".python-version": pin, "pyproject.toml": "[project]\n"} if ecosystem == "python" else {".nvmrc": pin, "package.json": "{}"}
    files["Dockerfile"] = f"FROM {ecosystem}:{patch}-slim\n"
    files[".github/workflows/ci.yml"] = f"steps:\n  - uses: actions/setup-{ecosystem}@v5\n    with:\n      {ecosystem}-version: '{patch}'\n"
    graph = diagnose(_repository(tmp_path, files))

    assert len([edge for edge in graph.edges if edge.relation == "matches"]) == 2
    assert all(edge.status == "satisfied" for edge in graph.edges if edge.relation == "matches")
    assert graph.diagnostics == ()


@pytest.mark.parametrize("instruction", ["CMD", "ENTRYPOINT"])
def test_docker_json_test_argv_is_linked(tmp_path: Path, instruction: str) -> None:
    graph = diagnose(_repository(tmp_path, {
        "Makefile": "test:\n\tnpm test\n",
        "Dockerfile": f'FROM python:3.12\n{instruction} ["python", "-m", "pytest", "-q"]\n',
    }))

    assert "CG006" in _codes(graph)
    assert any(item.subject == "docker-pytest" and item.normalized == "pytest" for item in graph.evidence)


def test_docker_malformed_json_argv_does_not_fabricate_command(tmp_path: Path) -> None:
    graph = diagnose(_repository(tmp_path, {"Dockerfile": 'FROM python:3.12\nCMD ["pytest",]\n'}))

    assert not any(item.kind == "command" for item in graph.evidence)


@pytest.mark.parametrize("source", ["src/", "./src", "src/*.py", "s*/", ".env.example"])
def test_docker_copy_accepts_indexed_directories_and_globs(tmp_path: Path, source: str) -> None:
    graph = diagnose(_repository(tmp_path, {
        "Dockerfile": f"FROM python:3.12\nCOPY {source} /app/\n",
        "src/app.py": "print('ok')\n", ".env.example": "EXAMPLE=1\n",
    }))

    assert "CG014" not in _codes(graph)
    assert [edge.status for edge in graph.edges if edge.relation == "references"] == ["satisfied"]


def test_docker_copy_json_multiple_sources_and_external_stages(tmp_path: Path) -> None:
    graph = diagnose(_repository(tmp_path, {
        "Dockerfile": 'FROM python:3.12\nCOPY ["src", "missing.txt", "/app/"]\nCOPY --from=builder /generated /app/\n',
        "src/app.py": "pass\n",
    }))

    assert sorted(edge.status for edge in graph.edges if edge.relation == "references") == ["missing", "satisfied", "unknown"]
    assert len([item for item in graph.diagnostics if item.code == "CG014"]) == 1


@pytest.mark.parametrize("command", ["-r requirements.txt", "-rrequirements.txt", "--requirement=./requirements.txt", '-r "requirements/dev.txt"'])
def test_install_requirement_references_resolve_readable_files(tmp_path: Path, command: str) -> None:
    graph = diagnose(_repository(tmp_path, {
        "Makefile": f"install:\n\tpython -m pip install {command}\n",
        "requirements.txt": "pytest>=8\n", "requirements/dev.txt": "pytest>=8\n",
    }))

    assert "CG007" not in _codes(graph)
    assert [edge.status for edge in graph.edges if edge.relation == "references"] == ["satisfied"]


def test_missing_requirement_reference_remains_diagnostic(tmp_path: Path) -> None:
    graph = diagnose(_repository(tmp_path, {"Makefile": "install:\n\tpip install -r missing.txt\n"}))

    assert "CG007" in _codes(graph)
    assert [edge.status for edge in graph.edges] == ["missing"]


def test_unreadable_requirement_reference_is_unknown(tmp_path: Path) -> None:
    graph = diagnose(_repository(tmp_path, {
        "Makefile": "install:\n\tpip install -r requirements.txt\n", "requirements.txt": "\x00binary",
    }))

    assert "CG007" not in _codes(graph)
    assert [edge.status for edge in graph.edges if edge.relation == "references"] == ["unknown"]


def test_workflow_collects_only_run_commands_including_inline_steps(tmp_path: Path) -> None:
    graph = diagnose(_repository(tmp_path, {
        "Makefile": "test:\n\tpython -m pytest\n",
        ".github/workflows/ci.yml": """jobs:
  test:
    env:
      run: npm test
      DESCRIPTION: |
        run: npm test
        npm test
    strategy:
      matrix:
        labels:
          - npm test
    steps:
      - run: python -m pytest -q
      - run: |
          pytest --verbose
        env:
          run: npm test
          DESCRIPTION: |
            npm test
      - run: pip install -r requirements.txt
""",
        "requirements.txt": "pytest>=8\n",
    }))

    commands = [item for item in graph.evidence if dict(item.attributes).get("source") == "workflow" and item.kind == "command"]
    assert sorted(item.normalized for item in commands) == ["install", "pytest", "pytest"]
    assert not ({"CG005", "CG007"} & _codes(graph))


@pytest.mark.parametrize("ecosystem", ["python", "node"])
def test_dynamic_ci_runtime_matrix_is_unknown(tmp_path: Path, ecosystem: str) -> None:
    files = {".python-version": "3.12", "pyproject.toml": "[project]\n"} if ecosystem == "python" else {".nvmrc": "20", "package.json": "{}"}
    files[".github/workflows/ci.yml"] = f"""jobs:
  test:
    strategy:
      matrix:
        {ecosystem}-version: ['3.11', '3.12', '20', '22']
    steps:
      - uses: actions/setup-{ecosystem}@v5
        with:
          {ecosystem}-version: ${{{{ matrix.{ecosystem}-version || '3.12' }}}}
"""
    graph = diagnose(_repository(tmp_path, files))

    assert [edge.status for edge in graph.edges if edge.relation == "matches"] == ["unknown", "unknown"]
    assert "CG011" not in _codes(graph)


@pytest.mark.parametrize(
    ("manifest", "lockfile", "contents"),
    [
        ("package.json", "package-lock.json", json.dumps({"lockfileVersion": 3, "packages": {"node_modules/react-dom": {"version": "1.0"}}, "description": "react"})),
        ("pyproject.toml", "uv.lock", 'version=1\n# react is mentioned\n[[package]]\nname="react-dom"\nversion="1.0"\n'),
        ("pyproject.toml", "requirements.txt", "react-dom==1.0\n# react\n"),
    ],
)
def test_lock_coverage_uses_structured_names_not_substrings(
    tmp_path: Path, manifest: str, lockfile: str, contents: str,
) -> None:
    declaration = '{"dependencies":{"react":"1.0"}}' if manifest == "package.json" else '[project]\ndependencies=["react==1.0"]\n'
    graph = diagnose(_repository(tmp_path, {manifest: declaration, lockfile: contents}))

    assert "CG009" in _codes(graph)
    assert any(edge.status == "mismatch" and edge.expected == "react" for edge in graph.edges if edge.relation == "covers")


@pytest.mark.parametrize(
    ("manifest", "lockfile", "contents"),
    [
        ("package.json", "package-lock.json", '{"react": true}'),
        ("package.json", "package-lock.json", '{"lockfileVersion":99,"dependencies":{"react":{"version":"1.0"}}}'),
        ("package.json", "yarn.lock", "# react@1.0\n"),
        ("package.json", "pnpm-lock.yaml", "react: invalid\n"),
        ("pyproject.toml", "uv.lock", '# react\nnot valid TOML'),
        ("pyproject.toml", "Pipfile.lock", '{"react":"1.0"}'),
    ],
)
def test_malformed_or_unsupported_lock_coverage_is_unknown(
    tmp_path: Path, manifest: str, lockfile: str, contents: str,
) -> None:
    declaration = '{"dependencies":{"react":"1.0"}}' if manifest == "package.json" else '[project]\ndependencies=["react==1.0"]\n'
    graph = diagnose(_repository(tmp_path, {manifest: declaration, lockfile: contents}))

    assert [edge.status for edge in graph.edges if edge.relation == "covers"] == ["unknown"]
    assert "CG009" not in _codes(graph)


@pytest.mark.parametrize(
    ("manifest", "lockfile", "contents"),
    [
        ("package.json", "package-lock.json", '{"lockfileVersion":3,"packages":{"node_modules/react":{"version":"1.0"}}}'),
        ("pyproject.toml", "uv.lock", 'version=1\n[[package]]\nname="react"\nversion="1.0"\n'),
        ("pyproject.toml", "requirements.txt", "react==1.0\n"),
    ],
)
def test_valid_structured_locks_cover_declared_names(
    tmp_path: Path, manifest: str, lockfile: str, contents: str,
) -> None:
    declaration = '{"dependencies":{"react":"1.0"}}' if manifest == "package.json" else '[project]\ndependencies=["react==1.0"]\n'
    graph = diagnose(_repository(tmp_path, {manifest: declaration, lockfile: contents}))

    assert any(edge.relation == "covers" for edge in graph.edges)
    assert all(edge.status == "satisfied" for edge in graph.edges if edge.relation == "covers")
    assert "CG009" not in _codes(graph)


def test_python_dependency_names_are_case_insensitive(tmp_path: Path) -> None:
    graph = diagnose(_repository(tmp_path, {
        "pyproject.toml": '[project]\ndependencies=["Requests>=2"]\n',
        "uv.lock": 'version=1\n[[package]]\nname="requests"\nversion="2.32.0"\n',
    }))

    assert [edge.status for edge in graph.edges if edge.relation == "covers"] == ["satisfied"]
    assert "CG009" not in _codes(graph)


def test_requirements_coverage_can_be_split_between_files(tmp_path: Path) -> None:
    graph = diagnose(_repository(tmp_path, {
        "pyproject.toml": '[project]\ndependencies=["requests", "pytest"]\n',
        "requirements.txt": "requests==2.32.0\n", "requirements-dev.txt": "pytest==8.0\n",
    }))

    assert all(edge.status == "satisfied" for edge in graph.edges if edge.relation == "covers")
    assert "CG009" not in _codes(graph)


def test_lock_selection_reuses_check_precedence(tmp_path: Path) -> None:
    graph = diagnose(_repository(tmp_path, {
        "package.json": '{"dependencies":{"react":"1.0"}}',
        "package-lock.json": '{"lockfileVersion":3,"packages":{"node_modules/react-dom":{"version":"1.0"}}}',
        "yarn.lock": 'react@1.0:\n  version: "1.0"\n',
    }))

    assert [edge.status for edge in graph.edges if edge.relation == "covers"] == ["mismatch"]
    assert "CG009" in _codes(graph)
