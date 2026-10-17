"""Non-destructive, filesystem-only fix planning and application."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from .checks import environment_reads
from .context import RepoContext
from .detector import detect_ecosystems
from .scoring import report


@dataclass(frozen=True)
class PlannedFile:
    """One inspectable fix operation.

    ``action`` is deliberately kept on the existing ``PlannedFile`` type so
    callers that already inspect ``path`` and ``content`` keep working while
    planning can also explain non-creation decisions.
    """

    path: Path
    content: str = ""
    reason: str = ""
    action: str = "create"

    @property
    def status(self) -> str:
        """Compatibility spelling for callers that call the action a status."""

        return self.action

    def as_dict(self, root: str | Path | None = None) -> dict[str, str]:
        path = self.path
        display = path.as_posix()
        if root is not None:
            try:
                display = path.relative_to(Path(root)).as_posix()
            except ValueError:
                display = path.as_posix()
        return {"action": self.action, "path": display, "reason": self.reason}


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
_MACHINE_PATHS = (
    re.compile(r"(?<![A-Za-z0-9_])/(?:Users|home)/[^/\s]+/"),
    re.compile(r"(?i)(?<![A-Za-z0-9_])[A-Z]:[\\/]Users[\\/][^\\/\s]+[\\/]"),
    re.compile(r"(?<![A-Za-z0-9_])\\\\(?:Users|home)\\\\[^\\\s]+\\\\"),
)


def _trusted_root(root: str | Path) -> Path | None:
    try:
        candidate = Path(root).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return None
    return candidate if candidate.is_dir() else None


def _safe_target(root: Path, relative: str) -> Path | None:
    operation = _target_operation(root, relative, "", "")
    return operation.path if operation.action == "create" else None


def _target_operation(root: Path, relative: str, content: str, reason: str) -> PlannedFile:
    """Classify a target without following an unsafe parent symlink."""

    try:
        pure = PurePosixPath(relative)
        target = root.joinpath(*pure.parts)
    except (TypeError, ValueError):
        return PlannedFile(root, content, f"{reason}; invalid target path.", "skip-unsafe")
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        return PlannedFile(target, content, f"{reason}; target escapes the repository root.", "skip-unsafe")
    current = root
    try:
        for part in pure.parts[:-1]:
            current = current / part
            if current.is_symlink():
                return PlannedFile(target, content, f"{reason}; parent path is a symlink.", "skip-unsafe")
        if target.exists() or target.is_symlink():
            return PlannedFile(
                target,
                content,
                f"{reason}; target already exists and will not be overwritten.",
                "skip-existing",
            )
        if not target.parent.resolve(strict=False).is_relative_to(root):
            return PlannedFile(target, content, f"{reason}; target escapes the repository root.", "skip-unsafe")
    except (OSError, RuntimeError, ValueError):
        return PlannedFile(target, content, f"{reason}; target could not be verified safely.", "skip-unsafe")
    return PlannedFile(target, content, reason, "create")


def _plan(root: Path, relative: str, content: str, reason: str) -> PlannedFile:
    return _target_operation(root, relative, content, reason)


def _toml(context: RepoContext, relative: str) -> dict[str, object] | None:
    text = context.read_text(relative)
    if text is None:
        return None
    try:
        return tomllib.loads(text)
    except (UnicodeError, tomllib.TOMLDecodeError):
        return None


def _runtime_value(value: str, *, node: bool = False) -> str | None:
    pattern = r"\s*v?(\d+(?:\.\d+){0,2})\s*" if node else r"\s*v?(\d+\.\d+(?:\.\d+)?)\s*"
    match = re.fullmatch(pattern, value)
    return match.group(1) if match else None


def _python_runtime(context: RepoContext, explicit: str | None) -> str | None:
    if explicit:
        return _runtime_value(explicit)
    document = _toml(context, "pyproject.toml")
    project = document.get("project") if isinstance(document, dict) else None
    requires = project.get("requires-python") if isinstance(project, dict) else None
    if isinstance(requires, str):
        match = re.search(r"(?<!\d)(\d+\.\d+(?:\.\d+)?)", requires)
        if match:
            return match.group(1)
    tools = context.read_text(".tool-versions") or ""
    match = re.search(r"(?m)^\s*python\s+(\d+\.\d+(?:\.\d+)?)", tools)
    return match.group(1) if match else None


def _node_runtime(context: RepoContext, explicit: str | None) -> str | None:
    if explicit:
        return _runtime_value(explicit, node=True)
    text = context.read_text("package.json")
    if text is None:
        return None
    try:
        package = json.loads(text)
    except json.JSONDecodeError:
        return None
    engines = package.get("engines") if isinstance(package, dict) else None
    value = engines.get("node") if isinstance(engines, dict) else None
    match = re.search(r"(?<!\d)(\d+(?:\.\d+){0,2})", value) if isinstance(value, str) else None
    return match.group(1) if match else None


def _contains_machine_path(text: str) -> bool:
    return any(pattern.search(text) for pattern in _MACHINE_PATHS)


def _dockerfile(ecosystems: list[str], runtime: str) -> str:
    if "python" in ecosystems:
        return f"FROM python:{runtime}-slim\n\nWORKDIR /app\nCOPY . .\nCMD [\"python\", \"-m\", \"pytest\", \"-q\"]\n"
    return f"FROM node:{runtime}-slim\n\nWORKDIR /app\nCOPY package*.json ./\nCOPY . .\nCMD [\"npm\", \"test\"]\n"


def _ci_workflow(ecosystems: list[str]) -> str:
    command = "python -m pytest -q" if "python" in ecosystems else "npm test"
    setup = """      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
