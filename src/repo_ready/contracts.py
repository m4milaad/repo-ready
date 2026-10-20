"""Static cross-file contract diagnostics.

The readiness score intentionally remains a small, stable checklist.  This
module adds a separate evidence graph for contradictions between the files
that make up a repository's setup contract.  It never executes a target
repository, invokes a package manager, or leaves the filesystem boundary
provided by :class:`~repo_ready.context.RepoContext`.
"""

from __future__ import annotations

import fnmatch
import json
import posixpath
import re
import shlex
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

from .checks import (
    _docker_image,
    _dockerfile_instructions,
    _lock_paths,
    _root_dockerfile,
    _text,
    _validate_lock,
)
from .context import RepoContext
from .detector import detect_ecosystems
from .ecosystems import (
    ECOSYSTEM_PROFILES,
    conventional_test_command,
    declared_dependencies,
    runtime_values,
)


EvidenceKind = Literal[
    "document",
    "command",
    "manifest",
    "dependency",
    "lockfile",
    "runtime",
    "container",
    "workflow",
    "artifact",
]
ContractStatus = Literal["satisfied", "mismatch", "missing", "unknown"]
DiagnosticSeverity = Literal["info", "warning", "error"]


@dataclass(frozen=True)
class Evidence:
    """One bounded, repository-relative fact used by the contract graph."""

    id: str
    kind: EvidenceKind
    subject: str
    file: str
    line: int | None
    value: str
    normalized: str
    attributes: tuple[tuple[str, str], ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind,
            "subject": self.subject,
            "file": self.file,
            "line": self.line,
            "value": self.value,
            "normalized": self.normalized,
            "attributes": {key: value for key, value in self.attributes},
        }


@dataclass(frozen=True)
class ContractEdge:
    """A deterministic relationship between two evidence records."""

    id: str
    relation: str
    source: str
    target: str | None
    status: ContractStatus
    expected: str | None
    actual: str | None
    detail: str

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "relation": self.relation,
            "source": self.source,
            "target": self.target,
            "status": self.status,
            "expected": self.expected,
            "actual": self.actual,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ContractDiagnostic:
    """A non-scoring explanation of a cross-file inconsistency."""

    code: str
    severity: DiagnosticSeverity
    message: str
    evidence: tuple[str, ...]
    related_checks: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "evidence": list(self.evidence),
            "related_checks": list(self.related_checks),
        }


@dataclass(frozen=True)
class ContractGraph:
    """Versioned static evidence graph, separate from the readiness score."""

    version: int
    evidence: tuple[Evidence, ...]
    edges: tuple[ContractEdge, ...]
    diagnostics: tuple[ContractDiagnostic, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "evidence": [item.as_dict() for item in self.evidence],
            "edges": [item.as_dict() for item in self.edges],
            "diagnostics": [item.as_dict() for item in self.diagnostics],
        }


GRAPH_VERSION = 1
_COMMAND_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("make-test", re.compile(r"^make\s+(?:-s\s+)?test(?:\s|$)")),
    ("pytest", re.compile(r"^(?:(?:python(?:3(?:\.\d+)?)?|py)\s+-m\s+)?pytest(?:\s|$)")),
    ("tox", re.compile(r"^tox(?:\s|$)")),
    ("nox", re.compile(r"^nox(?:\s|$)")),
    ("npm-test", re.compile(r"^npm\s+(?:run\s+)?test(?:\s|$)")),
    ("yarn-test", re.compile(r"^yarn\s+test(?:\s|$)")),
    ("pnpm-test", re.compile(r"^pnpm\s+(?:run\s+)?test(?:\s|$)")),
    ("go-test", re.compile(r"^go\s+test(?:\s|$)")),
    ("rust-test", re.compile(r"^(?:cargo|cargo-nextest)\s+test(?:\s|$)")),
    ("dotnet-test", re.compile(r"^dotnet\s+test(?:\s|$)")),
    ("maven-test", re.compile(r"^(?:\.\/)?mvn(?:w)?\s+test(?:\s|$)")),
    ("gradle-test", re.compile(r"^(?:\.\/)?gradlew?\s+test(?:\s|$)")),
    ("ruby-test", re.compile(r"^bundle\s+exec\s+(?:rake\s+test|rspec)(?:\s|$)")),
)
_INSTALL_COMMANDS = re.compile(
    r"^(?:python(?:3(?:\.\d+)?)?\s+-m\s+pip|pip|npm|yarn|pnpm|uv|poetry|go|cargo|dotnet)\s+(?:install|ci|sync|download|fetch|restore|mod\s+download)\b"
)
_RUNTIME_VERSION = re.compile(r"(?<!\d)(\d+)(?:\.(\d+))?(?:\.(\d+))?(?!\d)")
_PYTHON_FILES = (".python-version", "runtime.txt")
_NODE_FILES = (".nvmrc", ".node-version")


def _normalize_command(value: str) -> str:
    value = value.strip()
    value = re.sub(r"^[\@\+\-]+\s*", "", value)
    value = re.sub(r"\s+", " ", value)
    return value


def _command_family(value: str) -> str | None:
    normalized = _normalize_command(value)
    for family, pattern in _COMMAND_PATTERNS:
        if pattern.search(normalized):
            return family
    return None


