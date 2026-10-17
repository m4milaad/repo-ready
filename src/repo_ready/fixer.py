"""Non-destructive, filesystem-only fix planning and application."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .checks import environment_reads
from .context import RepoContext
from .detector import detect_ecosystems


@dataclass(frozen=True)
class PlannedFile:
    path: Path
    content: str
    reason: str = ""


_README = """# Repository

## Setup

Describe the runtime and installation steps.

## Run

Describe how to run the project.

## Test

Describe the project's test command.

## Deploy / rebuild

Describe how to rebuild this repository from a clean checkout.
"""
_CONTRIBUTING = """# Contributing

Thanks for contributing. Explain the development setup, the supported runtime,
the test command, how to report issues, and what a pull request should contain.
Keep changes focused, run the documented tests before submitting, and include
the exact verification command in the pull request description.
"""
_ISSUE = """---
name: Bug report
about: Report a reproducible problem
---

## What happened?

## Steps to reproduce

## Expected behavior
"""
_PR = """## Summary

## Verification

- [ ] `python -m pytest -q` or the documented project test command
"""
_MIT = """MIT License

Copyright (c) {holder}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the \"Software\"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
_CI = """name: CI

on:
  push:
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: python -m pytest -q
"""
_DOCKERIGNORE = ".git\n.venv\n__pycache__\n.pytest_cache\n.env\n"


def _trusted_root(root: str | Path) -> Path | None:
    try:
        candidate = Path(root).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return None
    return candidate if candidate.is_dir() else None


def _safe_target(root: Path, relative: str) -> Path | None:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        return None
    target = root.joinpath(*pure.parts)
    current = root
    for part in pure.parts[:-1]:
        current = current / part
        if current.is_symlink():
            return None
    if target.exists() or target.is_symlink():
        return None
    try:
        if not target.parent.resolve(strict=False).is_relative_to(root):
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    return target


def _plan(root: Path, relative: str, content: str, reason: str) -> PlannedFile | None:
    target = _safe_target(root, relative)
    return PlannedFile(target, content, reason) if target is not None else None


