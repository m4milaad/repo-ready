"""RR01--RR13 pure filesystem checks.

Every function in this module accepts a :class:`~repo_ready.context.RepoContext`.
There is no process execution, package-manager invocation, network access, or
clock-dependent behavior in a check.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Callable, Iterable

from .context import FileEntry, RepoContext
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

CHECK_MESSAGES: dict[str, str] = {
    "RR01": "README documents requirements, setup, run, test, and rebuild.",
    "RR02": "A license file is present.",
    "RR03": "Contributor guidance is present.",
    "RR04": "Pull-request and issue templates are present.",
    "RR05": "Supported ecosystem metadata is valid and identifiable.",
    "RR06": "Declared dependencies have a usable lockfile.",
    "RR07": "Supported runtimes are pinned.",
    "RR08": "A rebuildable Dockerfile is present.",
    "RR09": "A deterministic test command is documented.",
    "RR10": "An example environment file documents configuration keys.",
    "RR11": "No likely hard-coded secrets are committed.",
    "RR12": "No machine-specific absolute paths are committed.",
    "RR13": "Environment reads are represented in the example configuration.",
}

_README_NAMES = ("README.md", "README.rst", "README.txt")
_LICENSE_NAMES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING", "COPYING.md")
_CONTRIBUTING_NAMES = ("CONTRIBUTING.md", "CONTRIBUTING.rst", "CONTRIBUTING.txt")
_DOCKER_NAMES = ("Dockerfile", "dockerfile")
_MAKE_NAMES = ("Makefile", "makefile", "GNUmakefile")


def _finding(file: str, detail: str, line: int | None = None) -> Finding:
    return Finding(file=file, line=line, detail=detail)


def _ordered_findings(findings: Iterable[Finding]) -> tuple[Finding, ...]:
    return tuple(sorted(findings, key=lambda item: (item.file, item.line or 0, item.detail)))


def _result(
    rule_id: str,
    score: int,
    max_score: int,
    findings: Iterable[Finding] = (),
    *,
    message: str | None = None,
) -> CheckResult:
    weight = CHECK_WEIGHTS[rule_id]
    score = max(0, min(weight, int(score)))
    max_score = max(0, min(weight, int(max_score)))
    if max_score == 0:
        status = "na"
    elif score >= max_score:
        status = "pass"
    elif score:
        status = "partial"
    else:
        status = "fail"
    return CheckResult(
        id=rule_id,
        message=message or CHECK_MESSAGES[rule_id],
        weight=weight,
        status=status,
        score=score,
        max_score=max_score,
        findings=_ordered_findings(findings),
    )


def _text(ctx: RepoContext, names: Iterable[str]) -> tuple[str, str] | None:
    for name in names:
        if not ctx.has_file(name):
            continue
        value = ctx.read_text(name)
        if value is not None:
            return name, value
    return None


def _nonempty(ctx: RepoContext, names: Iterable[str]) -> str | None:
    for name in names:
        entry = ctx.entry(name)
        if entry is not None and entry.size > 0 and not entry.too_large and not entry.binary:
            return name
    return None


def _line_for(text: str, expression: str) -> int | None:
    pattern = re.compile(expression, re.IGNORECASE)
    for number, line in enumerate(text.splitlines(), 1):
        if pattern.search(line):
            return number
    return None


def _headings(text: str) -> dict[str, int]:
    headings: dict[str, int] = {}
    for number, line in enumerate(text.splitlines(), 1):
        match = re.match(r"^\s*#{1,6}\s+(.+?)\s*#*\s*$", line)
        if match:
            title = re.sub(r"\s+", " ", match.group(1)).strip().casefold()
            headings[title] = number
    return headings


def _rr01(ctx: RepoContext) -> CheckResult:
    readme = _text(ctx, _README_NAMES)
    if readme is None:
        return _result("RR01", 0, 14, [_finding("README.md", "README file is missing.")])
    path, text = readme
    headings = _headings(text)
    sections: tuple[tuple[str, tuple[str, ...], int], ...] = (
        ("requirements", ("requirements", "prerequisites"), 3),
        ("setup", ("setup", "installation", "install"), 3),
        ("run", ("run", "usage"), 3),
        ("test", ("test", "testing"), 3),
        ("deploy / rebuild", ("deploy / rebuild", "deploy", "deployment", "rebuild"), 2),
    )
    score = 0
    findings: list[Finding] = []
    for label, aliases, points in sections:
        matching = next((headings[alias] for alias in aliases if alias in headings), None)
        if matching is None:
            findings.append(_finding(path, f"README is missing the {label} section."))
        else:
            score += points
    return _result("RR01", score, 14, findings)


def _rr02(ctx: RepoContext) -> CheckResult:
    path = _nonempty(ctx, _LICENSE_NAMES)
    findings = () if path else (_finding("LICENSE", "A non-empty license file is missing."),)
    return _result("RR02", 6 if path else 0, 6, findings)


def _rr03(ctx: RepoContext) -> CheckResult:
    path = _nonempty(ctx, _CONTRIBUTING_NAMES)
    findings = () if path else (_finding("CONTRIBUTING.md", "Contributor guidance is missing."),)
    return _result("RR03", 6 if path else 0, 6, findings)


def _rr04(ctx: RepoContext) -> CheckResult:
    pull = _nonempty(
        ctx,
        (
            ".github/PULL_REQUEST_TEMPLATE.md",
            ".github/PULL_REQUEST_TEMPLATE.txt",
            ".github/pull_request_template.md",
        ),
    )
    issue = next(
        (
            entry.path
            for entry in ctx
            if entry.path.startswith(".github/ISSUE_TEMPLATE/")
            and len(PurePosixPath(entry.path).parts) > 2
            and entry.size > 0
            and not entry.binary
            and not entry.too_large
        ),
        None,
    )
    findings: list[Finding] = []
    if pull is None:
        findings.append(_finding(".github/PULL_REQUEST_TEMPLATE.md", "Pull-request template is missing."))
    if issue is None:
        findings.append(_finding(".github/ISSUE_TEMPLATE", "Issue template is missing."))
    return _result("RR04", 4 if pull and issue else 0, 4, findings)


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


def _python_metadata(ctx: RepoContext) -> bool:
    if ctx.has_file("pyproject.toml"):
        project = _toml(ctx, "pyproject.toml")
        metadata = project.get("project") if project else None
        return isinstance(metadata, dict) and bool(metadata.get("name")) and bool(metadata.get("version"))
    return any(ctx.has_file(name) for name in ("setup.py", "setup.cfg", "Pipfile"))


def _node_metadata(ctx: RepoContext) -> bool:
    package = _json(ctx, "package.json")
    return package is not None and bool(package.get("name")) and bool(package.get("version"))


def _per_ecosystem(
    rule_id: str,
    ecosystems: tuple[str, ...],
    evaluator: Callable[[str], tuple[bool, tuple[Finding, ...]]],
    *,
    applicable: Callable[[str], bool] | None = None,
) -> CheckResult:
    usable = [ecosystem for ecosystem in ecosystems if applicable is None or applicable(ecosystem)]
    if not usable:
        return _result(rule_id, 0, 0)
    passed = 0
    findings: list[Finding] = []
    for ecosystem in usable:
        ok, ecosystem_findings = evaluator(ecosystem)
        if ok:
            passed += 1
        else:
            findings.extend(ecosystem_findings)
    weight = CHECK_WEIGHTS[rule_id]
    return _result(rule_id, round(weight * passed / len(usable)), weight, findings)


def _dependencies(ctx: RepoContext, ecosystem: str) -> bool:
    if ecosystem == "python":
        requirements = ctx.read_text("requirements.txt") if ctx.has_file("requirements.txt") else None
        if requirements and any(line.strip() and not line.lstrip().startswith("#") for line in requirements.splitlines()):
            return True
        project = _toml(ctx, "pyproject.toml")
        metadata = project.get("project") if project else None
        if isinstance(metadata, dict) and (
            bool(metadata.get("dependencies")) or bool(metadata.get("optional-dependencies"))
        ):
            return True
        pipfile = _toml(ctx, "Pipfile")
        return bool(pipfile) and any(
            isinstance(pipfile.get(group), dict) and pipfile[group]
            for group in ("packages", "dev-packages")
        )
    package = _json(ctx, "package.json")
    return bool(package) and any(isinstance(package.get(group), dict) and package[group] for group in (
        "dependencies", "devDependencies", "optionalDependencies"
    ))


def _lockfile_for(ctx: RepoContext, ecosystem: str) -> str | None:
    names = (
        ("uv.lock", "poetry.lock", "requirements.lock")
        if ecosystem == "python"
        else ("package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml")
    )
    return _nonempty(ctx, names)


def _valid_node_lock(ctx: RepoContext, path: str) -> bool:
    package = _json(ctx, "package.json") or {}
    declared = {
        name
        for group in ("dependencies", "devDependencies", "optionalDependencies")
        if isinstance(package.get(group), dict)
        for name in package[group]
    }
    lock = _json(ctx, path)
    if lock is None:
        # yarn and pnpm lockfiles are text formats and their presence is the
        # useful offline signal; package-lock JSON is validated above.
        return path not in {"package-lock.json", "npm-shrinkwrap.json"}
    if not declared:
        return True
    packages = lock.get("packages")
    installed = {
        str(name).removeprefix("node_modules/")
        for name in packages
        if str(name).startswith("node_modules/")
    } if isinstance(packages, dict) else set()
    dependencies = lock.get("dependencies")
    return declared.issubset(installed) or (
        isinstance(dependencies, dict) and declared.issubset(dependencies)
    )


def _lock_ok(ctx: RepoContext, ecosystem: str) -> tuple[bool, tuple[Finding, ...]]:
    path = _lockfile_for(ctx, ecosystem)
    if path is None:
        return False, (_finding("package-lock.json" if ecosystem == "node" else "requirements.lock", f"{ecosystem.title()} dependency lockfile is missing."),)
    if ecosystem == "node" and not _valid_node_lock(ctx, path):
        return False, (_finding(path, "Lockfile does not cover all declared dependencies."),)
    if ecosystem == "python" and path == "requirements.lock":
        text = ctx.read_text(path)
        if text is None or not any(
            line.strip() and not line.lstrip().startswith("#")
            for line in text.splitlines()
        ):
            return False, (_finding(path, "Python lockfile is empty."),)
    return True, ()


def _rr05(ctx: RepoContext, ecosystems: tuple[str, ...]) -> CheckResult:
    def evaluate(ecosystem: str) -> tuple[bool, tuple[Finding, ...]]:
        if ecosystem == "python":
            ok = _python_metadata(ctx)
            return ok, () if ok else (_finding("pyproject.toml", "Python project metadata is incomplete."),)
        if ecosystem == "node":
            ok = _node_metadata(ctx)
            return ok, () if ok else (_finding("package.json", "Node package metadata is incomplete."),)
        return True, ()

    return _per_ecosystem(
        "RR05",
        ecosystems,
        evaluate,
    )


def _rr06(ctx: RepoContext, ecosystems: tuple[str, ...]) -> CheckResult:
    return _per_ecosystem(
        "RR06",
        ecosystems,
        lambda ecosystem: _lock_ok(ctx, ecosystem),
        applicable=lambda ecosystem: _dependencies(ctx, ecosystem),
    )


def _runtime_ok(ctx: RepoContext, ecosystem: str) -> tuple[bool, tuple[Finding, ...]]:
    path = ".python-version" if ecosystem == "python" else ".nvmrc"
    value = ctx.read_text(path) if ctx.has_file(path) else None
    pattern = r"\s*\d+\.\d+(?:\.\d+)?\s*" if ecosystem == "python" else r"\s*v?\d+(?:\.\d+){0,2}\s*"
    if value is not None and re.fullmatch(pattern, value):
        return True, ()
    return False, (_finding(path, f"{ecosystem.title()} runtime is not pinned to a numeric version."),)


def _rr07(ctx: RepoContext, ecosystems: tuple[str, ...]) -> CheckResult:
    return _per_ecosystem("RR07", ecosystems, lambda ecosystem: _runtime_ok(ctx, ecosystem))


def _docker_score(ctx: RepoContext, ecosystem: str) -> tuple[int, tuple[Finding, ...]]:
    selected = _text(ctx, _DOCKER_NAMES)
    if selected is None:
        return 0, (_finding("Dockerfile", "Dockerfile is missing."),)
    path, text = selected
    score = 0
    findings: list[Finding] = []
    from_match = re.search(r"(?im)^\s*FROM\s+([^\s]+)", text)
    if from_match:
        score += 3
    else:
        findings.append(_finding(path, "Dockerfile has no FROM instruction."))
    if re.search(r"(?im)^\s*WORKDIR\s+\S+", text) and re.search(r"(?im)^\s*COPY\s+", text):
        score += 3
    else:
        findings.append(_finding(path, "Dockerfile should define WORKDIR and COPY instructions."))
    expected = "python" if ecosystem == "python" else "node"
    if from_match and expected in from_match.group(1).casefold():
        score += 2
    else:
        findings.append(_finding(path, f"Dockerfile base image should identify {expected}."))
    install_or_command = re.search(r"(?im)^\s*(?:RUN|CMD|ENTRYPOINT)\s+", text)
    if install_or_command:
        score += 4
    else:
        findings.append(_finding(path, "Dockerfile has no install, command, or entrypoint instruction."))
    return score, findings


def _rr08(ctx: RepoContext, ecosystems: tuple[str, ...]) -> CheckResult:
    if not ecosystems:
        return _result("RR08", 0, 0)
    scores: list[int] = []
    findings: list[Finding] = []
    for ecosystem in ecosystems:
        score, ecosystem_findings = _docker_score(ctx, ecosystem)
        scores.append(score)
        if score < CHECK_WEIGHTS["RR08"]:
            findings.extend(ecosystem_findings)
    return _result("RR08", round(sum(scores) / len(scores)), 12, findings)


def _test_ok(ctx: RepoContext, ecosystem: str) -> tuple[bool, tuple[Finding, ...]]:
    make = _text(ctx, _MAKE_NAMES)
    if make and re.search(r"(?m)^\s*(?:[A-Za-z0-9_.-]+\s*:\s*)?test\s*:", make[1]):
        return True, ()
    if ecosystem == "node":
        package = _json(ctx, "package.json")
        scripts = package.get("scripts") if package else None
        if isinstance(scripts, dict) and isinstance(scripts.get("test"), str) and scripts["test"].strip():
            return True, ()
    if ecosystem == "python":
        pyproject = ctx.read_text("pyproject.toml") if ctx.has_file("pyproject.toml") else None
        if pyproject and re.search(r"(?im)^\s*test\s*=\s*['\"][^'\"]+['\"]", pyproject):
            return True, ()
    return False, (_finding("Makefile", f"No {ecosystem} test command is documented."),)


def _rr09(ctx: RepoContext, ecosystems: tuple[str, ...]) -> CheckResult:
    return _per_ecosystem("RR09", ecosystems, lambda ecosystem: _test_ok(ctx, ecosystem))


def _env_keys(ctx: RepoContext) -> set[str]:
    selected = _text(ctx, (".env.example",))
    if selected is None:
        return set()
    return {
        match.group(1)
        for line in selected[1].splitlines()
        if (match := re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line))
    }


def _rr10(ctx: RepoContext) -> CheckResult:
    path = _nonempty(ctx, (".env.example",))
    keys = _env_keys(ctx)
    if path and keys:
        return _result("RR10", 6, 6)
    detail = "Example environment file is missing." if path is None else "Example environment file has no KEY=value entries."
    return _result("RR10", 0, 6, [_finding(".env.example", detail)])


_SECRET = re.compile(
    r"(?i)\b(?:api[_-]?key|secret(?:[_-]?key)?|token|password|passwd|private[_-]?key)\b\s*[:=]\s*(['\"]?)([^'\"\s,;]+)\1"
)
_PLACEHOLDERS = {"change-me", "changeme", "example", "placeholder", "test", "dummy", "null", "none"}


def _rr11(ctx: RepoContext) -> CheckResult:
    findings: list[Finding] = []
    for entry in ctx:
        if entry.path in {".env.example", ".repo-ready.toml"}:
            continue
        text = ctx.read_text(entry)
        if text is None:
            continue
        for line_number, line in enumerate(text.splitlines(), 1):
            match = _SECRET.search(line)
            if match and match.group(2).casefold() not in _PLACEHOLDERS and len(match.group(2)) >= 8:
                findings.append(_finding(entry.path, "Possible hard-coded secret value.", line_number))
    return _result("RR11", 0 if findings else 8, 8, findings)


_ABSOLUTE_PATHS = (
    re.compile(r"(?<![A-Za-z0-9_])/(?:Users|home|root|private/var|opt|tmp)/"),
    re.compile(r"(?i)(?<![A-Za-z0-9_])[A-Z]:[\\/]Users[\\/]"),
    re.compile(r"(?i)(?<![A-Za-z0-9_])[A-Z]:[\\/](?:Windows|Program Files)[\\/]"),
    re.compile(r"(?<![A-Za-z0-9_])\\\\(?:Users|home|root)\\\\"),
)


def _rr12(ctx: RepoContext) -> CheckResult:
    findings: list[Finding] = []
    for entry in ctx:
        text = ctx.read_text(entry)
        if text is None:
            continue
        for line_number, line in enumerate(text.splitlines(), 1):
            if any(pattern.search(line) for pattern in _ABSOLUTE_PATHS):
                findings.append(_finding(entry.path, "Machine-specific absolute path found.", line_number))
    return _result("RR12", 0 if findings else 4, 4, findings)


_ENV_READS = (
    re.compile(r"\bos\.(?:environ(?:\.get)?|getenv)\s*(?:\[\s*[\"']([^\"']+)|\(\s*[\"']([^\"']+))"),
    re.compile(r"\bprocess\.env(?:\.([A-Za-z_][A-Za-z0-9_]*)|\[\s*[\"']([^\"']+))"),
    re.compile(r"\bos\.environ\b|\bprocess\.env\b"),
)
_SOURCE_SUFFIXES = {".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".sh"}


def _environment_reads(ctx: RepoContext) -> list[tuple[str, int, str | None]]:
    reads: list[tuple[str, int, str | None]] = []
    for entry in ctx:
        if PurePosixPath(entry.path).suffix.casefold() not in _SOURCE_SUFFIXES:
            continue
        text = ctx.read_text(entry)
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for pattern in _ENV_READS:
                match = pattern.search(line)
                if match:
                    name = next((group for group in match.groups() if group), None)
                    reads.append((entry.path, number, name))
                    break
    return reads


def _rr13(ctx: RepoContext) -> CheckResult:
    reads = _environment_reads(ctx)
    if not reads:
        return _result("RR13", 0, 0)
    keys = _env_keys(ctx)
    findings: list[Finding] = []
    documented = 0
    for path, line, name in reads:
        if name and name in keys:
            documented += 1
        else:
            detail = "Environment read is not documented in .env.example."
            if name:
                detail = f"Environment key {name} is not documented in .env.example."
            findings.append(_finding(path, detail, line))
    return _result("RR13", round(6 * documented / len(reads)), 6, findings)


def rr01(ctx: RepoContext) -> CheckResult:
    return _rr01(ctx)


def rr02(ctx: RepoContext) -> CheckResult:
    return _rr02(ctx)


def rr03(ctx: RepoContext) -> CheckResult:
    return _rr03(ctx)


def rr04(ctx: RepoContext) -> CheckResult:
    return _rr04(ctx)


def rr05(ctx: RepoContext, ecosystems: Iterable[str] = ()) -> CheckResult:
    return _rr05(ctx, tuple(ecosystems) or tuple(_detected_ecosystems(ctx)))


def rr06(ctx: RepoContext, ecosystems: Iterable[str] = ()) -> CheckResult:
    return _rr06(ctx, tuple(ecosystems) or tuple(_detected_ecosystems(ctx)))


def rr07(ctx: RepoContext, ecosystems: Iterable[str] = ()) -> CheckResult:
    return _rr07(ctx, tuple(ecosystems) or tuple(_detected_ecosystems(ctx)))


def rr08(ctx: RepoContext, ecosystems: Iterable[str] = ()) -> CheckResult:
    return _rr08(ctx, tuple(ecosystems) or tuple(_detected_ecosystems(ctx)))


def rr09(ctx: RepoContext, ecosystems: Iterable[str] = ()) -> CheckResult:
    return _rr09(ctx, tuple(ecosystems) or tuple(_detected_ecosystems(ctx)))


def rr10(ctx: RepoContext) -> CheckResult:
    return _rr10(ctx)


def rr11(ctx: RepoContext) -> CheckResult:
    return _rr11(ctx)


def rr12(ctx: RepoContext) -> CheckResult:
    return _rr12(ctx)


def rr13(ctx: RepoContext) -> CheckResult:
    return _rr13(ctx)


def _detected_ecosystems(ctx: RepoContext) -> tuple[str, ...]:
    from .detector import detect_ecosystems

    return tuple(detect_ecosystems(ctx))


@dataclass(frozen=True)
class Rule:
    id: str
    run: Callable[[RepoContext, tuple[str, ...]], CheckResult]


def _global(rule: Callable[[RepoContext], CheckResult]) -> Callable[[RepoContext, tuple[str, ...]], CheckResult]:
    return lambda ctx, _ecosystems: rule(ctx)


RULES: tuple[Rule, ...] = (
    Rule("RR01", lambda ctx, _ecosystems: rr01(ctx)),
    Rule("RR02", lambda ctx, _ecosystems: rr02(ctx)),
    Rule("RR03", lambda ctx, _ecosystems: rr03(ctx)),
    Rule("RR04", lambda ctx, _ecosystems: rr04(ctx)),
    Rule("RR05", rr05),
    Rule("RR06", rr06),
    Rule("RR07", rr07),
    Rule("RR08", rr08),
    Rule("RR09", rr09),
    Rule("RR10", lambda ctx, _ecosystems: rr10(ctx)),
    Rule("RR11", lambda ctx, _ecosystems: rr11(ctx)),
    Rule("RR12", lambda ctx, _ecosystems: rr12(ctx)),
    Rule("RR13", lambda ctx, _ecosystems: rr13(ctx)),
)


def run_checks(ctx: RepoContext, ecosystems: Iterable[str]) -> tuple[CheckResult, ...]:
    ecosystem_tuple = tuple(ecosystems)
    return tuple(rule.run(ctx, ecosystem_tuple) for rule in RULES)


__all__ = [
    "CHECK_MESSAGES", "CHECK_WEIGHTS", "RULES", "rr01", "rr02", "rr03",
    "rr04", "rr05", "rr06", "rr07", "rr08", "rr09", "rr10", "rr11",
    "rr12", "rr13", "run_checks",
]