def _runtime_version(value: str, *, node: bool = False) -> str | None:
    match = _RUNTIME_VERSION.search(value.strip().lstrip("v"))
    if not match:
        return None
    parts = [part for part in match.groups() if part is not None]
    if not node and len(parts) < 2:
        return None
    return ".".join(parts)


def _attrs(**values: object) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((key, str(value)) for key, value in values.items() if value is not None))


def _evidence_id(kind: str, subject: str, file: str, line: int | None) -> str:
    return f"{kind}:{subject}:{file}:{line if line is not None else 0}"


def _add_evidence(
    items: list[Evidence],
    *,
    kind: EvidenceKind,
    subject: str,
    file: str,
    line: int | None,
    value: str,
    normalized: str | None = None,
    attributes: tuple[tuple[str, str], ...] = (),
) -> Evidence:
    bounded = value.strip()[:512]
    item = Evidence(
        id=_evidence_id(kind, subject, file, line),
        kind=kind,
        subject=subject,
        file=file,
        line=line,
        value=bounded,
        normalized=(normalized if normalized is not None else bounded)[:512],
        attributes=attributes,
    )
    if item.id not in {existing.id for existing in items}:
        items.append(item)
    return item


def _readable_text(context: RepoContext, path: str) -> str | None:
    return context.read_text(path)


def _manifest_json(context: RepoContext) -> dict[str, object] | None:
    text = _readable_text(context, "package.json")
    if text is None:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _manifest_toml(context: RepoContext) -> dict[str, object] | None:
    text = _readable_text(context, "pyproject.toml")
    if text is None:
        return None
    try:
        value = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _command_from_line(line: str) -> tuple[str, str] | None:
    candidate = line.strip().strip("`*")
    candidate = re.sub(r"^(?:[-+]\s+|\d+[.)]\s+)", "", candidate)
    candidate = _normalize_command(candidate)
    family = _command_family(candidate)
    return (family, candidate) if family else None


def _collect_readme(context: RepoContext, evidence: list[Evidence]) -> None:
    selected = _text(context, ("README.md", "README.rst", "README.txt"))
    if selected is None:
        return
    path, text = selected
    headings: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), 1):
        match = re.match(r"^\s*#{1,6}\s+(.+?)\s*#*\s*$", line)
        if match:
            headings.append((number, match.group(1).strip().casefold()))
    sections = {"test", "testing", "run", "usage", "requirements", "runtime", "setup", "install", "installation", "rebuild", "deploy"}
    lines = text.splitlines()
    for index, (start, title) in enumerate(headings):
        if title not in sections:
            continue
        end = headings[index + 1][0] - 1 if index + 1 < len(headings) else len(lines)
        for number in range(start, end + 1):
            command = _command_from_line(lines[number - 1])
            if command is not None:
                family, value = command
                _add_evidence(
                    evidence,
                    kind="command",
                    subject=f"readme-{family}",
                    file=path,
                    line=number,
                    value=value,
                    normalized=family,
                    attributes=_attrs(source="readme", section=title, family=family),
                )
            stripped = lines[number - 1].strip()
            if title in {"requirements", "runtime"}:
                runtime = _runtime_version(stripped, node="node" in stripped.casefold())
                if runtime:
                    ecosystem = "node" if "node" in stripped.casefold() else "python"
                    declaration = re.search(r"(?i)\b(?:python|node(?:\.js)?)\b\s*:?\s*(.+)", stripped)
                    _add_evidence(
                        evidence,
                        kind="runtime",
                        subject=f"readme-{ecosystem}",
                        file=path,
                        line=number,
                        value=stripped,
                        normalized=runtime,
                        attributes=_attrs(
                            ecosystem=ecosystem, source="readme",
                            constraint=declaration.group(1).strip().strip("`*") if declaration else None,
                        ),
                    )


def _make_test_recipe(text: str) -> tuple[int, str] | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^\s*test\s*:\s*(.*)$", line)
        if not match:
            continue
        inline = match.group(1).strip()
        if inline.startswith(";"):
            value = _normalize_command(inline[1:])
            return (index + 1, value) if _command_family(value) else None
        for recipe_number, recipe in enumerate(lines[index + 1 :], index + 2):
            if recipe.lstrip().startswith("#"):
                continue
            if recipe.startswith(("\t", "    ")) and recipe.strip():
                value = _normalize_command(recipe)
                if _command_family(value):
                    return recipe_number, value
                continue
            if recipe and not recipe.startswith((" ", "\t")):
                break
    return None


def _collect_makefile(context: RepoContext, evidence: list[Evidence]) -> None:
    selected = _text(context, ("Makefile", "makefile", "GNUmakefile"))
    if selected is None:
        return
    path, text = selected
    recipe = _make_test_recipe(text)
    if recipe is not None:
        line, value = recipe
        family = _command_family(value) or "unknown"
        _add_evidence(
            evidence,
            kind="command",
            subject="make-test",
            file=path,
            line=line,
            value=value,
            normalized=family,
            attributes=_attrs(source="makefile", family=family),
        )
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if _INSTALL_COMMANDS.search(_normalize_command(stripped)):
            _add_evidence(
                evidence,
                kind="command",
                subject="make-install",
                file=path,
                line=number,
                value=_normalize_command(stripped),
                normalized="install",
                attributes=_attrs(source="makefile", family="install"),
            )


