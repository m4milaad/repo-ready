from __future__ import annotations

import hashlib
import json
from pathlib import Path

from repo_ready.showcase import build_showcase


def _repository(root: Path, files: dict[str, str]) -> Path:
    repository = root / "repository"
    repository.mkdir()
    for relative, contents in files.items():
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    return repository


def _analysis_fingerprint(analysis: dict[str, object]) -> str:
    canonical = json.dumps(analysis, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _scenario(payload: dict[str, object], identifier: str) -> dict[str, object]:
    scenarios = payload["analysis"]["scenarios"]
    assert isinstance(scenarios, list)
    return next(item for item in scenarios if item["id"] == identifier)


def test_showcase_payload_is_deterministic_and_json_serializable(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        {
            "README.md": "# Example\n\n## Setup\nInstall.\n\n## Run\nRun.\n\n## Test\nmake test\n",
            "Makefile": "test:\n\tpython -m pytest -q\n",
            ".python-version": "3.12\n",
            "pyproject.toml": "[project]\nname='example'\nversion='1.0'\n",
        },
    )

    first = build_showcase(repository)
    second = build_showcase(repository)

    assert first == second
    assert first["schema_version"] == 1
    assert first["analysis"]["mode"] == "static"
    assert first["receipt"]["fingerprint"] == _analysis_fingerprint(first["analysis"])
    json.dumps(first, ensure_ascii=True, sort_keys=True, allow_nan=False)
    assert first["analysis"]["repository"]["scope"] == "filesystem"
    assert first["analysis"]["repository"]["complete"] is True


def test_scenarios_project_contract_breakage_without_writing(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        {
            "README.md": "# Example\n\n## Setup\nInstall.\n\n## Run\nRun.\n\n## Test\nmake test\n",
            "Makefile": "test:\n\tpython -m pytest -q\ninstall:\n\tpython -m pip install -r requirements.txt\n",
            ".python-version": "3.12\n",
            "pyproject.toml": "[project]\nname='example'\nversion='1.0'\ndependencies=['requests']\n",
            "requirements.txt": "requests==2.31.0\n",
            ".github/workflows/ci.yml": (
                "name: CI\n"
                "on: [push]\n"
                "jobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
                "      - uses: actions/setup-python@v5\n"
                "        with:\n"
                "          python-version: '3.12'\n"
                "      - run: python -m pytest -q\n"
            ),
        },
    )
    before = {path.relative_to(repository).as_posix(): path.read_bytes() for path in repository.rglob("*") if path.is_file()}

    payload = build_showcase(repository)

    runtime = _scenario(payload, "remove-runtime-pin")
    drift = _scenario(payload, "ci-runtime-drift")
    dependency = _scenario(payload, "remove-referenced-dependency-file")
    test_entry = _scenario(payload, "remove-test-entry-point")
    assert all(item["status"] == "available" for item in (runtime, drift, dependency, test_entry))
    assert runtime["score_delta"] < 0
    assert drift["new_diagnostics"]
    assert dependency["score_delta"] < 0
    assert test_entry["score_delta"] < 0
    assert runtime["comparison"]["regressed"] is True
    assert runtime["comparison"]["score_delta"] == runtime["score_delta"]
    assert runtime["affected_files"] == [".python-version"]
    assert dependency["affected_files"] == ["requirements.txt"]
    assert test_entry["affected_files"] == ["Makefile"]
    for item in (runtime, drift, dependency, test_entry):
        assert item["report"] is not None
        assert item["graph"] is not None
        assert item["repair"] is not None

    after = {path.relative_to(repository).as_posix(): path.read_bytes() for path in repository.rglob("*") if path.is_file()}
    assert after == before


def test_repair_preview_uses_create_only_overlay_and_is_reviewable(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        {"pyproject.toml": "[project]\nname='example'\nversion='1.0'\n"},
    )
    before = {path.relative_to(repository).as_posix(): path.read_bytes() for path in repository.rglob("*") if path.is_file()}

    payload = build_showcase(repository, scenarios=False)
    repair = payload["analysis"]["baseline"]["repair"]

    assert repair["score_after"] >= repair["score_before"]
    assert repair["score_delta"] >= 0
    assert any(item["action"] == "create" for item in repair["operations"])
    assert "+++ README.md" in repair["patch"]
    assert repair["manual"]
    assert {path.relative_to(repository).as_posix(): path.read_bytes() for path in repository.rglob("*") if path.is_file()} == before


def test_unavailable_scenarios_explain_missing_contracts(tmp_path: Path) -> None:
    repository = _repository(tmp_path, {"README.md": "# No project\n"})

    payload = build_showcase(repository)

    for scenario in payload["analysis"]["scenarios"]:
        assert scenario["status"] == "unavailable"
        assert scenario["reason"]
        assert scenario["report"] is None
        assert scenario["graph"] is None
        assert scenario["score_delta"] is None
        assert scenario["repair"] is None
