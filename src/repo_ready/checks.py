"""The deterministic RR01--RR13 filesystem checks.

Checks never execute a project, call a package manager, open a socket, or use
the clock.  They consume one immutable :class:`RepoContext` file index.
"""

from __future__ import annotations

import json
import ast
import configparser
import re
import tomllib
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Callable, Iterable

from .context import RepoContext
from .models import CheckResult, Finding


CHECK_WEIGHTS: dict[str, int] = {
    "RR01": 14,
    "RR02": 6,
    "RR03": 6,
    "RR04": 4,
    "RR05": 4,
    "RR06": 10,
    "RR07": 8,
    "RR08": 12,
    "RR09": 12,
    "RR10": 6,
    "RR11": 8,
    "RR12": 4,
    "RR13": 6,
}

CHECK_TITLES: dict[str, str] = {
    "RR01": "README",
    "RR02": "LICENSE",
    "RR03": "CONTRIBUTING",
    "RR04": "Issue templates",
    "RR05": "PR template",
    "RR06": "Lockfile",
    "RR07": "Runtime pinned",
    "RR08": "Dockerfile",
    "RR09": "One test command",
    "RR10": ".env.example",
    "RR11": "No machine-specific paths",
    "RR12": ".gitignore",
    "RR13": "CI workflow",
}

CHECK_MESSAGES: dict[str, str] = {
    "RR01": "README covers setup, run, and test instructions.",
    "RR02": "A supported, non-empty license file is present.",
    "RR03": "Contributor guidance is present and substantial.",
    "RR04": "An issue template is present.",
    "RR05": "A pull-request template is present.",
    "RR06": "Declared dependencies are locked or the project has no third-party dependencies.",
    "RR07": "The supported runtime is pinned.",
    "RR08": "A Dockerfile has a tagged base image and a container command.",
    "RR09": "A usable test command is discoverable.",
    "RR10": "Environment variables used by source code are documented.",
    "RR11": "No machine-specific absolute paths are committed.",
    "RR12": ".gitignore covers environment files and ecosystem artifacts.",
    "RR13": "A CI workflow is present.",
}

CHECK_HINTS: dict[str, str | None] = {
    "RR01": "Add the missing README headings and document the real commands.",
    "RR02": "Add LICENSE, LICENSE.md, LICENSE.txt, or COPYING with the chosen legal terms.",
    "RR03": "Add at least 100 characters of contributor guidance in CONTRIBUTING.md.",
    "RR04": "Add .github/ISSUE_TEMPLATE/bug_report.md or ISSUE_TEMPLATE.md.",
    "RR05": "Add .github/PULL_REQUEST_TEMPLATE.md, PULL_REQUEST_TEMPLATE.md, or docs/PULL_REQUEST_TEMPLATE.md.",
    "RR06": "Run the package manager manually, such as `uv lock` or `npm install --package-lock-only`.",
    "RR07": "Pin the runtime in .python-version, runtime.txt, .nvmrc, or .tool-versions.",
    "RR08": "Add a root Dockerfile with a non-latest tagged FROM image and CMD or ENTRYPOINT.",
    "RR09": "Document a real test target, package test script, or pytest/tox/nox configuration.",
    "RR10": "Add an example env file and list every uppercase variable read by source code.",
    "RR11": "Replace laptop-specific absolute paths with relative paths or environment variables.",
    "RR12": "Add .env and the ecosystem cache/build directories to .gitignore.",
    "RR13": "Add a workflow under .github/workflows/ that runs the project test command.",
}

CHECK_FIXABLE: dict[str, bool] = {
    "RR01": True,
    "RR02": False,
    "RR03": True,
    "RR04": True,
    "RR05": True,
    "RR06": False,
    "RR07": True,
    "RR08": True,
    "RR09": False,
    "RR10": True,
    "RR11": False,
    "RR12": True,
    "RR13": True,
}


def _finding(file: str, detail: str, line: int | None = None) -> Finding:
    return Finding(file=file, line=line, detail=detail)


def _result(
    rule_id: str,
    earned: int,
    findings: Iterable[Finding] = (),
    *,
    message: str | None = None,
    fix_hint: str | None = None,
    fixable: bool | None = None,
    na: bool = False,
) -> CheckResult:
    weight = CHECK_WEIGHTS[rule_id]
    earned = max(0, min(weight, int(earned)))
    findings_tuple = tuple(sorted(findings, key=lambda item: (item.file, item.line or 0, item.detail)))
    status = "na" if na else ("pass" if earned == weight else "partial" if earned else "fail")
    return CheckResult(
        id=rule_id,
        title=CHECK_TITLES[rule_id],
        weight=weight,
        earned=earned,
        status=status,
        message=message or CHECK_MESSAGES[rule_id],
        fix_hint=CHECK_HINTS[rule_id] if fix_hint is None else fix_hint,
        fixable=CHECK_FIXABLE[rule_id] if fixable is None else fixable,
        findings=findings_tuple,
    )


def _text(ctx: RepoContext, names: Iterable[str]) -> tuple[str, str] | None:
    for name in names:
        if ctx.has_file(name):
            value = ctx.read_text(name)
            if value is not None:
                return name, value
    return None


def _nonempty(ctx: RepoContext, names: Iterable[str]) -> str | None:
    for name in names:
        entry = ctx.entry(name)
        if entry and entry.size > 0 and entry.readable:
            return name
    return None


def _headings(text: str) -> dict[str, int]:
    result: dict[str, int] = {}
    lines = text.splitlines()
    for number, line in enumerate(lines, 1):
        markdown = re.match(r"^\s*#{1,6}\s+(.+?)\s*#*\s*$", line)
        if markdown:
            result[re.sub(r"\s+", " ", markdown.group(1)).strip().casefold()] = number
        if number < len(lines):
            next_line = lines[number].strip()
            if line.strip() and re.fullmatch(r"(?:=+|-+)", next_line):
                result[line.strip().casefold()] = number
    return result


def rr01(ctx: RepoContext) -> CheckResult:
    readme = _text(ctx, ("README.md", "README.rst", "README.txt"))
    if readme is None:
        return _result("RR01", 0, [_finding("README.md", "README file is missing.")])
    path, text = readme
    headings = _headings(text)
    earned = 4
    findings: list[Finding] = []
    if "setup" in headings or "install" in headings or "installation" in headings:
        earned += 4
    else:
        findings.append(_finding(path, "README is missing a setup or install heading."))
    if "run" in headings or "usage" in headings:
        earned += 3
    else:
        findings.append(_finding(path, "README is missing a run or usage heading."))
    if "test" in headings or "testing" in headings:
        earned += 3
    else:
        findings.append(_finding(path, "README is missing a test heading."))
    if not text.strip():
        return _result("RR01", 0, [_finding(path, "README is empty.")])
    return _result("RR01", earned, findings)