def _collect_manifests(context: RepoContext, ecosystems: tuple[str, ...], evidence: list[Evidence]) -> None:
    package = _manifest_json(context)
    if package is not None:
        scripts = package.get("scripts")
        if isinstance(scripts, dict) and isinstance(scripts.get("test"), str):
            value = _normalize_command(str(scripts["test"]))
            family = _command_family(value)
            if family:
                _add_evidence(
                    evidence,
                    kind="command",
                    subject="package-test",
                    file="package.json",
                    line=1,
                    value=value,
                    normalized=family,
                    attributes=_attrs(source="package.json", family=family),
                )
        engines = package.get("engines")
        if isinstance(engines, dict) and isinstance(engines.get("node"), str):
            _add_evidence(
                evidence,
                kind="runtime",
                subject="package-node",
                file="package.json",
                line=1,
                value=str(engines["node"]),
                normalized=_runtime_version(str(engines["node"]), node=True) or str(engines["node"]),
                attributes=_attrs(ecosystem="node", source="package.json", constraint=str(engines["node"])),
            )
        for group in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            values = package.get(group)
            if isinstance(values, dict):
                for name, spec in sorted(values.items()):
                    _add_evidence(
                        evidence,
                        kind="dependency",
                        subject=f"node:{name}",
                        file="package.json",
                        line=1,
                        value=f"{name}: {spec}",
                        normalized=str(name).casefold(),
                        attributes=_attrs(ecosystem="node", group=group, name=name, spec=spec),
                    )

    for ecosystem in ecosystems:
        if ecosystem in {"python", "node"}:
            continue
        known, dependencies = declared_dependencies(context, ecosystem)
        if not known:
            continue
        profile = next((item for item in ECOSYSTEM_PROFILES if item.name == ecosystem), None)
        if profile is None:
            continue
        manifest = next(
            (entry.path for entry in context if any(
                entry.path == marker or fnmatch.fnmatchcase(entry.path, marker)
                for marker in profile.marker_files
            )),
            profile.marker_files[0],
        )
        for name in sorted(dependencies):
            _add_evidence(
                evidence,
                kind="dependency",
                subject=f"{ecosystem}:{name}",
                file=manifest,
                line=1,
                value=name,
                normalized=name,
                attributes=_attrs(ecosystem=ecosystem, name=name),
            )
        command = conventional_test_command(context, ecosystem)
        if command:
            family = _command_family(command)
            if family:
                _add_evidence(
                    evidence,
                    kind="command",
                    subject=f"{ecosystem}-test",
                    file=manifest,
                    line=1,
                    value=command,
                    normalized=family,
                    attributes=_attrs(source="ecosystem-profile", ecosystem=ecosystem, family=family),
                )
    document = _manifest_toml(context)
    if document is not None:
        project = document.get("project")
        if isinstance(project, dict):
            requires = project.get("requires-python")
            if isinstance(requires, str):
                _add_evidence(
                    evidence,
                    kind="runtime",
                    subject="pyproject-python",
                    file="pyproject.toml",
                    line=1,
                    value=requires,
                    normalized=_runtime_version(requires) or requires,
                    attributes=_attrs(ecosystem="python", source="pyproject.toml", constraint=requires),
                )
            dependencies = project.get("dependencies")
            if isinstance(dependencies, list):
                for dependency in dependencies:
                    if isinstance(dependency, str):
                        name = re.split(r"[ <>=!~;\[]", dependency, maxsplit=1)[0]
                        _add_evidence(
                            evidence,
                            kind="dependency",
                            subject=f"python:{name}",
                            file="pyproject.toml",
                            line=1,
                            value=dependency,
                            normalized=name.casefold(),
                            attributes=_attrs(ecosystem="python", name=name, spec=dependency),
                        )

    for entry in context:
        if not re.fullmatch(r"requirements(?:-[^/]+)?\.txt", entry.path):
            continue
        text = context.read_text(entry)
        if text is None:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("-"):
                continue
            name = re.split(r"[ <>=!~;\[]", stripped, maxsplit=1)[0]
            _add_evidence(
                evidence,
                kind="dependency",
                subject=f"python:{name}",
                file=entry.path,
                line=number,
                value=stripped,
                normalized=name.casefold(),
                attributes=_attrs(ecosystem="python", name=name, spec=stripped),
            )


def _collect_runtime(context: RepoContext, ecosystems: tuple[str, ...], evidence: list[Evidence]) -> None:
    for ecosystem, names, node in (
        ("python", _PYTHON_FILES, False),
        ("node", _NODE_FILES, True),
    ):
        if ecosystem not in ecosystems:
            continue
        for path in names:
            text = context.read_text(path)
            if text is None:
                continue
            value = _runtime_version(text, node=node)
            if value:
                _add_evidence(
                    evidence,
                    kind="runtime",
                    subject=f"pin-{ecosystem}",
                    file=path,
                    line=1,
                    value=text.strip(),
                    normalized=value,
                    attributes=_attrs(ecosystem=ecosystem, source=path, role="pin"),
                )
        tools = context.read_text(".tool-versions")
        if tools is not None:
            tool_name = "nodejs" if node else "python"
            for number, line in enumerate(tools.splitlines(), 1):
                if re.match(rf"^\s*{tool_name}\s+", line):
                    value = _runtime_version(line, node=node)
                    if value:
                        _add_evidence(
                            evidence,
                            kind="runtime",
                            subject=f"tools-{ecosystem}",
                            file=".tool-versions",
                            line=number,
                            value=line.strip(),
                            normalized=value,
                            attributes=_attrs(ecosystem=ecosystem, source=".tool-versions", role="pin"),
                        )
    for ecosystem in ecosystems:
        if ecosystem in {"python", "node"}:
            continue
        for path, value, line in runtime_values(context, ecosystem):
            _add_evidence(
                evidence,
                kind="runtime",
                subject=f"pin-{ecosystem}",
                file=path,
                line=line,
                value=value,
                normalized=_runtime_version(value, node=False) or value,
                attributes=_attrs(ecosystem=ecosystem, source=path, role="pin"),
            )


