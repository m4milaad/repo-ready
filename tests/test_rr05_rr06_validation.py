from __future__ import annotations

import json
from pathlib import Path

import pytest

from repo_ready.checks import _manifest_findings
from repo_ready.context import RepoContext
from repo_ready.scoring import report


REPORT_KEYS = ("tool", "version", "path", "ecosystems", "score", "max", "grade", "checks")
CHECK_KEYS = (
    "id",
    "title",
    "weight",
    "earned",
    "status",
    "message",
    "fix_hint",
    "fixable",
    "findings",
)


def _repository(root: Path, name: str, files: dict[str, str]) -> Path:
    repository = root / name
    repository.mkdir()
    for relative, content in files.items():
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return repository


def _check(repository: Path, rule_id: str) -> dict[str, object]:
    value = report(repository)
    assert tuple(value) == REPORT_KEYS
    assert value["max"] == 100
    assert isinstance(value["checks"], list)
    assert len(value["checks"]) == 13
    for item in value["checks"]:
        assert tuple(item) == CHECK_KEYS
    return next(item for item in value["checks"] if item["id"] == rule_id)


def _finding(file: str, detail: str) -> list[dict[str, object]]:
    return [{"file": file, "line": None, "detail": detail}]


VALID_RR05_MANIFESTS = (
    pytest.param(
        "pyproject.toml",
        '[project]\nname = "fixture"\nversion = "1.0.0"\ndependencies = ["requests>=2"]\n',
        id="pyproject.toml",
    ),
    pytest.param(
        "setup.cfg",
        "[metadata]\nname = fixture\nversion = 1.0.0\n[options]\ninstall_requires =\n    requests>=2\n",
        id="setup.cfg",
    ),
    pytest.param(
        "setup.py",
        "from setuptools import setup\n"
        'NAME = "fixture"\n'
        'VERSION = "1.0.0"\n'
        'INSTALL_REQUIRES = ["requests>=2"]\n'
        "setup(name=NAME, version=VERSION, install_requires=INSTALL_REQUIRES)\n",
        id="setup.py-static-ast",
    ),
    pytest.param("Pipfile", '[packages]\nrequests = "*"\n', id="Pipfile"),
    pytest.param(
        "package.json",
        json.dumps({"name": "fixture", "version": "1.0.0", "dependencies": {"left-pad": "^1.3.0"}}) + "\n",
        id="package.json",
    ),
)


@pytest.mark.parametrize(("manifest", "text"), VALID_RR05_MANIFESTS)
def test_manifest_validation_accepts_valid_project_manifests(manifest: str, text: str, tmp_path: Path) -> None:
    repository = _repository(tmp_path, "valid", {manifest: text})

    assert _manifest_findings(RepoContext(repository), manifest) == []


MALFORMED_RR05_MANIFESTS = (
    pytest.param(
        "pyproject.toml",
        "[project\nname = \"fixture\"\nversion = \"1.0.0\"\n",
        _finding("pyproject.toml", "pyproject.toml must contain valid TOML."),
        id="pyproject.toml-syntax-error",
    ),
    pytest.param(
        "pyproject.toml",
        '[project]\nname = "fixture"\nversion = 1\n',
        _finding("pyproject.toml", "pyproject.toml [project] needs a non-empty version or dynamic = [\"version\"]."),
        id="pyproject.toml-semantic-error",
    ),
    pytest.param(
        "setup.cfg",
        "[metadata]\nname = fixture\n[metadata]\nversion = 1.0.0\n",
        _finding("setup.cfg", "setup.cfg is not valid INI configuration: DuplicateSectionError."),
        id="setup.cfg-duplicate-section",
    ),
    pytest.param(
        "setup.py",
        "from setuptools import setup\n"
        'def version():\n    return "1.0.0"\n'
        'setup(name="fixture", version=version())\n',
        _finding("setup.py", "setup.py setup() needs a statically declared non-empty version= value."),
        id="setup.py-dynamic-ast-value",
    ),
    pytest.param(
        "Pipfile",
        "[packages\nrequests = \"*\"\n",
        _finding("Pipfile", "Pipfile must contain valid TOML."),
        id="Pipfile-malformed-toml",
    ),
    pytest.param(
        "package.json",
        '{"name": 42}\n',
        _finding("package.json", "package.json needs a non-empty string name."),
        id="package.json-semantic-error",
    ),
)


@pytest.mark.parametrize(("manifest", "text", "findings"), MALFORMED_RR05_MANIFESTS)
def test_manifest_validation_rejects_malformed_project_manifests(
    manifest: str,
    text: str,
    findings: list[dict[str, object]],
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "malformed", {manifest: text})

    assert [
        finding.as_dict()
        for finding in _manifest_findings(RepoContext(repository), manifest)
    ] == findings


@pytest.mark.parametrize(
    "relative",
    (".github/PULL_REQUEST_TEMPLATE.md", "PULL_REQUEST_TEMPLATE.md", "docs/PULL_REQUEST_TEMPLATE.md"),
)
def test_rr05_accepts_supported_pull_request_template_paths(relative: str, tmp_path: Path) -> None:
    repository = _repository(tmp_path, "pr-template", {relative: "## Summary\n"})

    item = _check(repository, "RR05")

    assert item["status"] == "pass"
    assert item["earned"] == 4
    assert item["findings"] == []


