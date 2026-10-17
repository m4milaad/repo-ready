"""The deterministic RR01--RR13 filesystem checks.

Checks never execute a project, call a package manager, open a socket, or use
the clock.  They consume one immutable :class:`RepoContext` file index.
"""

from __future__ import annotations

import json
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


def rr05(ctx: RepoContext) -> CheckResult:
    path = _nonempty(ctx, (".github/PULL_REQUEST_TEMPLATE.md", "PULL_REQUEST_TEMPLATE.md", "docs/PULL_REQUEST_TEMPLATE.md"))
    return _result("RR05", 4 if path else 0, () if path else [_finding(".github/PULL_REQUEST_TEMPLATE.md", "No pull-request template is present.")])


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


def _python_manifest(ctx: RepoContext) -> bool:
    if ctx.has_file("pyproject.toml"):
        value = _toml(ctx, "pyproject.toml")
        project = value.get("project") if value else None
        return isinstance(project, dict) and bool(project.get("name"))
    if ctx.has_file("setup.cfg"):
        return _toml(ctx, "setup.cfg") is not None or ctx.read_text("setup.cfg") is not None
    if ctx.has_file("setup.py"):
        text = ctx.read_text("setup.py")
        return text is not None and bool(re.search(r"\bsetup\s*\(", text))
    if ctx.has_file("Pipfile"):
        return _toml(ctx, "Pipfile") is not None
    return False


def _node_manifest(ctx: RepoContext) -> bool:
    value = _json(ctx, "package.json")
    return value is not None and isinstance(value.get("name"), str) and bool(value.get("name"))


def _dependencies(ctx: RepoContext, ecosystem: str) -> bool:
    if ecosystem == "python":
        requirements = next((entry.path for entry in ctx if re.fullmatch(r"requirements(?:-[^/]+)?\.txt", entry.path)), None)
        if requirements and any(line.strip() and not line.lstrip().startswith(('#', '-')) for line in (ctx.read_text(requirements) or "").splitlines()):
            return True
        value = _toml(ctx, "pyproject.toml")
        project = value.get("project") if value else None
        if isinstance(project, dict) and (project.get("dependencies") or project.get("optional-dependencies")):
            return True
        pipfile = _toml(ctx, "Pipfile")
        return isinstance(pipfile, dict) and any(isinstance(pipfile.get(name), dict) and pipfile[name] for name in ("packages", "dev-packages"))
    package = _json(ctx, "package.json") or {}
    return any(isinstance(package.get(name), dict) and package[name] for name in ("dependencies", "devDependencies", "optionalDependencies"))


def _requirements_locked(ctx: RepoContext) -> bool:
    paths = sorted(entry.path for entry in ctx if re.fullmatch(r"requirements(?:-[^/]+)?\.txt", entry.path))
    if not paths:
        return False
    for path in paths:
        text = ctx.read_text(path)
        if text is None:
            return False
        lines = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
        if any(line.startswith(('-r', '-e', '--')) or "==" not in line for line in lines):
            return False
    return True


def _lock_ok(ctx: RepoContext, ecosystem: str) -> bool | None:
    if not _dependencies(ctx, ecosystem):
        return None
    if ecosystem == "python":
        if _requirements_locked(ctx):
            return True
        for name in ("uv.lock", "poetry.lock", "Pipfile.lock", "pdm.lock"):
            if _nonempty(ctx, (name,)):
                if name == "Pipfile.lock" and _json(ctx, name) is None:
                    continue
                return True
        return False
    package = _json(ctx, "package.json") or {}
    declared = {name for group in ("dependencies", "devDependencies", "optionalDependencies") if isinstance(package.get(group), dict) for name in package[group]}
    path = _nonempty(ctx, ("package-lock.json", "yarn.lock", "pnpm-lock.yaml"))
    if path is None:
        return False
    if path == "package-lock.json":
        lock = _json(ctx, path)
        if lock is None:
            return False
        if not declared:
            return True
        packages = lock.get("packages")
        installed = {str(name).removeprefix("node_modules/") for name in packages if str(name).startswith("node_modules/")} if isinstance(packages, dict) else set()
        dependencies = lock.get("dependencies")
        return declared.issubset(installed) or (isinstance(dependencies, dict) and declared.issubset(dependencies))
    return True


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
    return _ecosystem_score("RR06", ecosystems, lambda ecosystem: _lock_ok(ctx, ecosystem), "No supported project manifest found.")