def _collect_workflows(context: RepoContext, evidence: list[Evidence]) -> None:
    for entry in context:
        if not entry.path.startswith(".github/workflows/"):
            continue
        text = context.read_text(entry)
        if text is None:
            continue
        lines = text.splitlines()
        in_run_block = False
        run_indent = 0
        steps_indent: int | None = None
        step_indent: int | None = None
        field_indent: int | None = None
        for number, line in enumerate(lines, 1):
            stripped = line.strip()
            indent = len(line) - len(line.lstrip())
            if re.fullmatch(r"steps:\s*(?:#.*)?", stripped):
                steps_indent, step_indent, field_indent = indent, None, None
            elif stripped and not stripped.startswith("#") and steps_indent is not None:
                if indent < steps_indent or (indent == steps_indent and not stripped.startswith("- ")):
                    steps_indent, step_indent, field_indent = None, None, None
                elif stripped.startswith("- ") and (step_indent is None or indent <= step_indent):
                    step_indent = indent
                    field_indent = indent + len(stripped) - len(stripped[1:].lstrip())
            setup_python = re.match(r"^\s*(?:-\s+)?python-version:\s*(.+?)\s*$", line)
            if setup_python:
                value = setup_python.group(1).split(" #", 1)[0].strip().strip("\"'")
                _add_evidence(
                    evidence,
                    kind="runtime",
                    subject="ci-python",
                    file=entry.path,
                    line=number,
                    value=value,
                    normalized=_runtime_version(value) or value,
                    attributes=_attrs(ecosystem="python", source="workflow", role="ci", constraint=value),
                )
            setup_node = re.match(r"^\s*(?:-\s+)?node-version:\s*(.+?)\s*$", line)
            if setup_node:
                value = setup_node.group(1).split(" #", 1)[0].strip().strip("\"'")
                _add_evidence(
                    evidence,
                    kind="runtime",
                    subject="ci-node",
                    file=entry.path,
                    line=number,
                    value=value,
                    normalized=_runtime_version(value, node=True) or value,
                    attributes=_attrs(ecosystem="node", source="workflow", role="ci", constraint=value),
                )
            for ecosystem, field in (
                ("go", "go-version"),
                ("rust", "rust-version"),
                ("dotnet", "dotnet-version"),
                ("java", "java-version"),
                ("ruby", "ruby-version"),
            ):
                setup = re.match(rf"^\s*(?:-\s+)?{re.escape(field)}:\s*(.+?)\s*$", line)
                if setup:
                    value = setup.group(1).split(" #", 1)[0].strip().strip("\"'")
                    _add_evidence(
                        evidence,
                        kind="runtime",
                        subject=f"ci-{ecosystem}",
                        file=entry.path,
                        line=number,
                        value=value,
                        normalized=_runtime_version(value) or value,
                        attributes=_attrs(ecosystem=ecosystem, source="workflow", role="ci", constraint=value),
                    )
            run = re.match(r"^\s*(?:-\s+)?run:\s*(.*?)\s*$", line)
            if steps_indent is None or field_indent is None or line.find("run:") != field_indent:
                run = None
            if run and re.fullmatch(r"[|>][+-]?(?:\s+#.*)?", run.group(1)):
                in_run_block = True
                run_indent = line.index("run:")
                continue
            if in_run_block and line.strip() and indent <= run_indent:
                in_run_block = False
            candidate = run.group(1).strip().strip("\"'") if run else stripped
            if in_run_block or run:
                command = _command_from_line(candidate)
                if command:
                    family, value = command
                    _add_evidence(
                        evidence,
                        kind="command",
                        subject=f"ci-{family}",
                        file=entry.path,
                        line=number,
                        value=value,
                        normalized=family,
                        attributes=_attrs(source="workflow", family=family),
                    )
                if _INSTALL_COMMANDS.search(_normalize_command(candidate)):
                    _add_evidence(
                        evidence,
                        kind="command",
                        subject="ci-install",
                        file=entry.path,
                        line=number,
                        value=_normalize_command(candidate),
                        normalized="install",
                        attributes=_attrs(source="workflow", family="install"),
                    )