def test_rr05_rejects_manifest_without_a_pull_request_template(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        "missing-pr-template",
        {"pyproject.toml": '[project]\nname = "fixture"\nversion = "1.0.0"\n'},
    )

    item = _check(repository, "RR05")

    assert item["status"] == "fail"
    assert item["earned"] == 0
    assert item["findings"] == _finding(
        ".github/PULL_REQUEST_TEMPLATE.md",
        "No pull-request template is present.",
    )


PYPROJECT_METADATA = '[project]\nname = "fixture"\nversion = "1.0.0"\n'
PYPROJECT_DEPENDENCIES = PYPROJECT_METADATA + 'dependencies = ["requests>=2"]\n'
NODE_DEPENDENCIES = json.dumps(
    {"name": "fixture", "version": "1.0.0", "dependencies": {"left-pad": "^1.3.0"}}
) + "\n"


RR06_LOCK_CASES = (
    pytest.param(
        {"pyproject.toml": PYPROJECT_METADATA},
        "requirements.txt",
        "requests==2.32.3\n",
        "requests>=2.0\n",
        "every requirements dependency must be pinned with == and the file must be non-empty",
        id="requirements.txt",
    ),
    pytest.param(
        {"pyproject.toml": PYPROJECT_METADATA},
        "requirements-dev.txt",
        "requests==2.32.3\n",
        "requests>=2.0\n",
        "every requirements dependency must be pinned with == and the file must be non-empty",
        id="requirements-dev.txt",
    ),
    pytest.param(
        {"pyproject.toml": PYPROJECT_DEPENDENCIES},
        "uv.lock",
        'version = 1\n\n[[package]]\nname = "requests"\nversion = "2.32.3"\n',
        'version = "one"\n\n[[package]]\nname = "requests"\nversion = "2.32.3"\n',
        "uv.lock needs an integer version field.",
        id="uv.lock",
    ),
    pytest.param(
        {
            "pyproject.toml": "[tool.poetry]\nname = \"fixture\"\nversion = \"1.0.0\"\n\n[tool.poetry.dependencies]\nrequests = \"^2.0\"\n",
        },
        "poetry.lock",
        '[metadata]\nlock-version = "2.0"\n\n[[package]]\nname = "requests"\nversion = "2.32.3"\n',
        '[[package]]\nname = "requests"\nversion = "2.32.3"\n',
        "poetry.lock needs a [metadata] table with lock-version.",
        id="poetry.lock",
    ),
    pytest.param(
        {"pyproject.toml": PYPROJECT_DEPENDENCIES},
        "pdm.lock",
        'lock_version = "4.4"\n\n[[package]]\nname = "requests"\nversion = "2.32.3"\n',
        '[[package]]\nname = "requests"\nversion = "2.32.3"\n',
        "pdm.lock needs a non-empty lock_version field.",
        id="pdm.lock",
    ),
    pytest.param(
        {"Pipfile": '[packages]\nrequests = "*"\n'},
        "Pipfile.lock",
        json.dumps({"_meta": {"hash": {"sha256": "fixture"}}, "default": {"requests": {"version": "==2.32.3"}}}) + "\n",
        '{"_meta": {}, "default": {}}\n',
        "Pipfile.lock must be valid JSON with non-empty _meta, default, or develop records.",
        id="Pipfile.lock",
    ),
    pytest.param(
        {"package.json": NODE_DEPENDENCIES},
        "package-lock.json",
        json.dumps(
            {
                "name": "fixture",
                "version": "1.0.0",
                "lockfileVersion": 3,
                "packages": {"": {"name": "fixture", "version": "1.0.0"}, "node_modules/left-pad": {"version": "1.3.0"}},
            }
        )
        + "\n",
        '{"lockfileVersion": 3, "packages": {}}\n',
        "package-lock.json must be valid JSON with lockfileVersion 1, 2, or 3 and dependency records.",
        id="package-lock.json",
    ),
    pytest.param(
        {"package.json": NODE_DEPENDENCIES},
        "npm-shrinkwrap.json",
        json.dumps(
            {
                "name": "fixture",
                "version": "1.0.0",
                "lockfileVersion": 3,
                "packages": {"": {"name": "fixture", "version": "1.0.0"}, "node_modules/left-pad": {"version": "1.3.0"}},
            }
        )
        + "\n",
        '{"lockfileVersion": 3, "packages": {}}\n',
        "npm-shrinkwrap.json must be valid JSON with lockfileVersion 1, 2, or 3 and dependency records.",
        id="npm-shrinkwrap.json",
    ),
    pytest.param(
        {"package.json": NODE_DEPENDENCIES},
        "yarn.lock",
        '__metadata:\n  version: 6\n\n"left-pad@^1.3.0":\n  version: 1.3.0\n  resolution: "left-pad@npm:1.3.0"\n',
        '"left-pad@^1.3.0":\n',
        "yarn.lock needs at least one package selector with a version or resolution.",
        id="yarn.lock",
    ),
    pytest.param(
        {"package.json": NODE_DEPENDENCIES},
        "pnpm-lock.yaml",
        'lockfileVersion: 9.0\npackages:\n  left-pad@1.3.0:\n    resolution:\n      integrity: sha512-fixture\n',
        'lockfileVersion: 9.0\npackages:\n',
        "pnpm-lock.yaml needs a non-empty packages, snapshots, dependencies, or importers section.",
        id="pnpm-lock.yaml",
    ),
)


