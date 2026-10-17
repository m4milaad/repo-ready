"""Non-destructive scaffolding for missing repository readiness files."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .detector import detect_ecosystems


@dataclass(frozen=True)
class PlannedFile:
    """A file that can be created without changing an existing file."""

    path: Path
    content: str


_README = """# Repository\n\n## Requirements\n\nDocument the runtime and system requirements here.\n\n## Setup\n\nDescribe how to install dependencies.\n\n## Run\n\nDescribe how to run the project.\n\n## Test\n\nDescribe how to run the test suite.\n\n## Deploy / rebuild\n\nDescribe how to build and run the project from a clean environment.\n\n## Configuration\n\nCopy `.env.example` for local configuration.\n"""

_LICENSE = """MIT License\n\nCopyright (c) 2026 Project Authors\n\nPermission is hereby granted, free of charge, to any person obtaining a copy\nof this software and associated documentation files (the \"Software\"), to deal\nin the Software without restriction, including without limitation the rights\nto use, copy, modify, merge, publish, distribute, sublicense, and/or sell\ncopies of the Software, and to permit persons to whom the Software is\nfurnished to do so, subject to the following conditions:\n\nThe above copyright notice and this permission notice shall be included in all\ncopies or substantial portions of the Software.\n\nTHE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR\nIMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,\nFITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE\nAUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER\nLIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,\nOUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE\nSOFTWARE.\n"""

_CONTRIBUTING = """# Contributing\n\n1. Make a focused change.\n2. Run the documented test command.\n3. Describe the change and its verification in the pull request.\n"""

_PULL_REQUEST_TEMPLATE = """## Summary\n\nDescribe the change and why it is needed.\n\n## Verification\n\n- [ ] Tests pass\n- [ ] Documentation is updated when needed\n"""

_ISSUE_TEMPLATE = """---\nname: Bug report\nabout: Report a reproducible problem\ntitle: \"\"\nlabels: bug\n---\n\n## What happened?\n\n## Steps to reproduce\n\n## Expected behavior\n\n## Environment\n"""

_ENV_EXAMPLE = "# Add non-secret local configuration values here.\n"


def _exists(root: Path, relative: str) -> bool:
    return (root / relative).exists()


def _first_existing(root: Path, names: tuple[str, ...]) -> bool:
    return any(_exists(root, name) for name in names)


def _python_version(root: Path) -> str:
    path = root / "pyproject.toml"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return "3.11"
    match = re.search(r"(?im)^\s*requires-python\s*=\s*[\"']\s*(?:>=)?(\d+\.\d+)", text)
    return match.group(1) if match else "3.11"


def _node_version(root: Path) -> str:
    try:
        package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "20"
    if isinstance(package, dict):
        engines = package.get("engines")
        if isinstance(engines, dict):
            version = engines.get("node")
            if isinstance(version, str):
                match = re.search(r"(\d+(?:\.\d+){0,2})", version)
                if match:
                    return match.group(1)
    return "20"


def _package_lock(root: Path) -> str:
    try:
        package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        package = {}
    if not isinstance(package, dict):
        package = {}
    root_package: dict[str, object] = {}
    for key in ("name", "version", "engines"):
        if key in package:
            root_package[key] = package[key]
    document = {
        "name": package.get("name", "repository"),
        "version": package.get("version", "0.0.0"),
        "lockfileVersion": 3,
        "requires": True,
        "packages": {"": root_package},
    }
    return json.dumps(document, indent=2) + "\n"


def _python_dependencies_exist(root: Path) -> bool:
    requirements = root / "requirements.txt"
    try:
        if any(
            line.strip() and not line.lstrip().startswith("#")
            for line in requirements.read_text(encoding="utf-8").splitlines()
        ):
            return True
    except (OSError, UnicodeError):
        pass

    try:
        project = tomllib.loads(
            (root / "pyproject.toml").read_text(encoding="utf-8")
        ).get("project", {})
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return False
    if not isinstance(project, dict):
        return False
    return bool(project.get("dependencies")) or bool(
        project.get("optional-dependencies")
    )


def _node_dependencies_exist(root: Path) -> bool:
    try:
        package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(package, dict):
        return False
    return any(
        isinstance(package.get(group), dict) and package[group]
        for group in ("dependencies", "devDependencies", "optionalDependencies")
    )