def _collect_docker(context: RepoContext, evidence: list[Evidence]) -> None:
    path, text = _root_dockerfile(context)
    if path is None or text is None:
        return
    instructions = _dockerfile_instructions(text)
    final_from = max((index for index, item in enumerate(instructions) if item[0] == "FROM"), default=-1)
    for index, (instruction, line, body) in enumerate(instructions):
        if instruction == "FROM":
            image = _docker_image(body)
            if image:
                ecosystem = ""
                for profile in ECOSYSTEM_PROFILES:
                    if any(image.casefold().startswith(prefix.casefold()) for prefix in profile.container_prefixes):
                        ecosystem = profile.name
                        break
                runtime = _runtime_version(image, node=ecosystem == "node")
                _add_evidence(
                    evidence,
                    kind="container",
                    subject="docker-from",
                    file=path,
                    line=line,
                    value=image,
                    normalized=runtime or image,
                    attributes=_attrs(ecosystem=ecosystem, image=image, stage="final" if index == final_from else "build"),
                )
        if instruction in {"CMD", "ENTRYPOINT"} and index > final_from:
            if body.lstrip().startswith("["):
                try:
                    argv = json.loads(body)
                except json.JSONDecodeError:
                    continue
                if not isinstance(argv, list) or not argv or not all(isinstance(arg, str) for arg in argv):
                    continue
                body = shlex.join(argv)
            command = _command_from_line(body)
            if command:
                family, value = command
                _add_evidence(
                    evidence,
                    kind="command",
                    subject=f"docker-{family}",
                    file=path,
                    line=line,
                    value=value,
                    normalized=family,
                    attributes=_attrs(source="docker", family=family, stage="final"),
                )
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.match(r"(?i)^(RUN|COPY|ADD)\s+(.*)$", stripped)
        if not match:
            continue
        instruction, body = match.groups()
        instruction = instruction.upper()
        if instruction == "RUN":
            command = _command_from_line(body)
            if command:
                family, value = command
                _add_evidence(
                    evidence,
                    kind="command",
                    subject=f"docker-run-{family}",
                    file=path,
                    line=number,
                    value=value,
                    normalized=family,
                    attributes=_attrs(source="docker", family=family),
                )
            if _INSTALL_COMMANDS.search(_normalize_command(body)):
                _add_evidence(
                    evidence,
                    kind="command",
                    subject="docker-install",
                    file=path,
                    line=number,
                    value=_normalize_command(body),
                    normalized="install",
                    attributes=_attrs(source="docker", family="install"),
                )
        else:
            flags = re.match(r"^(?:(--\S+)\s+)*", body)
            options = flags.group(0) if flags else ""
            body = body[len(options):]
            try:
                parts = json.loads(body) if body.startswith("[") else shlex.split(body)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(parts, list) and len(parts) >= 2 and all(isinstance(part, str) for part in parts):
                for index, source in enumerate(parts[:-1]):
                    if not source.startswith(("--", "<<")):
                        _add_evidence(
                            evidence,
                            kind="container",
                            subject="docker-copy" if len(parts) == 2 else f"docker-copy:{index}",
                            file=path,
                            line=number,
                            value=source,
                            normalized=source,
                            attributes=_attrs(source="docker", instruction=instruction, external="--from=" in options),
                        )


def _collect_locks(context: RepoContext, evidence: list[Evidence]) -> None:
    names = (
        "uv.lock", "poetry.lock", "Pipfile.lock", "pdm.lock", "package-lock.json",
        "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml",
    )
    for name in dict.fromkeys((*names, *_lock_paths(context, "python"), *_lock_paths(context, "node"))):
        entry = context.entry(name)
        if entry is not None:
            _add_evidence(
                evidence,
                kind="lockfile",
                subject=f"lock:{name}",
                file=name,
                line=1,
                value=name,
                normalized=name,
                attributes=_attrs(source="filesystem", role="lockfile"),
            )
    for entry in context:
        if re.fullmatch(r"requirements(?:-[^/]+)?\.lock", entry.path) and entry.readable:
            _add_evidence(
                evidence,
                kind="lockfile",
                subject=f"lock:{entry.path}",
                file=entry.path,
                line=1,
                value=entry.path,
                normalized=entry.path,
                attributes=_attrs(source="filesystem", role="requirements-lock"),
            )
    for profile in ECOSYSTEM_PROFILES:
        for name in _lock_paths(context, profile.name):
            entry = context.entry(name)
            if entry is not None:
                _add_evidence(
                    evidence,
                    kind="lockfile",
                    subject=f"lock:{name}",
                    file=name,
                    line=1,
                    value=name,
                    normalized=name,
                    attributes=_attrs(source="filesystem", role="lockfile", ecosystem=profile.name),
                )


def _by_subject(evidence: Iterable[Evidence], prefix: str, *, kind: EvidenceKind | None = None) -> list[Evidence]:
    return sorted(
        (
            item
            for item in evidence
            if item.subject.startswith(prefix) and (kind is None or item.kind == kind)
        ),
        key=lambda item: item.id,
    )


def _edge(
    edges: list[ContractEdge],
    *,
    relation: str,
    source: Evidence,
    target: Evidence | None,
    status: ContractStatus,
    expected: str | None,
    actual: str | None,
    detail: str,
) -> ContractEdge:
    target_id = target.id if target is not None else None
    item = ContractEdge(
        id=f"{relation}:{source.id}:{target_id or expected or 'missing'}",
        relation=relation,
        source=source.id,
        target=target_id,
        status=status,
        expected=expected,
        actual=actual,
        detail=detail,
    )
    if item.id not in {existing.id for existing in edges}:
        edges.append(item)
    return item