def _runtime_ok(ctx: RepoContext, ecosystem: str) -> bool:
    if ecosystem == "python":
        value = _text(ctx, (".python-version", "runtime.txt"))
        if value and re.fullmatch(r"\s*\d+\.\d+(?:\.\d+)?\s*", value[1]):
            return True
        tools = ctx.read_text(".tool-versions")
        return bool(tools and re.search(r"(?m)^\s*python\s+\d+\.\d+(?:\.\d+)?\s*$", tools))
    value = _text(ctx, (".nvmrc", ".node-version"))
    if value and re.fullmatch(r"\s*v?\d+(?:\.\d+){0,2}\s*", value[1]):
        return True
    package = _json(ctx, "package.json") or {}
    engines = package.get("engines")
    if isinstance(engines, dict) and isinstance(engines.get("node"), str) and engines["node"].strip():
        return True
    tools = ctx.read_text(".tool-versions")
    return bool(tools and re.search(r"(?m)^\s*nodejs\s+\S+", tools))


def rr07(ctx: RepoContext, ecosystems: tuple[str, ...]) -> CheckResult:
    return _ecosystem_score("RR07", ecosystems, lambda ecosystem: _runtime_ok(ctx, ecosystem), "No supported project manifest found.")


def rr08(ctx: RepoContext) -> CheckResult:
    selected = _text(ctx, ("Dockerfile", "Containerfile"))
    if selected is None:
        return _result("RR08", 0, [_finding("Dockerfile", "Root Dockerfile or Containerfile is missing.")])
    path, text = selected
    earned = 6
    findings: list[Finding] = []
    from_match = re.search(r"(?im)^\s*FROM\s+([^\s#]+)", text)
    if from_match:
        image = from_match.group(1)
        tag_or_digest = "@" in image or (":" in image.rsplit("/", 1)[-1] and not image.casefold().endswith(":latest"))
        if tag_or_digest:
            earned += 3
        else:
            findings.append(_finding(path, "FROM must use an explicit non-latest tag or digest.", _line_number(text, from_match.start())))
    else:
        findings.append(_finding(path, "Dockerfile has no FROM instruction."))
    command = re.search(r"(?im)^\s*(?:CMD|ENTRYPOINT)\s+\S+", text)
    if command:
        earned += 3
    else:
        findings.append(_finding(path, "Dockerfile has no CMD or ENTRYPOINT instruction."))
    return _result("RR08", earned, findings)


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _make_test_command(text: str) -> str | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^\s*test\s*:\s*(.*)$", line)
        if not match:
            continue
        inline = match.group(1).strip()
        if inline:
            return inline
        for recipe in lines[index + 1:]:
            if recipe.startswith(("\t", "    ")) and recipe.strip():
                return recipe.strip()
            if recipe and not recipe.startswith((" ", "\t")):
                break
    return None


def _test_command(ctx: RepoContext) -> str | None:
    make = _text(ctx, ("Makefile", "makefile", "GNUmakefile"))
    if make:
        command = _make_test_command(make[1])
        if command:
            return f"make test ({command})"
    package = _json(ctx, "package.json")
    if package:
        scripts = package.get("scripts")
        command = scripts.get("test") if isinstance(scripts, dict) else None
        if isinstance(command, str) and command.strip() and "no test specified" not in command.casefold():
            return f"npm test ({command.strip()})"
    for name in ("pytest.ini", "tox.ini", "noxfile.py"):
        if _nonempty(ctx, (name,)):
            return name
    pyproject = ctx.read_text("pyproject.toml")
    if pyproject and re.search(r"(?im)^\s*\[tool\.(?:pytest|tox|nox)", pyproject):
        return "pytest configuration in pyproject.toml"
    return None


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
        text = ctx.read_text(entry)
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
        text = ctx.read_text(entry)
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