def _dockerfile(ecosystems: list[str], root: Path) -> str:
    if "python" in ecosystems:
        version = _python_version(root)
        return (
            f"FROM python:{version}-slim\n\n"
            "WORKDIR /app\n"
            "COPY . .\n"
            "\n"
            'CMD ["python", "-m", "unittest", "discover"]\n'
        )
    if "node" in ecosystems:
        version = _node_version(root)
        return (
            f"FROM node:{version}-slim\n\n"
            "WORKDIR /app\n"
            "COPY . .\n\n"
            'CMD ["node", "--test"]\n'
        )
    return "FROM debian:bookworm-slim\n\nWORKDIR /app\nCOPY . .\n"


def _makefile(ecosystems: list[str]) -> str:
    if "python" in ecosystems:
        command = "python -m unittest discover"
    elif "node" in ecosystems:
        command = "npm test"
    else:
        command = "@echo \"Add the project test command\""
    return f".PHONY: test\n\ntest:\n\t{command}\n"


def plan_fixes(root: Path) -> list[PlannedFile]:
    """Return creations needed for currently missing, fixable checks."""

    ecosystems = detect_ecosystems(root)
    plans: list[PlannedFile] = []

    if not _first_existing(root, ("README.md", "README.rst", "README.txt")):
        plans.append(PlannedFile(root / "README.md", _README))
    if not _first_existing(
        root, ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING", "COPYING.md")
    ):
        plans.append(PlannedFile(root / "LICENSE", _LICENSE))
    if not _first_existing(
        root, ("CONTRIBUTING.md", "CONTRIBUTING.rst", "CONTRIBUTING.txt")
    ):
        plans.append(PlannedFile(root / "CONTRIBUTING.md", _CONTRIBUTING))

    if not _first_existing(
        root,
        (
            ".github/PULL_REQUEST_TEMPLATE.md",
            ".github/PULL_REQUEST_TEMPLATE.txt",
            ".github/pull_request_template.md",
        ),
    ):
        plans.append(
            PlannedFile(root / ".github" / "PULL_REQUEST_TEMPLATE.md", _PULL_REQUEST_TEMPLATE)
        )
    issue_directory = root / ".github" / "ISSUE_TEMPLATE"
    issue_files: list[Path] = []
    if issue_directory.is_dir():
        try:
            issue_files = [
                path
                for path in issue_directory.iterdir()
                if path.is_file() and path.stat().st_size > 0
            ]
        except OSError:
            issue_files = []
    if not issue_files:
        plans.append(PlannedFile(issue_directory / "bug_report.md", _ISSUE_TEMPLATE))

    if (
        "python" in ecosystems
        and not _first_existing(root, ("uv.lock", "poetry.lock", "requirements.lock"))
        and not _python_dependencies_exist(root)
    ):
        plans.append(
            PlannedFile(
                root / "requirements.lock",
                "# Add the resolved Python dependency set here.\n",
            )
        )
    if (
        "node" in ecosystems
        and not _first_existing(
            root,
            ("package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml"),
        )
        and not _node_dependencies_exist(root)
    ):
        plans.append(PlannedFile(root / "package-lock.json", _package_lock(root)))

    if "python" in ecosystems and not _exists(root, ".python-version"):
        plans.append(PlannedFile(root / ".python-version", _python_version(root) + "\n"))
    if "node" in ecosystems and not _exists(root, ".nvmrc"):
        plans.append(PlannedFile(root / ".nvmrc", _node_version(root) + "\n"))

    if not _first_existing(root, ("Dockerfile", "dockerfile")):
        plans.append(PlannedFile(root / "Dockerfile", _dockerfile(ecosystems, root)))

    if not _first_existing(root, ("Makefile", "makefile", "GNUmakefile")):
        plans.append(PlannedFile(root / "Makefile", _makefile(ecosystems)))

    if not _exists(root, ".env.example"):
        plans.append(PlannedFile(root / ".env.example", _ENV_EXAMPLE))

    return plans


def apply_fixes(plans: list[PlannedFile], dry_run: bool = False) -> list[Path]:
    """Apply planned creations, returning paths actually created or planned.

    Exclusive creation keeps the no-overwrite guarantee even if a file appears
    between planning and writing.
    """

    completed: list[Path] = []
    for plan in plans:
        if dry_run:
            completed.append(plan.path)
            continue
        try:
            plan.path.parent.mkdir(parents=True, exist_ok=True)
            with plan.path.open("x", encoding="utf-8", newline="") as handle:
                handle.write(plan.content)
        except FileExistsError:
            continue
        completed.append(plan.path)
    return completed