def _diagnostic(
    diagnostics: list[ContractDiagnostic],
    *,
    code: str,
    severity: DiagnosticSeverity,
    message: str,
    evidence: Iterable[Evidence],
    related_checks: tuple[str, ...] = (),
) -> None:
    ids = tuple(sorted(item.id for item in evidence))
    item = ContractDiagnostic(code, severity, message, ids, related_checks)
    if item not in diagnostics:
        diagnostics.append(item)


def _link_commands(
    evidence: list[Evidence],
    edges: list[ContractEdge],
    diagnostics: list[ContractDiagnostic],
) -> None:
    readme = _by_subject(evidence, "readme-", kind="command")
    effective = _by_subject(evidence, "make-test", kind="command") or _by_subject(evidence, "package-test", kind="command")
    if readme and not effective and any(item.normalized == "make-test" for item in readme):
        source = next(item for item in readme if item.normalized == "make-test")
        _edge(
            edges,
            relation="implements",
            source=source,
            target=None,
            status="missing",
            expected="Makefile test target",
            actual=None,
            detail="README refers to make test but no usable Makefile test recipe was found.",
        )
        _diagnostic(
            diagnostics,
            code="CG002",
            severity="warning",
            message="README refers to make test, but no usable Makefile test target exists.",
            evidence=(source,),
            related_checks=("RR09",),
        )
    if readme and effective:
        selected = effective[0]
        for source in readme:
            status: ContractStatus = "satisfied" if _commands_match(source, selected) else "mismatch"
            edge = _edge(
                edges,
                relation="documents",
                source=source,
                target=selected,
                status=status,
                expected=selected.normalized,
                actual=source.normalized,
                detail="README test command compared with the selected repository test command.",
            )
            if edge.status == "mismatch":
                _diagnostic(
                    diagnostics,
                    code="CG001",
                    severity="warning",
                    message=(
                        f"README test command family {source.normalized!r} differs from "
                        f"the selected project test command family {selected.normalized!r}."
                    ),
                    evidence=(source, selected),
                    related_checks=("RR01", "RR09"),
                )
    make = _by_subject(evidence, "make-test", kind="command")
    package = _by_subject(evidence, "package-test", kind="command")
    if make and package:
        edge = _edge(
            edges,
            relation="implements",
            source=make[0],
            target=package[0],
            status="satisfied" if _commands_match(make[0], package[0]) else "mismatch",
            expected=package[0].normalized,
            actual=make[0].normalized,
            detail="Makefile test recipe compared with package.json scripts.test.",
        )
        if edge.status == "mismatch":
            _diagnostic(
                diagnostics,
                code="CG004",
                severity="warning",
                message="Makefile test recipe conflicts with package.json scripts.test.",
                evidence=(make[0], package[0]),
                related_checks=("RR09",),
            )
    ci = _by_subject(evidence, "ci-", kind="command")
    ci_tests = [item for item in ci if item.subject != "ci-install"]
    for source in ci_tests:
        if not effective:
            continue
        target = effective[0]
        status: ContractStatus = "satisfied" if _commands_match(source, target) else "mismatch"
        edge = _edge(
            edges,
            relation="invokes",
            source=source,
            target=target,
            status=status,
            expected=target.normalized,
            actual=source.normalized,
            detail="CI test command compared with the selected project test command.",
        )
        if edge.status == "mismatch":
            _diagnostic(
                diagnostics,
                code="CG005",
                severity="warning",
                message="CI test command conflicts with the selected project test command.",
                evidence=(source, target),
                related_checks=("RR09", "RR13"),
            )
    docker = [
        item
        for item in evidence
        if item.kind == "command"
        and item.subject.startswith("docker-")
        and item.subject != "docker-install"
    ]
    for source in docker:
        if not effective:
            continue
        target = effective[0]
        status = "satisfied" if _commands_match(source, target) else "mismatch"
        edge = _edge(
            edges,
            relation="implements",
            source=source,
            target=target,
            status=status,
            expected=target.normalized,
            actual=source.normalized,
            detail="Docker test command compared with the selected project test command.",
        )
        if edge.status == "mismatch":
            _diagnostic(
                diagnostics,
                code="CG006",
                severity="warning",
                message="Docker test command conflicts with the selected project test command.",
                evidence=(source, target),
                related_checks=("RR08", "RR09"),
            )


def _numeric_version(value: str) -> tuple[int, ...] | None:
    if not re.fullmatch(r"v?\d+(?:\.\d+){0,2}", value):
        return None
    return tuple(int(part) for part in value.lstrip("v").split("."))


