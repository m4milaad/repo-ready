from __future__ import annotations

import json
from pathlib import Path

import pytest

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
FINDING_KEYS = ("file", "line", "detail")


def _repository(root: Path, name: str, files: dict[str, str | bytes]) -> Path:
    repository = root / name
    repository.mkdir()
    for relative, contents in files.items():
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(contents, bytes):
            path.write_bytes(contents)
        else:
            path.write_text(contents, encoding="utf-8")
    return repository


def _check(repository: Path, rule_id: str) -> dict[str, object]:
    value = report(repository)
    assert tuple(value) == REPORT_KEYS
    assert value["max"] == 100
    assert isinstance(value["checks"], list)
    assert len(value["checks"]) == 13
    for item in value["checks"]:
        assert tuple(item) == CHECK_KEYS
        assert isinstance(item["findings"], list)
        for finding in item["findings"]:
            assert tuple(finding) == FINDING_KEYS
    return next(item for item in value["checks"] if item["id"] == rule_id)


def _finding(file: str, detail: str, line: int | None = None) -> list[dict[str, object]]:
    return [{"file": file, "line": line, "detail": detail}]


def _assert_command(repository: Path, expected: str) -> None:
    first = _check(repository, "RR09")
    second = _check(repository, "RR09")

    assert first["status"] == second["status"] == "pass"
    assert first["earned"] == second["earned"] == 12
    assert first["message"] == second["message"] == f"Test command: {expected}"
    assert first["findings"] == second["findings"] == []


@pytest.mark.parametrize(
    ("contents", "name"),
    (
        ("\n", "empty"),
        (b"FROM python:3.12\n\x00CMD [\"python\"]\n", "binary"),
        (b"FROM python:3.12\n" + b"x" * (1024 * 1024), "oversized"),
    ),
)
def test_rr08_rejects_empty_binary_and_oversized_dockerfiles(
    contents: str | bytes,
    name: str,
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, name, {"Dockerfile": contents})

    item = _check(repository, "RR08")

    assert item["status"] == "fail"
    assert item["earned"] == 0
    assert item["findings"] == _finding(
        "Dockerfile",
        "Root Dockerfile or Containerfile is empty or unreadable.",
    )


def test_rr08_ignores_commented_instructions(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        "commented-dockerfile",
        {
            "Dockerfile": (
                "  # FROM python:latest\n"
                "# CMD [\"echo\", \"not a command\"]\n"
            )
        },
    )

    item = _check(repository, "RR08")

    assert item["status"] == "partial"
    assert item["earned"] == 6
    assert item["findings"] == [
        {
            "file": "Dockerfile",
            "line": None,
            "detail": "Dockerfile has no CMD or ENTRYPOINT instruction.",
        },
        {
            "file": "Dockerfile",
            "line": None,
            "detail": "Dockerfile has no FROM instruction.",
        },
    ]


def test_rr08_reports_each_unpinned_multistage_from_at_its_line(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        "multistage-dockerfile",
        {
            "Dockerfile": (
                "FROM python:3.12 AS build\n"
                "RUN python -m compileall .\n"
                "FROM python:latest AS runtime\n"
                "CMD [\"python\", \"-m\", \"app\"]\n"
            )
        },
    )

    item = _check(repository, "RR08")

    assert item["status"] == "partial"
    assert item["earned"] == 9
    assert item["findings"] == _finding(
        "Dockerfile",
        "FROM must use an explicit non-latest tag or digest.",
        line=3,
    )


def test_rr08_accepts_digest_and_platform_flags(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        "pinned-platform-dockerfile",
        {
            "Dockerfile": (
                "FROM --platform=linux/amd64 python@sha256:0123456789abcdef AS build\n"
                "FROM --platform=$TARGETPLATFORM python:3.12 AS runtime\n"
                "ENTRYPOINT [\"python\", \"-m\", \"app\"]\n"
            )
        },
    )

    item = _check(repository, "RR08")

    assert item["status"] == "pass"
    assert item["earned"] == 12
    assert item["findings"] == []


@pytest.mark.parametrize("instruction", ("CMD", "ENTRYPOINT"))
def test_rr08_requires_a_command_in_the_final_stage(instruction: str, tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        f"missing-final-{instruction.lower()}",
        {
            "Dockerfile": (
                "FROM python:3.12 AS build\n"
                f"{instruction} [\"build\"]\n"
                "FROM python:3.12 AS runtime\n"
            )
        },
    )

    item = _check(repository, "RR08")

    assert item["status"] == "partial"
    assert item["earned"] == 9
    assert item["findings"] == _finding(
        "Dockerfile",
        "Dockerfile has no CMD or ENTRYPOINT instruction.",
    )