def rr02(ctx: RepoContext) -> CheckResult:
    path = _nonempty(ctx, ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING"))
    return _result("RR02", 6 if path else 0, () if path else [_finding("LICENSE", "A non-empty license file is missing.")])


def rr03(ctx: RepoContext) -> CheckResult:
    names = ("CONTRIBUTING.md", "docs/CONTRIBUTING.md", ".github/CONTRIBUTING.md")
    path = next((name for name in names if (entry := ctx.entry(name)) and entry.readable and entry.size >= 100), None)
    return _result("RR03", 6 if path else 0, () if path else [_finding("CONTRIBUTING.md", "Contributor guidance is missing or shorter than 100 characters.")])


def rr04(ctx: RepoContext) -> CheckResult:
    issue = _nonempty(ctx, ("ISSUE_TEMPLATE.md",))
    if issue is None:
        issue = next((entry.path for entry in ctx if entry.path.startswith(".github/ISSUE_TEMPLATE/") and entry.readable and entry.size > 0), None)
    return _result("RR04", 4 if issue else 0, () if issue else [_finding(".github/ISSUE_TEMPLATE", "No issue template file is present.")])


_PYTHON_METADATA_FILES = ("pyproject.toml", "setup.cfg", "setup.py", "Pipfile")
_REQUIREMENTS_FILE = re.compile(r"requirements(?:-[^/]+)?\.txt")
_DEPENDENCY_GROUPS = (
    "dependencies",
    "devDependencies",
    "optionalDependencies",
    "peerDependencies",
)


def _manifest_text(ctx: RepoContext, path: str) -> tuple[str | None, str | None]:
    """Return a manifest's text and a deterministic read error, if any."""

    if not ctx.has_file(path):
        return None, f"{path} is missing."
    text = ctx.read_text(path)
    if text is None:
        return None, f"{path} is unreadable or is not a supported text file."
    if not text.strip():
        return None, f"{path} is empty."
    return text, None


def _json(ctx: RepoContext, path: str) -> dict[str, object] | None:
    text = ctx.read_text(path)
    if text is None:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _toml(ctx: RepoContext, path: str) -> dict[str, object] | None:
    text = ctx.read_text(path)
    if text is None:
        return None
    try:
        value = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: object) -> bool:
    return isinstance(value, list) and all(_nonempty_string(item) for item in value)


def _valid_python_toml(value: dict[str, object], path: str) -> list[str]:
    findings: list[str] = []
    if path == "Pipfile":
        known_sections = ("packages", "dev-packages", "requires", "source", "scripts")
        if not any(section in value for section in known_sections):
            findings.append("Pipfile must contain a packages, dev-packages, requires, source, or scripts table.")
        for section in ("packages", "dev-packages"):
            if section in value and not isinstance(value[section], dict):
                findings.append(f"Pipfile [{section}] must be a table.")
        if "source" in value and not isinstance(value["source"], list):
            findings.append("Pipfile [[source]] must be an array of tables.")
        if "requires" in value and not isinstance(value["requires"], dict):
            findings.append("Pipfile [requires] must be a table.")
        return findings

    project = value.get("project")
    if not isinstance(project, dict):
        tool = value.get("tool")
        poetry = tool.get("poetry") if isinstance(tool, dict) else None
        if isinstance(poetry, dict):
            if not _nonempty_string(poetry.get("name")):
                findings.append("pyproject.toml [tool.poetry] needs a non-empty name.")
            if not _nonempty_string(poetry.get("version")):
                findings.append("pyproject.toml [tool.poetry] needs a non-empty version.")
            return findings
        findings.append("pyproject.toml must contain a [project] table with package metadata.")
        return findings

    if not _nonempty_string(project.get("name")):
        findings.append("pyproject.toml [project] needs a non-empty name.")
    dynamic = project.get("dynamic", [])
    if dynamic and (not _string_list(dynamic) or "version" not in dynamic):
        findings.append("pyproject.toml [project].dynamic must be a string list containing version when version is omitted.")
    if not _nonempty_string(project.get("version")) and "version" not in dynamic:
        findings.append("pyproject.toml [project] needs a non-empty version or dynamic = [\"version\"].")
    if "dependencies" in project and not _string_list(project["dependencies"]):
        findings.append("pyproject.toml [project].dependencies must be a list of requirement strings.")
    optional = project.get("optional-dependencies")
    if optional is not None:
        if not isinstance(optional, dict) or not all(_string_list(item) for item in optional.values()):
            findings.append("pyproject.toml [project].optional-dependencies must map names to requirement lists.")
    if "requires-python" in project and not _nonempty_string(project["requires-python"]):
        findings.append("pyproject.toml [project].requires-python must be a non-empty string.")
    build_system = value.get("build-system")
    if build_system is not None:
        if not isinstance(build_system, dict):
            findings.append("pyproject.toml [build-system] must be a table.")
        else:
            if "requires" in build_system and not _string_list(build_system["requires"]):
                findings.append("pyproject.toml [build-system].requires must be a list of strings.")
            if "build-backend" in build_system and not _nonempty_string(build_system["build-backend"]):
                findings.append("pyproject.toml [build-system].build-backend must be a non-empty string.")
    return findings


_MISSING = object()


def _static_ast_value(node: ast.AST, constants: dict[str, object]) -> object:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id, _MISSING)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values = [_static_ast_value(item, constants) for item in node.elts]
        return _MISSING if any(item is _MISSING for item in values) else values
    if isinstance(node, ast.Dict):
        result: dict[object, object] = {}
        for key, value in zip(node.keys, node.values):
            if key is None:
                return _MISSING
            static_key = _static_ast_value(key, constants)
            static_value = _static_ast_value(value, constants)
            if static_key is _MISSING or static_value is _MISSING:
                return _MISSING
            try:
                result[static_key] = static_value
            except TypeError:
                return _MISSING
        return result
    return _MISSING


def _setup_call(text: str) -> tuple[ast.Call, dict[str, object]] | None:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    setup_names = {"setup"}
    constants: dict[str, object] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {"setuptools", "distutils.core"}:
            for alias in node.names:
                if alias.name == "setup":
                    setup_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Assign):
            value = _static_ast_value(node.value, constants)
            if value is not _MISSING:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        constants[target.id] = value
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id in setup_names:
            return node, constants
        if isinstance(node.func, ast.Attribute) and node.func.attr == "setup":
            return node, constants
    return None


def _valid_setup_cfg(text: str) -> list[str]:
    parser = configparser.RawConfigParser(interpolation=None, strict=True)
    try:
        parser.read_string(text)
    except configparser.Error as error:
        return [f"setup.cfg is not valid INI configuration: {error.__class__.__name__}."]
    if not parser.has_section("metadata"):
        return ["setup.cfg must contain a [metadata] section with a name."]
    findings: list[str] = []
    if not _nonempty_string(parser.get("metadata", "name", fallback=None)):
        findings.append("setup.cfg [metadata] needs a non-empty name.")
    if "version" in parser["metadata"] and not _nonempty_string(parser.get("metadata", "version")):
        findings.append("setup.cfg [metadata].version must be a non-empty value.")
    if parser.has_section("options") and "install_requires" in parser["options"]:
        if not parser.get("options", "install_requires").strip():
            findings.append("setup.cfg [options].install_requires cannot be empty when declared.")
    return findings


