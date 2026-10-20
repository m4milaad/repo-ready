"""Validate release metadata before building or publishing an artifact."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import sys
import tomllib


SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


class ReleaseCheckError(ValueError):
    """Raised when repository release metadata is inconsistent."""


def _read_version(root: Path) -> str:
    version_file = root / "src" / "repo_ready" / "_version.py"
    text = version_file.read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']\s*$', text, re.MULTILINE)
    if match is None:
        raise ReleaseCheckError(f"could not find __version__ in {version_file}")
    return match.group(1)


def validate_release(root: str | Path, expected: str | None = None) -> tuple[str, ...]:
    """Return deterministic validation messages or raise on the first error."""

    repository = Path(root).resolve()
    if not repository.is_dir():
        raise ReleaseCheckError(f"repository is not a directory: {repository}")

    version = _read_version(repository)
    if SEMVER.fullmatch(version) is None:
        raise ReleaseCheckError(f"version is not semantic versioning: {version!r}")
    if expected is not None and version != expected.removeprefix("v"):
        raise ReleaseCheckError(f"expected version {expected.removeprefix('v')!r}, found {version!r}")

    try:
        project = tomllib.loads((repository / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise ReleaseCheckError(f"pyproject.toml could not be read: {error}") from error
    project_data = project.get("project")
    dynamic = project_data.get("dynamic", []) if isinstance(project_data, dict) else []
    if "version" not in dynamic:
        raise ReleaseCheckError("pyproject.toml must declare a dynamic version")
    dynamic_config = project.get("tool", {}).get("setuptools", {}).get("dynamic", {})
    version_attr = dynamic_config.get("version", {}).get("attr") if isinstance(dynamic_config, dict) else None
    if version_attr != "repo_ready._version.__version__":
        raise ReleaseCheckError("setuptools dynamic version must read repo_ready._version.__version__")

    changelog = (repository / "CHANGELOG.md").read_text(encoding="utf-8")
    if f"## [{version}]" not in changelog:
        raise ReleaseCheckError(f"CHANGELOG.md has no release section for {version}")

    node_version = (repository / ".nvmrc").read_text(encoding="utf-8").strip()
    package = json.loads((repository / "package.json").read_text(encoding="utf-8"))
    if not isinstance(package, dict) or package.get("private") is not True:
        raise ReleaseCheckError("browser tooling package must remain private")
    if package.get("engines", {}).get("node") != node_version:
        raise ReleaseCheckError("package.json Node engine does not match .nvmrc")

    return (
        f"version: {version}",
        "pyproject: dynamic version configured",
        f"changelog: [{version}] present",
        f"node: {node_version}",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate repo-ready release metadata.")
    parser.add_argument("expected", nargs="?", help="expected release version, with or without a leading v")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        for message in validate_release(args.root, args.expected):
            print(f"release metadata: {message}")
    except (OSError, json.JSONDecodeError, ReleaseCheckError) as error:
        print(f"release metadata: failed: {error}", file=sys.stderr)
        return 1
    print("release metadata: passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