@pytest.mark.parametrize(
    ("manifest_files", "lockfile", "valid_lock", "malformed_lock", "malformed_detail"),
    RR06_LOCK_CASES,
)
def test_rr06_accepts_valid_and_rejects_malformed_lockfiles(
    manifest_files: dict[str, str],
    lockfile: str,
    valid_lock: str,
    malformed_lock: str,
    malformed_detail: str,
    tmp_path: Path,
) -> None:
    valid_repository = _repository(tmp_path, "valid", {**manifest_files, lockfile: valid_lock})
    malformed_repository = _repository(tmp_path, "malformed", {**manifest_files, lockfile: malformed_lock})

    valid_rr06 = _check(valid_repository, "RR06")
    assert valid_rr06["status"] == "pass"
    assert valid_rr06["earned"] == 10
    assert valid_rr06["findings"] == []

    malformed_rr06 = _check(malformed_repository, "RR06")
    assert malformed_rr06["status"] == "fail"
    assert malformed_rr06["earned"] == 0
    assert malformed_rr06["findings"] == _finding(lockfile, malformed_detail)


def test_rr06_rejects_an_npm_lockfile_that_omits_a_declared_dependency(tmp_path: Path) -> None:
    manifest = json.dumps(
        {"name": "fixture", "version": "1.0.0", "dependencies": {"left-pad": "^1.3.0"}}
    ) + "\n"
    lockfile = json.dumps(
        {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "fixture", "version": "1.0.0"},
                "node_modules/other-package": {"version": "1.0.0"},
            },
        }
    ) + "\n"
    repository = _repository(tmp_path, "missing-coverage", {"package.json": manifest, "package-lock.json": lockfile})

    rr06 = _check(repository, "RR06")
    assert rr06["status"] == "fail"
    assert rr06["earned"] == 0
    assert rr06["findings"] == _finding("package-lock.json", "package-lock.json does not cover declared dependencies: left-pad.")


@pytest.mark.parametrize(
    ("manifest", "text"),
    (
        ("pyproject.toml", PYPROJECT_METADATA),
        ("package.json", json.dumps({"name": "fixture", "version": "1.0.0"}) + "\n"),
    ),
)
def test_rr06_marks_dependency_free_projects_na(manifest: str, text: str, tmp_path: Path) -> None:
    repository = _repository(tmp_path, "dependency-free", {manifest: text})

    rr06 = _check(repository, "RR06")
    assert rr06["status"] == "na"
    assert rr06["earned"] == 10
    assert rr06["findings"] == []


@pytest.mark.parametrize(
    ("lockfile", "contents"),
    (
        ("package-lock.json", '{"lockfileVersion":3,"packages":{"node_modules/left-pad":{}}}\n'),
        ("yarn.lock", '"left-pad@^1.3.0":\n  version:\n'),
        ("pnpm-lock.yaml", "lockfileVersion: 9.0\npackages:\n  left-pad@1.3.0:\n"),
    ),
)
def test_rr06_rejects_empty_dependency_records(
    lockfile: str,
    contents: str,
    tmp_path: Path,
) -> None:
    repository = _repository(
        tmp_path,
        "empty-record",
        {"package.json": NODE_DEPENDENCIES, lockfile: contents},
    )

    item = _check(repository, "RR06")

    assert item["status"] == "fail"
    assert item["earned"] == 0
    assert item["findings"]


def test_rr06_requires_declared_dependency_coverage_in_toml_and_yarn_locks(tmp_path: Path) -> None:
    uv_repository = _repository(
        tmp_path,
        "uv-missing",
        {
            "pyproject.toml": PYPROJECT_DEPENDENCIES,
            "uv.lock": 'version = 1\n\n[[package]]\nname = "other"\nversion = "1.0.0"\n',
        },
    )
    yarn_repository = _repository(
        tmp_path,
        "yarn-missing",
        {
            "package.json": NODE_DEPENDENCIES,
            "yarn.lock": '"other@^1.0.0":\n  version: 1.0.0\n',
        },
    )

    assert _check(uv_repository, "RR06")["status"] == "fail"
    assert _check(yarn_repository, "RR06")["status"] == "fail"


def test_rr06_prefers_an_explicit_lockfile_over_requirements_manifest(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        "explicit-lock",
        {
            "pyproject.toml": PYPROJECT_DEPENDENCIES,
            "requirements.txt": "requests>=2\n",
            "uv.lock": 'version = 1\n\n[[package]]\nname = "requests"\nversion = "2.32.3"\n',
        },
    )

    item = _check(repository, "RR06")

    assert item["status"] == "pass"
    assert item["earned"] == 10