""" if "python" in ecosystems else ""
    return (
        "name: CI\n\n"
        "on: [push, pull_request]\n\n"
        "jobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - uses: actions/checkout@v4\n"
        f"{setup}"
        f"      - run: {command}\n"
    )


def plan_fixes(
    root: str | Path,
    *,
    license_name: str | None = None,
    holder: str | None = None,
    runtime_version: str | None = None,
) -> list[PlannedFile]:
    """Return deterministic create, skip, and manual operations for *root*.

    The returned list is intentionally inspectable.  Only ``create`` entries
    can be written; lockfiles, tests, unknown runtimes, and machine-specific
    paths remain explicit ``manual`` entries.
    """

    trusted = _trusted_root(root)
    if trusted is None:
        return []
    context = RepoContext(trusted)
    ecosystems = detect_ecosystems(context)
    plans: list[PlannedFile] = []

    def add(relative: str, content: str, reason: str) -> None:
        plans.append(_plan(trusted, relative, content, reason))

    def skip_existing(relative: str, reason: str) -> bool:
        if (entry := context.entry(relative)) is not None:
            plans.append(
                PlannedFile(
                    entry.absolute,
                    "",
                    f"{reason}; {relative} already exists and will not be overwritten.",
                    "skip-existing",
                )
            )
            return True
        candidate = trusted / relative
        try:
            if candidate.is_symlink():
                plans.append(
                    PlannedFile(
                        candidate,
                        "",
                        f"{reason}; {relative} is an existing symlink and will not be overwritten.",
                        "skip-existing",
                    )
                )
                return True
        except OSError:
            pass
        return False

    def add_if_missing(relative: str, content: str, reason: str, names: tuple[str, ...]) -> None:
        existing = next((name for name in names if context.entry(name) is not None), None)
        if existing is not None:
            skip_existing(existing, reason)
        elif any((trusted / name).is_symlink() for name in names):
            existing = next(name for name in names if (trusted / name).is_symlink())
            skip_existing(existing, reason)
        else:
            add(relative, content, reason)

    add_if_missing("README.md", _README, "README skeleton", ("README.md", "README.rst", "README.txt"))
    add_if_missing(
        "CONTRIBUTING.md",
        _CONTRIBUTING,
        "contributor guide",
        ("CONTRIBUTING.md", "docs/CONTRIBUTING.md", ".github/CONTRIBUTING.md"),
    )
    issue_existing = next(
        (entry.path for entry in context if entry.path == "ISSUE_TEMPLATE.md" or entry.path.startswith(".github/ISSUE_TEMPLATE/")),
        None,
    )
    if issue_existing:
        skip_existing(issue_existing, "issue template")
    else:
        add(".github/ISSUE_TEMPLATE/bug_report.md", _ISSUE, "issue template")
    add_if_missing(
        ".github/PULL_REQUEST_TEMPLATE.md",
        _PR,
        "pull-request template",
        (".github/PULL_REQUEST_TEMPLATE.md", "PULL_REQUEST_TEMPLATE.md", "docs/PULL_REQUEST_TEMPLATE.md"),
    )

    license_path = _nonempty(context, ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING"))
    if license_path:
        skip_existing(license_path, "license")
    elif license_name == "mit" and holder:
        license_content = _MIT.format(holder=holder)
        if _contains_machine_path(license_content):
            plans.append(
                PlannedFile(
                    trusted / "LICENSE",
                    "",
                    "The requested license holder contains a machine-specific path; edit the holder and create the license manually.",
                    "manual",
                )
            )
        else:
            add("LICENSE", license_content, "MIT license requested explicitly")
    else:
        plans.append(
            PlannedFile(
                trusted / "LICENSE",
                "",
                "A license choice and copyright holder are required; no license is generated without explicit input.",
                "manual",
            )
        )

    python_runtime = _python_runtime(context, runtime_version) if "python" in ecosystems else None
    node_runtime = _node_runtime(context, runtime_version) if "node" in ecosystems else None
    if "python" in ecosystems:
        runtime_path = _nonempty(context, (".python-version", "runtime.txt"))
        if runtime_path:
            skip_existing(runtime_path, "Python runtime pin")
        elif python_runtime:
            add(".python-version", python_runtime + "\n", "Python runtime pin")
        else:
            plans.append(
                PlannedFile(
                    trusted / ".python-version",
                    "",
                    "Python runtime is unknown; choose a supported version and pin it manually.",
                    "manual",
                )
            )
    if "node" in ecosystems:
        runtime_path = _nonempty(context, (".nvmrc", ".node-version"))
        if runtime_path:
            skip_existing(runtime_path, "Node runtime pin")
        elif node_runtime:
            add(".nvmrc", node_runtime + "\n", "Node runtime pin")
        else:
            plans.append(
                PlannedFile(
                    trusted / ".nvmrc",
                    "",
                    "Node runtime is unknown; choose a supported version and pin it manually.",
                    "manual",
                )
            )

    if ecosystems:
        docker_path = _nonempty(context, ("Dockerfile", "Containerfile"))
        docker_runtime = python_runtime if "python" in ecosystems else node_runtime
        if docker_path:
            skip_existing(docker_path, "Dockerfile")
        elif docker_runtime:
            add("Dockerfile", _dockerfile(ecosystems, docker_runtime), "Dockerfile from detected runtime")
            add(".dockerignore", _DOCKERIGNORE, "Docker build context exclusions")
        else:
            plans.append(
                PlannedFile(
                    trusted / "Dockerfile",
                    "",
                    "A Dockerfile needs a known runtime base image; choose the runtime and create it manually.",
                    "manual",
                )
            )
    reads = environment_reads(context)
    if reads:
        keys = sorted({name for _, _, name in reads})
        add_if_missing(
            ".env.example",
            "".join(f"{key}=\n" for key in keys),
            "environment variable documentation",
            (".env.example", ".env.sample", ".env.template"),
        )
    lines = [".env", "__pycache__", ".venv"]
    if "node" in ecosystems:
        lines.append("node_modules")
    add_if_missing(".gitignore", "\n".join(lines) + "\n", "ecosystem gitignore", (".gitignore",))
    workflow_existing = next((entry.path for entry in context if entry.path.startswith(".github/workflows/")), None)
    if workflow_existing:
        skip_existing(workflow_existing, "CI workflow")
    else:
        add(".github/workflows/ci.yml", _ci_workflow(ecosystems), "CI workflow")

    plans.extend(
        (
            PlannedFile(
                trusted / "lockfiles (manual)",
                "",
                "Lockfiles are never created automatically; choose and run the project package manager manually.",
                "manual",
            ),
            PlannedFile(
                trusted / "tests (manual)",
                "",
                "Tests and test commands are never created automatically; document the real project test command manually.",
                "manual",
            ),
            PlannedFile(
                trusted / "machine-specific paths (manual)",
                "",
                "Machine-specific absolute paths are never rewritten automatically; review them manually.",
                "manual",
            ),
        )
    )
    return plans


def _nonempty(context: RepoContext, names: tuple[str, ...]) -> str | None:
    for name in names:
        entry = context.entry(name)
        if entry and entry.readable and entry.size > 0:
            return name
    return None


def apply_fix_plan(
    plans: list[PlannedFile],
    *,
    root: str | Path,
    dry_run: bool = False,
) -> list[PlannedFile]:
    """Apply a plan and return an inspectable result for every operation."""

    trusted = _trusted_root(root)
    if trusted is None:
        return []
    outcomes: list[PlannedFile] = []
    for plan in plans:
        if plan.action != "create":
            outcomes.append(plan)
            continue
        try:
            candidate = plan.path if plan.path.is_absolute() else trusted / plan.path
            relative = candidate.relative_to(trusted).as_posix()
        except (OSError, RuntimeError, ValueError):
            outcomes.append(replace(plan, action="skip-unsafe", reason=f"{plan.reason}; target escapes the repository root."))
            continue
        guarded = _plan(trusted, relative, plan.content, plan.reason)
        if guarded.action != "create":
            outcomes.append(guarded)
            continue
        target = guarded.path
        if dry_run:
            outcomes.append(guarded)
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("x", encoding="utf-8", newline="") as handle:
                handle.write(plan.content)
            outcomes.append(guarded)
        except FileExistsError:
            outcomes.append(
                replace(
                    guarded,
                    action="skip-existing",
                    reason=f"{plan.reason}; target appeared before exclusive creation and was not overwritten.",
                )
            )
        except (OSError, RuntimeError):
            outcomes.append(
                replace(
                    guarded,
                    action="skip-unsafe",
                    reason=f"{plan.reason}; target could not be created safely.",
                )
            )
    return outcomes


def project_fix_score(
    root: str | Path,
    outcomes: list[PlannedFile],
) -> int:
    """Score create operations in an isolated copy of *root*.

    Dry-run scoring must see the same repository files as a real scan plus
    the create operations that would be applied.  The copy is made with
    standard-library filesystem operations and the existing fix-plan and
    report APIs; the target repository is never modified.
    """

    trusted = _trusted_root(root)
    if trusted is None:
        return 0
    with tempfile.TemporaryDirectory(prefix="repo-ready-fix-") as temporary:
        projected_root = Path(temporary) / "repository"
        projected_root.mkdir(parents=True)
        context = RepoContext(trusted)
        for entry in context:
            destination = projected_root / entry.path
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(entry.absolute, destination)
            except OSError:
                continue
        projected_plans: list[PlannedFile] = []
        for operation in outcomes:
            if operation.action != "create":
                continue
            try:
                relative = operation.path.relative_to(trusted)
            except (OSError, RuntimeError, ValueError):
                continue
            projected_plans.append(
                PlannedFile(relative, operation.content, operation.reason)
            )
        apply_fix_plan(projected_plans, root=projected_root)
        return int(report(projected_root)["score"])


def protect_score(
    root: str | Path,
    plans: list[PlannedFile],
    score_before: int,
) -> list[PlannedFile]:
    """Convert score-decreasing creations into manual operations.

    Fixing a repository must not silently make its readiness score worse. The
    proposed files are evaluated in an isolated copy before any target write;
    if the projection regresses, the create operations are retained as an
    explainable manual item instead of being applied.
    """

    projected = project_fix_score(root, plans)
    if projected >= score_before:
        return plans
    return [
        replace(
            operation,
            action="manual",
            reason=(
                f"{operation.reason}; creating this file would lower the score "
                "and was not applied."
            ),
        )
        if operation.action == "create"
        else operation
        for operation in plans
    ]


def apply_fixes(plans: list[PlannedFile], *, root: str | Path, dry_run: bool = False) -> list[Path]:
    """Compatibility wrapper returning paths for create operations only."""

    return [
        operation.path.resolve(strict=False)
        for operation in apply_fix_plan(plans, root=root, dry_run=dry_run)
        if operation.action == "create"
    ]


__all__ = [
    "PlannedFile",
    "apply_fix_plan",
    "apply_fixes",
    "plan_fixes",
    "project_fix_score",
    "protect_score",
]