def test_rr08_prefers_dockerfile_over_containerfile(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        "dockerfile-priority",
        {
            "Dockerfile": "FROM python:latest\nCMD [\"python\"]\n",
            "Containerfile": "FROM python:3.12\nCMD [\"python\"]\n",
        },
    )

    item = _check(repository, "RR08")

    assert item["status"] == "partial"
    assert item["earned"] == 9
    assert item["findings"] == _finding(
        "Dockerfile",
        "FROM must use an explicit non-latest tag or digest.",
        line=1,
    )


def test_rr08_uses_containerfile_when_dockerfile_is_absent(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        "containerfile-fallback",
        {"Containerfile": "FROM python:3.12\nCMD [\"python\"]\n"},
    )

    item = _check(repository, "RR08")

    assert item["status"] == "pass"
    assert item["earned"] == 12
    assert item["findings"] == []


def test_rr08_rejects_empty_tag_and_empty_json_command(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        "empty-tag",
        {"Dockerfile": "FROM python:\nCMD []\n"},
    )

    item = _check(repository, "RR08")

    assert item["status"] == "partial"
    assert item["earned"] == 6
    assert len(item["findings"]) == 2


def test_rr08_handles_continued_from_lines_and_heredoc_content(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        "continued-dockerfile",
        {
            "Dockerfile": (
                "FROM --platform=$BUILDPLATFORM \\\n    python:3.12 AS build\n"
                "RUN <<EOF\n"
                "FROM python:latest\n"
                "EOF\n"
                "FROM --platform=$TARGETPLATFORM \\\n    python:3.12 AS runtime\n"
                "CMD [\"python\"]\n"
            )
        },
    )

    item = _check(repository, "RR08")

    assert item["status"] == "pass"
    assert item["earned"] == 12
    assert item["findings"] == []


def test_rr09_prioritizes_makefile_and_reports_the_selected_file(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        "makefile-priority",
        {
            "Makefile": ".PHONY: test\ntest:\n\t@python -m pytest -q\n",
            "package.json": json.dumps(
                {"name": "fixture", "scripts": {"test": "npm run test:unit"}}
            )
            + "\n",
        },
    )

    _assert_command(repository, "make test (Makefile: python -m pytest -q)")


@pytest.mark.parametrize("target", ("test: unit lint\n", "test: | FORCE\n", "test: ;\n"))
def test_rr09_does_not_treat_make_prerequisites_as_a_recipe(target: str, tmp_path: Path) -> None:
    repository = _repository(tmp_path, "make-prerequisites", {"Makefile": target})

    item = _check(repository, "RR09")

    assert item["status"] == "fail"
    assert item["earned"] == 0


def test_rr09_prioritizes_a_valid_package_test_script_over_config(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        "package-script-priority",
        {
            "package.json": json.dumps(
                {"name": "fixture", "scripts": {"test": "npm run test:unit"}}
            )
            + "\n",
            "pytest.ini": "[pytest]\naddopts = -q\n",
        },
    )

    _assert_command(repository, "npm test (package.json scripts.test: npm run test:unit)")


def test_rr09_rejects_package_placeholder_and_falls_back_to_config(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        "package-placeholder",
        {
            "package.json": json.dumps(
                {
                    "name": "fixture",
                    "scripts": {"test": "echo \\\"Error: no test specified\\\" && exit 1"},
                }
            )
            + "\n",
            "tox.ini": "[tox]\nenvlist = py312\n",
        },
    )

    _assert_command(repository, "tox (tox.ini)")


@pytest.mark.parametrize(
    ("files", "expected"),
    (
        ({"pytest.ini": "[pytest]\n"}, "pytest (pytest.ini)"),
        ({"tox.ini": "[tox]\n"}, "tox (tox.ini)"),
        ({"noxfile.py": "def test(session):\n    pass\n"}, "nox (noxfile.py)"),
        ({"setup.cfg": "[tool:pytest]\naddopts = -q\n"}, "pytest (setup.cfg [tool:pytest])"),
        ({"pyproject.toml": "[tool.tox]\nenvlist = ['py312']\n"}, "tox (pyproject.toml [tool.tox])"),
        ({"pyproject.toml": "[tool.nox]\nsessions = ['tests']\n"}, "nox (pyproject.toml [tool.nox])"),
    ),
)
def test_rr09_accepts_supported_pytest_tox_and_nox_configs(
    files: dict[str, str],
    expected: str,
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path, "config", files)

    _assert_command(repository, expected)


def test_rr08_and_rr09_keep_the_complete_report_schema(tmp_path: Path) -> None:
    repository = _repository(
        tmp_path,
        "schema",
        {
            "Dockerfile": "FROM python:3.12\nCMD [\"python\"]\n",
            "Makefile": ".PHONY: test\ntest:\n\tpython -m pytest -q\n",
        },
    )

    value = report(repository)

    assert tuple(value) == REPORT_KEYS
    assert tuple(value["checks"][7]) == CHECK_KEYS
    assert tuple(value["checks"][8]) == CHECK_KEYS
    assert value["checks"][7]["id"] == "RR08"
    assert value["checks"][8]["id"] == "RR09"