def _toml(root: Path, relative: str) -> dict[str, object] | None:
    try:
        return tomllib.loads((root / relative).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return None


def _python_runtime(root: Path, explicit: str | None) -> str | None:
    if explicit:
        return explicit.lstrip("v")
    document = _toml(root, "pyproject.toml")
    project = document.get("project") if isinstance(document, dict) else None
    requires = project.get("requires-python") if isinstance(project, dict) else None
    if isinstance(requires, str):
        match = re.search(r"(?<!\d)(\d+\.\d+(?:\.\d+)?)", requires)
        if match:
            return match.group(1)
    tools = (root / ".tool-versions").read_text(encoding="utf-8") if (root / ".tool-versions").is_file() else ""
    match = re.search(r"(?m)^\s*python\s+(\d+\.\d+(?:\.\d+)?)", tools)
    return match.group(1) if match else None


def _node_runtime(root: Path, explicit: str | None) -> str | None:
    if explicit:
        return explicit.lstrip("v")
    try:
        package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    engines = package.get("engines") if isinstance(package, dict) else None
    value = engines.get("node") if isinstance(engines, dict) else None
    match = re.search(r"(?<!\d)(\d+(?:\.\d+){0,2})", value) if isinstance(value, str) else None
    return match.group(1) if match else None


def _dockerfile(ecosystems: list[str], runtime: str) -> str:
    if "python" in ecosystems:
        return f"FROM python:{runtime}-slim\n\nWORKDIR /app\nCOPY . .\nCMD [\"python\", \"-m\", \"pytest\", \"-q\"]\n"
    return f"FROM node:{runtime}-slim\n\nWORKDIR /app\nCOPY package*.json ./\nCOPY . .\nCMD [\"npm\", \"test\"]\n"


def plan_fixes(
    root: str | Path,
    *,
    license_name: str | None = None,
    holder: str | None = None,
    runtime_version: str | None = None,
) -> list[PlannedFile]:
    """Plan only safe creations inside *root*; never plan lockfiles or tests."""

    trusted = _trusted_root(root)
    if trusted is None:
        return []
    context = RepoContext(trusted)
    ecosystems = detect_ecosystems(context)
    plans: list[PlannedFile] = []

    def add(relative: str, content: str, reason: str) -> None:
        planned = _plan(trusted, relative, content, reason)
        if planned:
            plans.append(planned)

    if _nonempty(context, ("README.md", "README.rst", "README.txt")) is None:
        add("README.md", _README, "README skeleton")
    if _nonempty(context, ("CONTRIBUTING.md", "docs/CONTRIBUTING.md", ".github/CONTRIBUTING.md")) is None:
        add("CONTRIBUTING.md", _CONTRIBUTING, "contributor guide")
    if _nonempty(context, ("ISSUE_TEMPLATE.md",)) is None and not any(entry.path.startswith(".github/ISSUE_TEMPLATE/") and entry.size > 0 for entry in context):
        add(".github/ISSUE_TEMPLATE/bug_report.md", _ISSUE, "issue template")
    if _nonempty(context, (".github/PULL_REQUEST_TEMPLATE.md", "PULL_REQUEST_TEMPLATE.md", "docs/PULL_REQUEST_TEMPLATE.md")) is None:
        add(".github/PULL_REQUEST_TEMPLATE.md", _PR, "pull-request template")
    if license_name == "mit" and holder and _nonempty(context, ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING")) is None:
        add("LICENSE", _MIT.format(holder=holder), "MIT license requested explicitly")

    if "python" in ecosystems and _nonempty(context, (".python-version", "runtime.txt")) is None:
        runtime = _python_runtime(trusted, runtime_version)
        if runtime:
            add(".python-version", runtime + "\n", "Python runtime pin")
    if "node" in ecosystems and _nonempty(context, (".nvmrc", ".node-version")) is None:
        runtime = _node_runtime(trusted, runtime_version)
        if runtime:
            add(".nvmrc", runtime + "\n", "Node runtime pin")
    if _nonempty(context, ("Dockerfile", "Containerfile")) is None and ecosystems:
        runtime = _python_runtime(trusted, runtime_version) if "python" in ecosystems else _node_runtime(trusted, runtime_version)
        if runtime:
            add("Dockerfile", _dockerfile(ecosystems, runtime), "Dockerfile from detected runtime")
            add(".dockerignore", _DOCKERIGNORE, "Docker build context exclusions")
    reads = environment_reads(context)
    if reads and _nonempty(context, (".env.example", ".env.sample", ".env.template")) is None:
        keys = sorted({name for _, _, name in reads})
        add(".env.example", "".join(f"{key}=\n" for key in keys), "environment variable documentation")
    if _nonempty(context, (".gitignore",)) is None:
        lines = [".env", "__pycache__", ".venv"]
        if "node" in ecosystems:
            lines.append("node_modules")
        add(".gitignore", "\n".join(lines) + "\n", "ecosystem gitignore")
    if not any(entry.path.startswith(".github/workflows/") and entry.size > 0 for entry in context):
        add(".github/workflows/ci.yml", _CI, "CI workflow")
    return plans


def _nonempty(context: RepoContext, names: tuple[str, ...]) -> str | None:
    for name in names:
        entry = context.entry(name)
        if entry and entry.readable and entry.size > 0:
            return name
    return None


def apply_fixes(plans: list[PlannedFile], *, root: str | Path, dry_run: bool = False) -> list[Path]:
    """Create planned files with exclusive mode and a second containment check."""

    trusted = _trusted_root(root)
    if trusted is None:
        return []
    completed: list[Path] = []
    for plan in plans:
        try:
            target = plan.path.resolve(strict=False)
            if not target.is_relative_to(trusted):
                continue
            current = trusted
            relative = target.relative_to(trusted)
            for part in relative.parts[:-1]:
                current = current / part
                if current.is_symlink():
                    raise OSError("symlinked parent")
            if dry_run:
                completed.append(target)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("x", encoding="utf-8", newline="") as handle:
                handle.write(plan.content)
            completed.append(target)
        except (FileExistsError, OSError, RuntimeError):
            continue
    return completed