def _valid_setup_py(text: str) -> list[str]:
    call_data = _setup_call(text)
    if call_data is None:
        try:
            ast.parse(text)
        except SyntaxError as error:
            return [f"setup.py has invalid Python syntax at line {error.lineno or 1}."]
        return ["setup.py must contain a statically inspectable setup() call."]
    call, constants = call_data
    keywords = {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg is not None}
    findings: list[str] = []
    name = _static_ast_value(keywords["name"], constants) if "name" in keywords else _MISSING
    version = _static_ast_value(keywords["version"], constants) if "version" in keywords else _MISSING
    if not _nonempty_string(name):
        findings.append("setup.py setup() needs a statically declared non-empty name= value.")
    if not _nonempty_string(version):
        findings.append("setup.py setup() needs a statically declared non-empty version= value.")
    for key in ("install_requires", "extras_require"):
        if key in keywords and _static_ast_value(keywords[key], constants) is _MISSING:
            findings.append(f"setup.py setup() {key}= must be a static list or mapping for filesystem-only validation.")
    return findings


def _valid_package_json(value: dict[str, object]) -> list[str]:
    findings: list[str] = []
    if not _nonempty_string(value.get("name")):
        findings.append("package.json needs a non-empty string name.")
    if "version" in value and not _nonempty_string(value["version"]):
        findings.append("package.json version must be a non-empty string when present.")
    for group in _DEPENDENCY_GROUPS:
        if group in value:
            dependencies = value[group]
            if not isinstance(dependencies, dict):
                findings.append(f"package.json {group} must be an object of package names to specifiers.")
            elif any(not isinstance(name, str) or not name.strip() or not isinstance(specifier, str) or not specifier.strip() for name, specifier in dependencies.items()):
                findings.append(f"package.json {group} must contain non-empty string names and specifiers.")
    for field in ("scripts", "engines"):
        if field in value:
            section = value[field]
            if not isinstance(section, dict) or any(not isinstance(key, str) or not isinstance(item, str) for key, item in section.items()):
                findings.append(f"package.json {field} must be an object of string values.")
    for field in ("files", "bundledDependencies", "bundleDependencies"):
        if field in value and (not isinstance(value[field], list) or any(not _nonempty_string(item) for item in value[field])):
            findings.append(f"package.json {field} must be a list of non-empty strings.")
    if "private" in value and not isinstance(value["private"], bool):
        findings.append("package.json private must be a boolean when present.")
    return findings


def _manifest_findings(ctx: RepoContext, path: str) -> list[Finding]:
    text, error = _manifest_text(ctx, path)
    if error:
        return [_finding(path, error)]
    assert text is not None
    if path == "package.json":
        value = _json(ctx, path)
        if value is None:
            return [_finding(path, "package.json must contain a valid JSON object.")]
        return [_finding(path, detail) for detail in _valid_package_json(value)]
    if path == "setup.py":
        return [_finding(path, detail) for detail in _valid_setup_py(text)]
    if path == "setup.cfg":
        return [_finding(path, detail) for detail in _valid_setup_cfg(text)]
    value = _toml(ctx, path)
    if value is None:
        return [_finding(path, f"{path} must contain valid TOML.")]
    return [_finding(path, detail) for detail in _valid_python_toml(value, path)]


def _python_metadata_paths(ctx: RepoContext) -> tuple[str, ...]:
    return tuple(path for path in _PYTHON_METADATA_FILES if ctx.has_file(path))


def _python_marker_present(ctx: RepoContext) -> bool:
    return bool(_python_metadata_paths(ctx) or any(_REQUIREMENTS_FILE.fullmatch(entry.path) for entry in ctx))


def _python_manifest(ctx: RepoContext) -> bool:
    paths = _python_metadata_paths(ctx)
    return bool(paths) and not any(_manifest_findings(ctx, path) for path in paths)


def _node_manifest(ctx: RepoContext) -> bool:
    return ctx.has_file("package.json") and not _manifest_findings(ctx, "package.json")


def rr05(ctx: RepoContext) -> CheckResult:
    """Check for a pull-request template at one of the supported paths."""

    path = _nonempty(
        ctx,
        (
            ".github/PULL_REQUEST_TEMPLATE.md",
            "PULL_REQUEST_TEMPLATE.md",
            "docs/PULL_REQUEST_TEMPLATE.md",
        ),
    )
    return _result(
        "RR05",
        4 if path else 0,
        () if path else [_finding(".github/PULL_REQUEST_TEMPLATE.md", "No pull-request template is present.")],
    )


def _dependencies(ctx: RepoContext, ecosystem: str) -> bool:
    if ecosystem == "python":
        requirements = sorted(entry.path for entry in ctx if _REQUIREMENTS_FILE.fullmatch(entry.path))
        for path in requirements:
            text = ctx.read_text(path)
            if _requirements_have_dependencies(text):
                return True
        value = _toml(ctx, "pyproject.toml") if ctx.has_file("pyproject.toml") else None
        if ctx.has_file("pyproject.toml") and value is None:
            return True
        project = value.get("project") if value else None
        if isinstance(project, dict) and (project.get("dependencies") or project.get("optional-dependencies")):
            return True
        tool = value.get("tool") if value else None
        poetry = tool.get("poetry") if isinstance(tool, dict) else None
        if isinstance(poetry, dict):
            for section in ("dependencies", "dev-dependencies"):
                values = poetry.get(section)
                if isinstance(values, dict) and any(str(name).casefold() != "python" for name in values):
                    return True
        if ctx.has_file("setup.cfg"):
            parser = _setup_cfg_parser(ctx.read_text("setup.cfg"))
            if parser is None:
                return True
            if parser.has_section("options") and parser.get("options", "install_requires", fallback="").strip():
                return True
            if parser.has_section("options.extras_require") and parser.items("options.extras_require"):
                return True
        if ctx.has_file("setup.py"):
            known, names = _setup_dependency_names(ctx.read_text("setup.py"))
            if not known or names:
                return True
        pipfile = _toml(ctx, "Pipfile") if ctx.has_file("Pipfile") else None
        if ctx.has_file("Pipfile") and pipfile is None:
            return True
        return isinstance(pipfile, dict) and any(
            isinstance(pipfile.get(name), dict) and bool(pipfile[name])
            for name in ("packages", "dev-packages")
        )
    package = _json(ctx, "package.json") if ctx.has_file("package.json") else None
    if ctx.has_file("package.json") and package is None:
        return True
    if not isinstance(package, dict):
        return False
    for name in (*_DEPENDENCY_GROUPS, "bundledDependencies", "bundleDependencies"):
        if name not in package:
            continue
        value = package[name]
        if isinstance(value, dict):
            if value:
                return True
        elif isinstance(value, list):
            if value:
                return True
        elif value is not None:
            return True
    return False


