"""Pure, local filesystem checks and readiness scoring."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Mapping

from .detector import detect_ecosystems


# This is the one fixed scoring table.  Its values intentionally add to 100.
CHECK_WEIGHTS: dict[str, int] = {
    "readme_sections": 15,
    "license": 10,
    "contributing": 10,
    "templates": 10,
    "lockfile": 10,
    "pinned_runtime": 10,
    "dockerfile": 10,
    "test_command": 10,
    "env_example": 5,
    "no_hardcoded_paths": 10,
}

Check = Callable[[Path], bool]


def _nonempty_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _first_existing_file(root: Path, names: tuple[str, ...]) -> Path | None:
    for name in names:
        candidate = root / name
        if _nonempty_file(candidate):
            return candidate
    return None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None


def _read_json(path: Path) -> object | None:
    text = _read_text(path)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _readme_headings(text: str) -> set[str]:
    headings: set[str] = set()
    for line in text.splitlines():
        match = re.match(r"^#{2,6}\s+(.+?)\s*#*\s*$", line)
        if match:
            headings.add(match.group(1).casefold())
    return headings


def readme_sections(root: Path) -> bool:
    """Check that README documents the core setup, use, and rebuild flow."""

    readme = _first_existing_file(root, ("README.md", "README.rst", "README.txt"))
    if readme is None:
        return False
    text = _read_text(readme)
    if text is None:
        return False
    headings = _readme_headings(text)
    required = {"requirements", "setup", "run", "test", "deploy / rebuild"}
    return required.issubset(headings)


def license(root: Path) -> bool:
    """Check for a non-empty conventional license file."""

    return _first_existing_file(
        root,
        ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING", "COPYING.md"),
    ) is not None


def contributing(root: Path) -> bool:
    """Check for contributor guidance."""

    return _first_existing_file(
        root, ("CONTRIBUTING.md", "CONTRIBUTING.rst", "CONTRIBUTING.txt")
    ) is not None


def templates(root: Path) -> bool:
    """Check for both pull-request and issue contribution templates."""

    pull_request = _first_existing_file(
        root,
        (
            ".github/PULL_REQUEST_TEMPLATE.md",
            ".github/PULL_REQUEST_TEMPLATE.txt",
            ".github/pull_request_template.md",
        ),
    )
    issue_directory = root / ".github" / "ISSUE_TEMPLATE"
    if pull_request is None or not issue_directory.is_dir():
        return False
    try:
        return any(
            candidate.is_file() and candidate.stat().st_size > 0
            for candidate in issue_directory.iterdir()
        )
    except OSError:
        return False


def lockfile(root: Path) -> bool:
    """Check for a lockfile used by a supported ecosystem."""

    return _first_existing_file(
        root,
        (
            "uv.lock",
            "poetry.lock",
            "requirements.lock",
            "package-lock.json",
            "npm-shrinkwrap.json",
            "yarn.lock",
            "pnpm-lock.yaml",
        ),
    ) is not None


def pinned_runtime(root: Path) -> bool:
    """Check for a supported runtime-version file."""

    return _first_existing_file(root, (".python-version", ".nvmrc")) is not None


def dockerfile(root: Path) -> bool:
    """Check for a usable Dockerfile with a base image declaration."""

    path = _first_existing_file(root, ("Dockerfile", "dockerfile"))
    if path is None:
        return False
    text = _read_text(path)
    return text is not None and bool(re.search(r"(?m)^\s*FROM\s+\S+", text))


def _makefile_has_test_target(text: str) -> bool:
    return bool(re.search(r"(?m)^\s*(?:[A-Za-z0-9_.-]+\s*:\s*[^\n]*\s*)?test\s*:", text))


def test_command(root: Path) -> bool:
    """Check for a declared test command in common project entry points."""

    makefile = _first_existing_file(root, ("Makefile", "makefile", "GNUmakefile"))
    if makefile is not None:
        text = _read_text(makefile)
        if text is not None and _makefile_has_test_target(text):
            return True

    package = _read_json(root / "package.json")
    if isinstance(package, dict):
        scripts = package.get("scripts")
        if isinstance(scripts, dict) and isinstance(scripts.get("test"), str):
            return bool(scripts["test"].strip())

    pyproject = _read_text(root / "pyproject.toml")
    if pyproject is not None and re.search(
        r"(?im)^\s*test\s*=\s*[\"'](?:[^\"']+)\s*[\"']", pyproject
    ):
        return True
    return False


def env_example(root: Path) -> bool:
    """Check for a non-empty example environment file."""

    return _nonempty_file(root / ".env.example")


_ABSOLUTE_PATH_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9_])/(?:Users|home|root|private/var|opt|tmp)/"),
    re.compile(r"(?i)(?<![A-Za-z0-9_])[A-Z]:[\\/]Users[\\/]"),
    re.compile(r"(?i)(?<![A-Za-z0-9_])[A-Z]:[\\/](?:Windows|Program Files)[\\/]"),
    re.compile(r"(?<![A-Za-z0-9_])\\\\(?:Users|home|root)\\\\"),
)
_SKIPPED_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
}


def _iter_text_files(root: Path):
    try:
        paths = root.rglob("*")
    except (OSError, ValueError):
        return
    for path in paths:
        if not path.is_file() or any(part in _SKIPPED_DIRECTORIES for part in path.parts):
            continue
        try:
            if path.stat().st_size > 1_000_000:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in data:
            continue
        try:
            yield path, data.decode("utf-8")
        except UnicodeDecodeError:
            continue


def no_hardcoded_paths(root: Path) -> bool:
    """Check that repository text does not contain common machine paths."""

    for _path, text in _iter_text_files(root):
        if any(pattern.search(text) for pattern in _ABSOLUTE_PATH_PATTERNS):
            return False
    return True


CHECKS: dict[str, Check] = {
    "readme_sections": readme_sections,
    "license": license,
    "contributing": contributing,
    "templates": templates,
    "lockfile": lockfile,
    "pinned_runtime": pinned_runtime,
    "dockerfile": dockerfile,
    "test_command": test_command,
    "env_example": env_example,
    "no_hardcoded_paths": no_hardcoded_paths,
}


def evaluate_checks(root: Path) -> dict[str, bool]:
    """Evaluate all checks in the fixed contract order."""

    return {name: check(root) for name, check in CHECKS.items()}


def score_checks(checks: Mapping[str, bool]) -> int:
    """Return the weighted score for a check result mapping."""

    return sum(weight for name, weight in CHECK_WEIGHTS.items() if checks.get(name, False))


def report(root: Path) -> dict[str, object]:
    """Build the exact machine-readable check report."""

    checks = evaluate_checks(root)
    return {
        "score": score_checks(checks),
        "ecosystems": detect_ecosystems(root),
        "checks": checks,
    }