def _version_prefix(parts: tuple[int, ...]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """A minor/major pin denotes its prefix; a patch pin denotes one release."""

    lower = parts + (0,) * (3 - len(parts))
    upper = (*parts[:-1], parts[-1] + 1) + (0,) * (3 - len(parts))
    return lower, upper


def _runtime_interval(value: str, *, node: bool) -> tuple[tuple[int, ...], tuple[int, ...] | None] | None:
    """Parse only numeric conjunctions and Node carets, never a partial range."""

    value = value.strip()
    if parts := _numeric_version(value):
        return _version_prefix(parts)
    if node and value.startswith("^"):
        parts = _numeric_version(value[1:])
        if parts is None:
            return None
        index = next((index for index, part in enumerate(parts) if part), len(parts) - 1)
        lower = parts + (0,) * (3 - len(parts))
        upper = (*parts[:index], parts[index] + 1) + (0,) * (2 - index)
        return lower, upper
    # Reject unions, exclusions, wildcards, prereleases and arbitrary suffixes.
    token = r"(>=|<=|==|>|<|=)\s*(\d+(?:\.\d+){0,2})"
    if not re.fullmatch(rf"{token}(?:(?:\s*,\s*|\s+){token})*", value):
        return None
    lower = (0, 0, 0)
    upper = None
    for operator, version in re.findall(token, value):
        parts = _numeric_version(version)
        assert parts is not None
        point = parts + (0,) * (3 - len(parts))
        following = (*point[:2], point[2] + 1)
        if node and len(parts) < 3:
            following = _version_prefix(parts)[1]
        if operator in {">", ">=", "=", "=="}:
            lower = max(lower, following if operator == ">" else point)
        if operator in {"<", "<=", "=", "=="}:
            bound = point if operator == "<" else following
            upper = min(upper, bound) if upper is not None else bound
    return (lower, upper) if upper is None or lower < upper else None


def _compatible_runtime(pin: Evidence, candidate: Evidence) -> bool | None:
    parts = _numeric_version(pin.normalized)
    if parts is None:
        return None
    attrs = dict(candidate.attributes)
    constraint = attrs.get("constraint")
    if constraint is not None:
        constraint = constraint.strip()
    if constraint is None or _numeric_version(constraint):
        other = _numeric_version(candidate.normalized if constraint is None else constraint)
        return parts[:min(len(parts), len(other))] == other[:min(len(parts), len(other))] if other else None
    interval = _runtime_interval(constraint, node=attrs.get("ecosystem") == "node")
    if interval is None:
        return None
    low, high = _version_prefix(parts)
    required_low, required_high = interval
    if high <= required_low or (required_high is not None and low >= required_high):
        return False
    if low >= required_low and (required_high is None or high <= required_high):
        return True
    # An unresolved patch may fall on either side of a bound.
    return None


def _commands_match(source: Evidence, target: Evidence) -> bool:
    if source.normalized == target.normalized:
        return True
    return source.normalized == "make-test" and target.subject == "make-test"

def _link_runtimes(
    evidence: list[Evidence],
    edges: list[ContractEdge],
    diagnostics: list[ContractDiagnostic],
) -> None:
    for ecosystem in tuple(profile.name for profile in ECOSYSTEM_PROFILES):
        pins = [item for item in evidence if item.kind == "runtime" and item.subject.startswith("pin-") and dict(item.attributes).get("ecosystem") == ecosystem]
        if not pins:
            continue
        pin = pins[0]
        candidates = [
            item
            for item in evidence
            if item.kind in {"runtime", "container"}
            and dict(item.attributes).get("ecosystem") == ecosystem
            and item.id != pin.id
        ]
        for candidate in candidates:
            compatible = _compatible_runtime(pin, candidate)
            status: ContractStatus = "unknown" if compatible is None else "satisfied" if compatible else "mismatch"
            edge = _edge(
                edges,
                relation="matches",
                source=pin,
                target=candidate,
                status=status,
                expected=pin.normalized,
                actual=candidate.normalized,
                detail=f"{ecosystem} runtime declarations compared.",
            )
            if edge.status == "mismatch":
                code = "CG011" if candidate.subject.startswith("ci-") else "CG012" if candidate.subject.startswith("docker-") else "CG013"
                _diagnostic(
                    diagnostics,
                    code=code,
                    severity="error",
                    message=f"{ecosystem} runtime declaration {candidate.value!r} conflicts with pinned runtime {pin.value!r}.",
                    evidence=(pin, candidate),
                    related_checks=("RR07", "RR08", "RR13"),
                )


def _link_dependencies(
    context: RepoContext,
    evidence: list[Evidence],
    edges: list[ContractEdge],
    diagnostics: list[ContractDiagnostic],
) -> None:
    locks = [item for item in evidence if item.kind == "lockfile"]
    selected = {
        ecosystem: _lock_paths(context, ecosystem)
        for ecosystem in tuple(profile.name for profile in ECOSYSTEM_PROFILES)
    }
    validation: dict[tuple[str, str], tuple[bool, str]] = {}
    for dependency in (item for item in evidence if item.kind == "dependency"):
        attrs = dict(dependency.attributes)
        ecosystem = attrs.get("ecosystem")
        candidates = [item for path in selected.get(ecosystem, ()) for item in locks if item.file == path]
        if not candidates:
            continue
        outcomes: list[tuple[ContractStatus, Evidence, str]] = []
        for lock in candidates:
            key = (ecosystem, lock.file)
            if key not in validation:
                validation[key] = _validate_lock(context, lock.file, ecosystem, set())
            valid, detail = validation[key]
            status: ContractStatus = "unknown"
            if valid:
                name = dependency.normalized if ecosystem == "python" else attrs.get("name", dependency.normalized)
                covered, detail = _validate_lock(context, lock.file, ecosystem, {name})
                status = "satisfied" if covered else "mismatch"
            outcomes.append((status, lock, detail))
        # Requirements files can split dependency groups across several files.
        status, lock, detail = min(outcomes, key=lambda item: {"satisfied": 0, "unknown": 1, "mismatch": 2}[item[0]])
        edge = _edge(
            edges,
            relation="covers",
            source=dependency,
            target=lock,
            status=status,
            expected=dependency.normalized,
            actual=lock.file,
            detail=detail or "Declared dependency name is covered by the selected structured lockfile.",
        )
        if edge.status == "mismatch":
            _diagnostic(
                diagnostics,
                code="CG009",
                severity="error",
                message=f"Declared dependency {attrs.get('name', dependency.normalized)!r} was not found in {lock.file}.",
                evidence=(dependency, lock),
                related_checks=("RR06",),
            )

    install_commands = [item for item in evidence if item.subject in {"ci-install", "docker-install", "make-install"}]
    for command in install_commands:
        try:
            tokens = shlex.split(command.value)
        except ValueError:
            continue
        references: set[str] = set()
        for index, token in enumerate(tokens):
            if token in {"-r", "--requirement", "--lockfile"} and index + 1 < len(tokens):
                references.add(tokens[index + 1])
            elif token.startswith(("--requirement=", "--lockfile=")):
                references.add(token.split("=", 1)[1])
            elif token.startswith("-r") and len(token) > 2:
                references.add(token[2:])
            elif token in {"package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "uv.lock", "poetry.lock", "Pipfile.lock", "pdm.lock"}:
                references.add(token)
        for path in sorted(references):
            path = posixpath.normpath(path)
            target = next((item for item in locks if item.file == path), None)
            uncertain = path.startswith(("/", "../")) or path == ".." or any(char in path for char in "$*?{}:")
            entry = None if uncertain else context.entry(path)
            readable = entry is not None and context.read_text(entry) is not None
            if readable and target is None:
                target = _add_evidence(
                    evidence, kind="artifact", subject="dependency-file", file=path,
                    line=1, value=path, attributes=_attrs(source="filesystem"),
                )
            status = "satisfied" if readable else "unknown" if uncertain or entry is not None or context.truncated else "missing"
            _edge(
                edges,
                relation="references",
                source=command,
                target=target,
                status=status,
                expected=path,
                actual=path if readable else None,
                detail="Install command reference compared with readable indexed repository files.",
            )
            if status == "missing":
                _diagnostic(
                    diagnostics,
                    code="CG008" if command.subject.startswith("docker") else "CG007",
                    severity="error",
                    message=f"{command.value!r} references missing lock or dependency file {path!r}.",
                    evidence=(command,),
                    related_checks=("RR06", "RR08", "RR13"),
                )


def _link_copies(context: RepoContext, evidence: list[Evidence], edges: list[ContractEdge], diagnostics: list[ContractDiagnostic]) -> None:
    for item in (entry for entry in evidence if entry.subject.startswith("docker-copy")):
        source = posixpath.normpath(item.value.strip())
        if source == ".":
            continue
        uncertain = (
            dict(item.attributes).get("external") == "True"
            or source.startswith(("/", "../")) or source == ".."
            or any(character in source for character in "$:{}")
        )
        parts = source.split("/")
        present = not uncertain and any(
            len(entry.path.split("/")) >= len(parts)
            and all(fnmatch.fnmatchcase(part, pattern) for part, pattern in zip(entry.path.split("/"), parts))
            for entry in context
        )
        status: ContractStatus = "satisfied" if present else "unknown" if uncertain or context.truncated else "missing"
        _edge(
            edges,
            relation="references",
            source=item,
            target=None,
            status=status,
            expected=source,
            actual=source if present else None,
            detail="Docker COPY/ADD source compared with the repository file index.",
        )
        if status == "missing":
            _diagnostic(
                diagnostics,
                code="CG014",
                severity="error",
                message=f"Docker {item.value!r} references a file that is not present in the repository.",
                evidence=(item,),
                related_checks=("RR08",),
            )


def build_contract_graph(
    context: RepoContext,
    ecosystems: Iterable[str] | None = None,
) -> ContractGraph:
    """Build deterministic, non-scoring cross-file diagnostics."""

    values = tuple(sorted(ecosystems if ecosystems is not None else detect_ecosystems(context)))
    evidence: list[Evidence] = []
    _collect_readme(context, evidence)
    _collect_makefile(context, evidence)
    _collect_manifests(context, values, evidence)
    _collect_runtime(context, values, evidence)
    _collect_workflows(context, evidence)
    _collect_docker(context, evidence)
    _collect_locks(context, evidence)

    edges: list[ContractEdge] = []
    diagnostics: list[ContractDiagnostic] = []
    _link_commands(evidence, edges, diagnostics)
    _link_runtimes(evidence, edges, diagnostics)
    _link_dependencies(context, evidence, edges, diagnostics)
    _link_copies(context, evidence, edges, diagnostics)

    return ContractGraph(
        version=GRAPH_VERSION,
        evidence=tuple(sorted(evidence, key=lambda item: item.id)),
        edges=tuple(sorted(edges, key=lambda item: item.id)),
        diagnostics=tuple(sorted(diagnostics, key=lambda item: (item.code, item.message, item.evidence))),
    )


def diagnose(root: str | Path, *, context: RepoContext | None = None) -> ContractGraph:
    """Build a contract graph from a repository path or an existing context."""

    return build_contract_graph(context or RepoContext(root))


__all__ = [
    "ContractDiagnostic",
    "ContractEdge",
    "ContractGraph",
    "Evidence",
    "GRAPH_VERSION",
    "build_contract_graph",
    "diagnose",
]