def _setup_cfg_parser(text: str | None) -> configparser.RawConfigParser | None:
    if text is None:
        return None
    parser = configparser.RawConfigParser(interpolation=None, strict=True)
    try:
        parser.read_string(text)
    except configparser.Error:
        return None
    return parser


_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9_.-]*)")


def _dependency_name(value: str) -> str | None:
    match = _REQUIREMENT_NAME.match(value)
    return match.group(1).casefold() if match else None


def _setup_dependency_names(text: str | None) -> tuple[bool, set[str]]:
    if text is None:
        return False, set()
    call_data = _setup_call(text)
    if call_data is None:
        return False, set()
    call, constants = call_data
    keywords = {keyword.arg: keyword.value for keyword in call.keywords if keyword.arg is not None}
    names: set[str] = set()
    for key in ("install_requires", "extras_require"):
        if key not in keywords:
            continue
        value = _static_ast_value(keywords[key], constants)
        if isinstance(value, dict):
            values = [item for group in value.values() if isinstance(group, list) for item in group]
        elif isinstance(value, list):
            values = value
        else:
            return False, set()
        for item in values:
            if isinstance(item, str) and (name := _dependency_name(item)):
                names.add(name)
    return True, names


def _requirement_entries(text: str | None) -> list[str]:
    if text is None:
        return []
    return [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]


def _requirements_have_dependencies(text: str | None) -> bool:
    allowed_options = (
        "--hash=", "--index-url", "--extra-index-url", "--trusted-host",
        "--find-links", "--no-index", "--require-hashes",
    )
    return any(not line.startswith(allowed_options) for line in _requirement_entries(text))


def _requirements_locked(ctx: RepoContext) -> bool:
    paths = sorted(entry.path for entry in ctx if _REQUIREMENTS_FILE.fullmatch(entry.path))
    if not paths:
        return False
    for path in paths:
        if not _valid_requirements_lock(ctx.read_text(path)):
            return False
    return True


def _valid_requirements_lock(text: str | None) -> bool:
    entries = _requirement_entries(text)
    if not entries:
        return False
    dependencies = 0
    allowed_options = (
        "--hash=", "--index-url", "--extra-index-url", "--trusted-host",
        "--find-links", "--no-index", "--require-hashes",
    )
    for line in entries:
        logical = line.rstrip("\\").strip()
        if logical.startswith(allowed_options):
            continue
        if logical.startswith(("-r", "--requirement", "-c", "--constraint", "-e", "--editable")):
            return False
        if logical.startswith("-"):
            return False
        requirement_text, _, _marker = logical.partition(";")
        parts = requirement_text.split()
        requirement = parts[0] if parts else ""
        if any(not part.startswith("--hash=") for part in parts[1:]):
            return False
        if not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[^\]]+\])?\s*==\s*(?![=\s])[^\s;#\\]+",
            requirement,
        ):
            return False
        dependencies += 1
    return dependencies > 0


def _lock_paths(ctx: RepoContext, ecosystem: str) -> tuple[str, ...]:
    if ecosystem == "python":
        requirements = sorted(entry.path for entry in ctx if _REQUIREMENTS_FILE.fullmatch(entry.path))
        for name in ("uv.lock", "poetry.lock", "Pipfile.lock", "pdm.lock"):
            if ctx.has_file(name):
                return (name,)
        return tuple(requirements)
    for name in ("package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml"):
        if ctx.has_file(name):
            return (name,)
    return ()


def _python_declared_names(ctx: RepoContext) -> set[str]:
    names: set[str] = set()
    for path in sorted(entry.path for entry in ctx if _REQUIREMENTS_FILE.fullmatch(entry.path)):
        for line in _requirement_entries(ctx.read_text(path)):
            if name := _dependency_name(line):
                names.add(name)
    value = _toml(ctx, "pyproject.toml") if ctx.has_file("pyproject.toml") else None
    project = value.get("project") if value else None
    if isinstance(project, dict):
        values = list(project.get("dependencies", [])) if isinstance(project.get("dependencies"), list) else []
        optional = project.get("optional-dependencies")
        if isinstance(optional, dict):
            values.extend(item for group in optional.values() if isinstance(group, list) for item in group)
        names.update(name for item in values if isinstance(item, str) if (name := _dependency_name(item)))
    tool = value.get("tool") if value else None
    poetry = tool.get("poetry") if isinstance(tool, dict) else None
    if isinstance(poetry, dict):
        for section in ("dependencies", "dev-dependencies"):
            values = poetry.get(section)
            if isinstance(values, dict):
                names.update(str(name).casefold() for name in values if str(name).casefold() != "python")
    parser = _setup_cfg_parser(ctx.read_text("setup.cfg")) if ctx.has_file("setup.cfg") else None
    if parser is not None:
        if parser.has_section("options"):
            names.update(name for line in _requirement_entries(parser.get("options", "install_requires", fallback="")) if (name := _dependency_name(line)))
        if parser.has_section("options.extras_require"):
            for _, value in parser.items("options.extras_require"):
                names.update(name for line in _requirement_entries(value) if (name := _dependency_name(line)))
    known, setup_names = _setup_dependency_names(ctx.read_text("setup.py")) if ctx.has_file("setup.py") else (True, set())
    if known:
        names.update(setup_names)
    pipfile = _toml(ctx, "Pipfile") if ctx.has_file("Pipfile") else None
    if isinstance(pipfile, dict):
        for section in ("packages", "dev-packages"):
            packages = pipfile.get(section)
            if isinstance(packages, dict):
                names.update(str(name).casefold() for name in packages)
    return names


def _node_declared_names(ctx: RepoContext) -> set[str]:
    package = _json(ctx, "package.json") if ctx.has_file("package.json") else None
    if not isinstance(package, dict):
        return set()
    names: set[str] = set()
    for group in _DEPENDENCY_GROUPS:
        values = package.get(group)
        if isinstance(values, dict):
            names.update(str(name) for name in values)
    for group in ("bundledDependencies", "bundleDependencies"):
        values = package.get(group)
        if isinstance(values, list):
            names.update(str(name) for name in values)
    return names


def _lock_packages(value: dict[str, object]) -> tuple[bool, str, set[str]]:
    packages = value.get("package")
    if not isinstance(packages, list) or not packages:
        return False, "lockfile must contain one or more [[package]] records.", set()
    names: set[str] = set()
    for package in packages:
        if not isinstance(package, dict) or not _nonempty_string(package.get("name")):
            return False, "each lockfile package record needs a non-empty name.", names
        names.add(str(package["name"]).casefold())
        if not (
            _nonempty_string(package.get("version"))
            or _nonempty_string(package.get("git"))
            or _nonempty_string(package.get("path"))
            or isinstance(package.get("source"), dict)
        ):
            return False, "each lockfile package record needs a version or static source.", names
    return True, "", names


def _valid_toml_lock(
    ctx: RepoContext,
    path: str,
    declared: set[str],
) -> tuple[bool, str]:
    text, error = _manifest_text(ctx, path)
    if error:
        return False, error
    value = _toml(ctx, path)
    if value is None:
        return False, f"{path} must contain valid TOML."
    if path == "uv.lock":
        if isinstance(value.get("version"), bool) or not isinstance(value.get("version"), int):
            return False, "uv.lock needs an integer version field."
    elif path == "poetry.lock":
        metadata = value.get("metadata")
        if not isinstance(metadata, dict) or not _nonempty_string(metadata.get("lock-version")):
            return False, "poetry.lock needs a [metadata] table with lock-version."
    elif path == "pdm.lock":
        metadata = value.get("metadata")
        lock_version = value.get("lock_version")
        if lock_version is None and isinstance(metadata, dict):
            lock_version = metadata.get("lock_version")
        if not _nonempty_string(lock_version) and not isinstance(lock_version, int):
            return False, "pdm.lock needs a non-empty lock_version field."
    valid, detail, names = _lock_packages(value)
    if not valid:
        return False, detail
    missing = sorted(declared - names)
    if missing:
        return False, f"{path} does not cover declared dependencies: {', '.join(missing)}."
    return True, ""


def _valid_node_lock(lock: dict[str, object] | None) -> bool:
    """Accept only object-shaped npm lockfiles with dependency records."""

    if lock is None:
        return False
    version = lock.get("lockfileVersion")
    if isinstance(version, bool) or not isinstance(version, int) or version not in {1, 2, 3}:
        return False
    packages = lock.get("packages")
    dependencies = lock.get("dependencies")
    if packages is not None:
        if not isinstance(packages, dict):
            return False
        if any(
            not _valid_node_package_record(item)
            for key, item in packages.items()
            if key != ""
        ):
            return False
    if dependencies is not None:
        if not isinstance(dependencies, dict):
            return False
        if any(not _valid_node_package_record(item) for item in dependencies.values()):
            return False
    package_entries = (
        [key for key in packages if key and key != ""]
        if isinstance(packages, dict)
        else []
    )
    return bool(package_entries or (isinstance(dependencies, dict) and dependencies))


def _valid_node_package_record(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("link") is True:
        return True
    return any(_nonempty_string(value.get(key)) for key in ("version", "resolved", "integrity"))


def _valid_pipfile_lock(ctx: RepoContext, path: str) -> bool:
    """Validate the required top-level structure of a Pipenv JSON lock."""

    lock = _json(ctx, path)
    if lock is None or not isinstance(lock.get("_meta"), dict):
        return False
    sections = [lock[name] for name in ("default", "develop") if name in lock]
    if not sections or not all(isinstance(section, dict) for section in sections):
        return False
    for section in sections:
        if not section:
            continue
        for package, record in section.items():
            if not isinstance(package, str) or not package.strip() or not isinstance(record, dict):
                return False
            if not any(
                _nonempty_string(record.get(key))
                for key in ("version", "path", "editable", "file")
            ):
                return False
    return any(bool(section) for section in sections)


def _node_lock_covers(lock: dict[str, object], declared: set[str]) -> set[str]:
    covered: set[str] = set()
    packages = lock.get("packages")
    if isinstance(packages, dict):
        for name in declared:
            suffix = f"node_modules/{name}"
            if any(str(path) == suffix or str(path).endswith(f"/{suffix}") for path in packages):
                covered.add(name)
    dependencies = lock.get("dependencies")
    if isinstance(dependencies, dict):
        covered.update(name for name in declared if name in dependencies)
    return covered


def _valid_yarn_lock(text: str | None, declared: set[str]) -> tuple[bool, str]:
    if text is None:
        return False, "yarn.lock is unreadable."
    active = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not active:
        return False, "yarn.lock is empty or contains only comments."
    stanzas: list[dict[str, object]] = []
    current: dict[str, bool] | None = None
    for line in active:
        stripped = line.strip()
        indented = line.startswith((" ", "\t"))
        if not indented:
            if not stripped.endswith(":"):
                return False, "yarn.lock contains a malformed top-level selector."
            key = stripped[:-1].strip().strip('"\'')
            current = {"metadata": key == "__metadata", "version": False, "names": set()}
            if not current["metadata"]:
                selectors = key.split(",")
                current["names"] = {
                    selector_name(selector.strip())
                    for selector in selectors
                    if selector_name(selector.strip())
                }
            stanzas.append(current)
            continue
        if current is None:
            return False, "yarn.lock contains an indented record without a selector."
        if re.match(r"^(?:version|resolution)\s*:\s*\S+", stripped):
            current["version"] = True
    packages = [stanza for stanza in stanzas if not stanza["metadata"]]
    if not packages or not all(stanza["version"] for stanza in packages):
        return False, "yarn.lock needs at least one package selector with a version or resolution."
    covered = set().union(*(stanza["names"] for stanza in packages))
    missing = sorted(declared - covered)
    if missing:
        return False, f"yarn.lock does not cover declared dependencies: {', '.join(missing)}."
    return True, ""


def _valid_pnpm_lock(text: str | None, declared: set[str]) -> tuple[bool, str]:
    if text is None:
        return False, "pnpm-lock.yaml is unreadable."
    active = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not active:
        return False, "pnpm-lock.yaml is empty or contains only comments."
    first = active[0]
    match = re.fullmatch(r"lockfileVersion\s*:\s*['\"]?(\d+(?:\.\d+)?)['\"]?\s*", first.strip())
    if not match:
        return False, "pnpm-lock.yaml needs a numeric lockfileVersion at the top level."
    sections: dict[str, int] = {}
    covered: set[str] = set()
    active_section: str | None = None
    pending_record_depths: list[int] = []
    for line in active:
        indentation = len(line) - len(line.lstrip())
        pending_record_depths = [depth for depth in pending_record_depths if indentation <= depth]
        if "\t" in line[: len(line) - len(line.lstrip())]:
            return False, "pnpm-lock.yaml must use spaces for indentation."
        if not re.match(r"^\s*(?:[^:#\"']+|['\"][^\"']+['\"])\s*:", line):
            return False, "pnpm-lock.yaml contains a malformed YAML mapping line."
        if not line.startswith((" ", "\t")):
            key = line.split(":", 1)[0].strip().strip('"\'')
            sections[key] = 0
            active_section = key
        elif sections:
            parent = active_section or next(reversed(sections))
            if parent in {"packages", "snapshots", "dependencies", "importers"} and line.strip().endswith(":"):
                sections[parent] += 1
                candidate = line.strip().split(":", 1)[0].strip('"\'')
                for name in declared:
                    if candidate == name or candidate.startswith(f"{name}@") or candidate.endswith(f"/{name}"):
                        covered.add(name)
                if parent in {"packages", "snapshots"} and "@" in candidate:
                    pending_record_depths.append(indentation)
    if not any(sections.get(name, 0) for name in ("packages", "snapshots", "dependencies", "importers")):
        return False, "pnpm-lock.yaml needs a non-empty packages, snapshots, dependencies, or importers section."
    if pending_record_depths:
        return False, "pnpm-lock.yaml contains a package record without a non-empty record body."
    missing = sorted(declared - covered)
    if missing:
        return False, f"pnpm-lock.yaml does not cover declared dependencies: {', '.join(missing)}."
    return True, ""


def selector_name(selector: str) -> str | None:
    selector = selector.strip().strip('"\'')
    if selector.startswith("@"):
        marker = selector.find("@", 1)
        return selector[:marker].casefold() if marker > 0 else selector.casefold()
    return selector.split("@", 1)[0].casefold() or None


def _validate_lock(ctx: RepoContext, path: str, ecosystem: str, declared: set[str]) -> tuple[bool, str]:
    if ecosystem == "python":
        if _REQUIREMENTS_FILE.fullmatch(path):
            text = ctx.read_text(path)
            if not _valid_requirements_lock(text):
                return False, "every requirements dependency must be pinned with == and the file must be non-empty"
            names = {
                name
                for line in _requirement_entries(text)
                if (name := _dependency_name(line))
            }
            missing = sorted(declared - names)
            return (not missing, f"{path} does not cover declared dependencies: {', '.join(missing)}." if missing else "")
        if path == "Pipfile.lock":
            if not _valid_pipfile_lock(ctx, path):
                return False, "Pipfile.lock must be valid JSON with non-empty _meta, default, or develop records."
            lock = _json(ctx, path) or {}
            covered = {
                str(name).casefold()
                for section in ("default", "develop")
                if isinstance(lock.get(section), dict)
                for name in lock[section]
            }
            missing = sorted(name for name in declared if name not in covered)
            return (not missing, f"Pipfile.lock does not cover declared dependencies: {', '.join(missing)}." if missing else "")
        return _valid_toml_lock(ctx, path, declared)
    if path in {"package-lock.json", "npm-shrinkwrap.json"}:
        lock = _json(ctx, path)
        if not _valid_node_lock(lock):
            return False, f"{path} must be valid JSON with lockfileVersion 1, 2, or 3 and dependency records."
        assert lock is not None
        missing = sorted(declared - _node_lock_covers(lock, declared))
        return (not missing, f"{path} does not cover declared dependencies: {', '.join(missing)}." if missing else "")
    if path == "yarn.lock":
        return _valid_yarn_lock(ctx.read_text(path), declared)
    return _valid_pnpm_lock(ctx.read_text(path), declared)


def _lock_status(ctx: RepoContext, ecosystem: str) -> tuple[bool | None, list[Finding]]:
    if not _dependencies(ctx, ecosystem):
        return None, []
    paths = _lock_paths(ctx, ecosystem)
    declared = _python_declared_names(ctx) if ecosystem == "python" else _node_declared_names(ctx)
    if not paths:
        return False, [_finding(".", f"{ecosystem.title()} dependencies are declared but no supported lockfile was found.")]
    findings: list[Finding] = []
    for path in paths:
        valid, detail = _validate_lock(ctx, path, ecosystem, declared)
        if not valid:
            findings.append(_finding(path, detail or "Lockfile is malformed or empty."))
    return (not findings, findings)


def _lock_ok(ctx: RepoContext, ecosystem: str) -> bool | None:
    return _lock_status(ctx, ecosystem)[0]


def _ecosystem_score(rule_id: str, ecosystems: tuple[str, ...], evaluator: Callable[[str], bool | None], missing_message: str) -> CheckResult:
    if not ecosystems:
        return _result(rule_id, 0, [_finding(".", missing_message)], message=missing_message, fixable=False)
    outcomes = [evaluator(ecosystem) for ecosystem in ecosystems]
    earned = round(CHECK_WEIGHTS[rule_id] * sum(outcome is not False for outcome in outcomes) / len(outcomes))
    if all(outcome is None for outcome in outcomes):
        return _result(rule_id, CHECK_WEIGHTS[rule_id], na=True)
    findings = [_finding(".", f"{ecosystem.title()} project does not satisfy this check.") for ecosystem, outcome in zip(ecosystems, outcomes) if outcome is False]
    return _result(rule_id, earned, findings)


def rr06(ctx: RepoContext, ecosystems: tuple[str, ...]) -> CheckResult:
    if not ecosystems:
        return _result(
            "RR06",
            0,
            [_finding(".", "No supported project manifest found.")],
            message="No supported project manifest found.",
            fixable=False,
        )
    outcomes: list[bool | None] = []
    findings: list[Finding] = []
    for ecosystem in ecosystems:
        outcome, ecosystem_findings = _lock_status(ctx, ecosystem)
        outcomes.append(outcome)
        findings.extend(ecosystem_findings)
    if all(outcome is None for outcome in outcomes):
        return _result("RR06", CHECK_WEIGHTS["RR06"], na=True)
    earned = round(CHECK_WEIGHTS["RR06"] * sum(outcome is not False for outcome in outcomes) / len(outcomes))
    return _result("RR06", earned, findings)


def _runtime_ok(ctx: RepoContext, ecosystem: str) -> bool:
    if ecosystem == "python":
        for name in (".python-version", "runtime.txt"):
            value = ctx.read_text(name)
            if value and re.fullmatch(r"\s*\d+\.\d+(?:\.\d+)?\s*", value):
                return True
        tools = ctx.read_text(".tool-versions")
        return bool(tools and re.search(r"(?m)^\s*python\s+\d+\.\d+(?:\.\d+)?\s*(?:#.*)?$", tools))
    for name in (".nvmrc", ".node-version"):
        value = ctx.read_text(name)
        if value and re.fullmatch(r"\s*v?\d+(?:\.\d+){0,2}\s*", value):
            return True
    package = _json(ctx, "package.json") or {}
    engines = package.get("engines")
    node_engine = engines.get("node") if isinstance(engines, dict) else None
    if isinstance(node_engine, str) and re.fullmatch(r"\s*v?\d+(?:\.\d+){0,2}\s*", node_engine):
        return True
    tools = ctx.read_text(".tool-versions")
    return bool(tools and re.search(r"(?m)^\s*nodejs\s+v?\d+(?:\.\d+){0,2}\s*(?:#.*)?$", tools))


def rr07(ctx: RepoContext, ecosystems: tuple[str, ...]) -> CheckResult:
    return _ecosystem_score("RR07", ecosystems, lambda ecosystem: _runtime_ok(ctx, ecosystem), "No supported project manifest found.")


def _root_dockerfile(ctx: RepoContext) -> tuple[str | None, str | None]:
    """Select one root container file without hiding an unreadable primary file."""

    for path in ("Dockerfile", "Containerfile"):
        entry = ctx.entry(path)
        if entry is not None:
            return path, ctx.read_text(entry)
    return None, None


def _dockerfile_instructions(text: str) -> tuple[tuple[str, int, str], ...]:
    """Return supported instructions while ignoring full-line comments."""

    instructions: list[tuple[str, int, str]] = []
    logical_lines: list[tuple[int, str]] = []
    pending = ""
    pending_line = 0
    heredoc_end: str | None = None
    for line_number, line in enumerate(text.splitlines(), 1):
        if heredoc_end is not None:
            if line.strip() == heredoc_end:
                heredoc_end = None
            continue
        stripped = line.lstrip()
        if pending:
            pending += stripped
        else:
            pending = line
            pending_line = line_number
        if pending.rstrip().endswith("\\"):
            pending = pending.rstrip()[:-1].rstrip() + " "
            continue
        logical_lines.append((pending_line, pending))
        pending = ""
        match = re.match(r"(?i)^\s*(?:RUN|COPY|ADD)\s+<<-?\s*([A-Za-z_][A-Za-z0-9_]*)", line)
        if match:
            heredoc_end = match.group(1)
    if pending:
        logical_lines.append((pending_line, pending))
    for line_number, line in logical_lines:
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.match(r"(?i)^(FROM|CMD|ENTRYPOINT)(?:\s+|$)(.*)$", stripped)
        if match:
            instructions.append((match.group(1).upper(), line_number, match.group(2).strip()))
    return tuple(instructions)


def _docker_image(body: str) -> str | None:
    """Extract a FROM image after static FROM flags such as --platform."""

    for token in body.split():
        if not token.startswith("--") and not token.startswith("#"):
            return token
    return None


def _docker_image_is_pinned(image: str | None) -> bool:
    if not image:
        return False
    if "@" in image:
        return bool(image.rsplit("@", 1)[1])
    last_component = image.rsplit("/", 1)[-1]
    if ":" not in last_component:
        return False
    tag = last_component.rsplit(":", 1)[1]
    return bool(tag) and tag.casefold() != "latest"


def _docker_command_is_present(body: str) -> bool:
    body = body.strip()
    if not body or body.startswith("#") or body in {"\\", "[]", "{}"}:
        return False
    if body.startswith("[") and body.endswith("]"):
        try:
            return bool(json.loads(body))
        except json.JSONDecodeError:
            return False
    return True


def rr08(ctx: RepoContext) -> CheckResult:
    path, text = _root_dockerfile(ctx)
    if path is None:
        return _result("RR08", 0, [_finding("Dockerfile", "Root Dockerfile or Containerfile is missing.")])
    if text is None or not text.strip():
        return _result("RR08", 0, [_finding(path, "Root Dockerfile or Containerfile is empty or unreadable.")])

    instructions = _dockerfile_instructions(text)
    from_instructions = [item for item in instructions if item[0] == "FROM"]
    command_instructions = [
        item for item in instructions
        if item[0] in {"CMD", "ENTRYPOINT"} and _docker_command_is_present(item[2])
    ]
    earned = 6
    findings: list[Finding] = []
    if not from_instructions:
        findings.append(_finding(path, "Dockerfile has no FROM instruction."))
    else:
        unpinned = [
            item for item in from_instructions
            if not _docker_image_is_pinned(_docker_image(item[2]))
        ]
        if not unpinned:
            earned += 3
        else:
            findings.extend(
                _finding(path, "FROM must use an explicit non-latest tag or digest.", line)
                for _, line, _ in unpinned
            )

    # A command in an earlier build stage is not part of the final image.
    final_stage = -1
    for index, (instruction, _, _) in enumerate(instructions):
        if instruction == "FROM":
            final_stage = index
    final_stage_instructions = instructions[final_stage + 1:] if final_stage >= 0 else ()
    final_command = any(
        instruction in {"CMD", "ENTRYPOINT"} and _docker_command_is_present(body)
        for instruction, _, body in final_stage_instructions
    )
    command_ok = final_command if final_stage >= 0 else bool(command_instructions)
    if command_ok:
        earned += 3
    else:
        findings.append(_finding(path, "Dockerfile has no CMD or ENTRYPOINT instruction."))
    return _result("RR08", earned, findings)


def _make_recipe_command(value: str) -> str | None:
    command = value.strip()
    while command and command[0] in "@+-":
        command = command[1:].lstrip()
    if not command or command.startswith("#"):
        return None
    return command


def _make_test_command(text: str) -> str | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^\s*test\s*:\s*(.*)$", line)
        if not match:
            continue
        inline_text = match.group(1).strip()
        inline = _make_recipe_command(inline_text[1:]) if inline_text.startswith(";") else None
        if inline:
            return inline
        for recipe in lines[index + 1:]:
            if recipe.lstrip().startswith("#"):
                continue
            if recipe.startswith(("\t", "    ")) and recipe.strip():
                command = _make_recipe_command(recipe)
                if command:
                    return command
                continue
            if recipe and not recipe.startswith((" ", "\t")):
                break
    return None


def _config_test_command(ctx: RepoContext) -> str | None:
    for path, command in (("pytest.ini", "pytest"), ("tox.ini", "tox"), ("noxfile.py", "nox")):
        if not _nonempty(ctx, (path,)):
            continue
        text = ctx.read_text(path)
        if text is None:
            continue
        if path == "noxfile.py":
            if re.search(r"(?m)^\s*(?:def|class)\s+(?:session|test)\b", text):
                return f"{command} ({path})"
        else:
            parser = configparser.RawConfigParser(interpolation=None)
            try:
                parser.read_string(text)
            except configparser.Error:
                continue
            if parser.sections():
                return f"{command} ({path})"

    setup_cfg = ctx.read_text("setup.cfg")
    if setup_cfg and re.search(r"(?im)^\s*\[tool:pytest\]\s*$", setup_cfg):
        return "pytest (setup.cfg [tool:pytest])"

    pyproject = ctx.read_text("pyproject.toml")
    if pyproject:
        match = re.search(r"(?im)^\s*\[tool\.(pytest|tox|nox)(?:[.\]])", pyproject)
        if match:
            command = match.group(1)
            try:
                document = tomllib.loads(pyproject)
            except tomllib.TOMLDecodeError:
                return None
            tool = document.get("tool") if isinstance(document, dict) else None
            if isinstance(tool, dict) and isinstance(tool.get(command), dict):
                return f"{command} (pyproject.toml [tool.{command}])"
    return None


def _test_command(ctx: RepoContext) -> str | None:
    make = _text(ctx, ("Makefile", "makefile", "GNUmakefile"))
    if make:
        command = _make_test_command(make[1])
        if command:
            return f"make test ({make[0]}: {command})"

    package = _json(ctx, "package.json")
    if package:
        scripts = package.get("scripts")
        command = scripts.get("test") if isinstance(scripts, dict) else None
        if isinstance(command, str) and command.strip() and "no test specified" not in command.casefold():
            return f"npm test (package.json scripts.test: {command.strip()})"

    return _config_test_command(ctx)


def rr09(ctx: RepoContext, ecosystems: tuple[str, ...]) -> CheckResult:
    command = _test_command(ctx)
    if command:
        return _result("RR09", 12, message=f"Test command: {command}")
    message = "No supported project manifest found." if not ecosystems else "No usable test command was found."
    return _result("RR09", 0, [_finding(".", message)], message=message)


_ENV_FILES = (".env.example", ".env.sample", ".env.template")
_SOURCE_SUFFIXES = {".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}
_ENV_NAME = r"[A-Z_][A-Z0-9_]*"
_PY_ENV = re.compile(r"\bos\.(?:environ(?:\.get)?|getenv)\s*(?:\[\s*[\"'](" + _ENV_NAME + r")[\"']|\(\s*[\"'](" + _ENV_NAME + r")[\"'])")
_NODE_ENV = re.compile(r"\bprocess\.env(?:\.((?:" + _ENV_NAME + r"))|\[\s*[\"'](" + _ENV_NAME + r")[\"'])")


def environment_reads(ctx: RepoContext) -> tuple[tuple[str, int, str], ...]:
    reads: list[tuple[str, int, str]] = []
    for entry in ctx:
        if PurePosixPath(entry.path).suffix.casefold() not in _SOURCE_SUFFIXES:
            continue
        text = ctx.read_indexed_text(entry)
        if text is None:
            continue
        for line_number, line in enumerate(text.splitlines(), 1):
            for pattern in (_PY_ENV, _NODE_ENV):
                match = pattern.search(line)
                if match:
                    name = next((group for group in match.groups() if group), None)
                    if name:
                        reads.append((entry.path, line_number, name))
                        break
    return tuple(reads)


def _env_keys(ctx: RepoContext) -> set[str]:
    keys: set[str] = set()
    for name in _ENV_FILES:
        text = ctx.read_text(name)
        if text is None:
            continue
        for line in text.splitlines():
            match = re.match(r"^\s*(" + _ENV_NAME + r")\s*=", line)
            if match:
                keys.add(match.group(1))
    return keys


def rr10(ctx: RepoContext) -> CheckResult:
    reads = environment_reads(ctx)
    if not reads:
        return _result("RR10", 6, na=True)
    present = _nonempty(ctx, _ENV_FILES)
    keys = _env_keys(ctx)
    earned = (3 if present else 0) + (3 if {name for _, _, name in reads}.issubset(keys) else 0)
    findings: list[Finding] = []
    if not present:
        findings.append(_finding(".env.example", "No example environment file is present."))
    for path, line, name in reads:
        if name not in keys:
            findings.append(_finding(path, f"Environment key {name} is not listed in an example env file.", line))
    return _result("RR10", earned, findings)


_LOCKFILE_NAMES = {
    "uv.lock", "poetry.lock", "Pipfile.lock", "pdm.lock", "package-lock.json",
    "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml",
}
_ABSOLUTE_PATHS = (
    re.compile(r"(?<![A-Za-z0-9_])/(?:Users|home)/[^/\s]+/"),
    re.compile(r"(?i)(?<![A-Za-z0-9_])[A-Z]:[\\/]Users[\\/][^\\/\s]+[\\/]"),
    re.compile(r"(?<![A-Za-z0-9_])\\\\(?:Users|home)\\\\[^\\\s]+\\\\"),
)


def rr11(ctx: RepoContext) -> CheckResult:
    findings: list[Finding] = []
    for entry in ctx:
        if entry.name in _LOCKFILE_NAMES or entry.path in _ENV_FILES:
            continue
        text = ctx.read_indexed_text(entry)
        if text is None:
            continue
        for line_number, line in enumerate(text.splitlines(), 1):
            if any(pattern.search(line) for pattern in _ABSOLUTE_PATHS):
                findings.append(_finding(entry.path, "Machine-specific absolute path found.", line_number))
                if len(findings) >= 20:
                    return _result("RR11", 0, findings)
    return _result("RR11", 0 if findings else 8, findings)


def rr12(ctx: RepoContext, ecosystems: tuple[str, ...]) -> CheckResult:
    text = ctx.read_text(".gitignore")
    if text is None or not text.strip():
        return _result("RR12", 0, [_finding(".gitignore", ".gitignore is missing or empty.")])
    lines = {line.strip().lstrip("/") for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")}
    env_ok = any(line == ".env" or line.startswith(".env/") or line == ".env*" for line in lines)
    basics_ok = True
    if "python" in ecosystems:
        basics_ok = basics_ok and any("__pycache__" in line or ".venv" in line for line in lines)
    if "node" in ecosystems:
        basics_ok = basics_ok and any("node_modules" in line for line in lines)
    if not ecosystems:
        basics_ok = False
    if env_ok and basics_ok:
        return _result("RR12", 4)
    details = []
    if not env_ok:
        details.append(_finding(".gitignore", ".env is not ignored."))
    if not basics_ok:
        details.append(_finding(".gitignore", "Ecosystem cache directory is not ignored."))
    return _result("RR12", 0, details)


def rr13(ctx: RepoContext) -> CheckResult:
    workflow = next((entry.path for entry in ctx if entry.path.startswith(".github/workflows/") and entry.readable and entry.size > 0), None)
    return _result("RR13", 6 if workflow else 0, () if workflow else [_finding(".github/workflows", "No CI workflow is present.")])


@dataclass(frozen=True)
class Rule:
    id: str
    run: Callable[..., CheckResult]


RULES: tuple[Rule, ...] = (
    Rule("RR01", rr01), Rule("RR02", rr02), Rule("RR03", rr03), Rule("RR04", rr04), Rule("RR05", rr05),
    Rule("RR06", rr06), Rule("RR07", rr07), Rule("RR08", rr08), Rule("RR09", rr09), Rule("RR10", rr10),
    Rule("RR11", rr11), Rule("RR12", rr12), Rule("RR13", rr13),
)


def run_checks(ctx: RepoContext, ecosystems: Iterable[str]) -> tuple[CheckResult, ...]:
    values = tuple(ecosystems)
    results: list[CheckResult] = []
    for rule in RULES:
        if rule.id in {"RR06", "RR07", "RR09", "RR12"}:
            results.append(rule.run(ctx, values))
        else:
            results.append(rule.run(ctx))
    return tuple(results)


__all__ = ["CHECK_FIXABLE", "CHECK_HINTS", "CHECK_MESSAGES", "CHECK_TITLES", "CHECK_WEIGHTS", "RULES", "environment_reads", "run_checks"]
