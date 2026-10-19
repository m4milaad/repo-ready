"""Build a deterministic, static Repo X-Ray showcase payload.

The showcase is a data API.  It intentionally does not render HTML, execute a
repository, invoke Git or another subprocess, access the network, or create a
projection on disk.  A live :class:`RepoContext` is captured once and every
report, graph, repair preview, and scenario is evaluated from that immutable
snapshot or an in-memory :class:`OverlayContext` derived from it.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from pathlib import Path
from typing import Mapping

from . import __version__
from .baseline import compare_reports
from .context import RepoContext
from .contracts import ContractGraph, build_contract_graph
from .fixer import PlannedFile, plan_fixes
from .overlay import OverlayContext, SnapshotContext, snapshot_context
from .scoring import report
from .tracked import TrackedReport, inspect_tracked


SHOWCASE_SCHEMA_VERSION = 1
_RUNTIME_PIN_FILES = (".python-version", "runtime.txt", ".nvmrc", ".node-version")
_WORKFLOW_RUNTIME = {
    "python": ("python-version", "python"),
    "node": ("node-version", "node"),
}
_STATIC_LIMITATIONS = (
    "Static inspection only; target code is never executed.",
    "No subprocesses, network access, package managers, or target-file writes are used.",
    "Scenario results are in-memory what-if projections, not build or test results.",
)


def _canonical_json(value: object) -> str:
    """Serialize JSON data with the version-1 canonical encoding."""

    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def analysis_fingerprint(analysis: Mapping[str, object]) -> str:
    """Return the SHA-256 fingerprint of canonical analysis data only."""

    return hashlib.sha256(_canonical_json(dict(analysis)).encode("utf-8")).hexdigest()


def _relative_path(operation: PlannedFile, root: Path) -> str:
    try:
        return operation.path.relative_to(root).as_posix()
    except (OSError, RuntimeError, ValueError):
        return operation.path.as_posix()


def _patch(operations: list[PlannedFile], root: Path) -> str:
    """Build a create-only preview patch without opening any source file."""

    chunks: list[str] = []
    for operation in operations:
        if operation.action != "create":
            continue
        lines = operation.content.splitlines(keepends=True)
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += "\n"
        chunks.append(
            "".join(
                difflib.unified_diff(
                    (),
                    lines,
                    fromfile="/dev/null",
                    tofile=_relative_path(operation, root),
                    lineterm="\n",
                )
            )
        )
    return "".join(chunks)


def _manual_category(operation: PlannedFile) -> str:
    """Return a stable presentation category for a manual repair blocker."""

    haystack = f"{operation.path.as_posix()} {operation.reason}".casefold()
    if "license" in haystack or "copyright" in haystack:
        return "license"
    if any(value in haystack for value in ("runtime", "python", "node", "version", "pin")):
        return "runtime"
    if "docker" in haystack or "container" in haystack:
        return "container"
    if "lockfile" in haystack or "lock file" in haystack:
        return "lockfile"
    if "test" in haystack or "verification" in haystack:
        return "test"
    if "machine" in haystack or "absolute path" in haystack:
        return "machine-specific"
    if "score" in haystack or "regress" in haystack:
        return "score-protection"
    return "other"


def _repair_preview(context: RepoContext, baseline_score: int) -> dict[str, object]:
    """Preview the existing fixer plan against *context* in memory."""

    root = context.root
    plans = plan_fixes(root, context=context)
    operations = [operation.as_dict(root) for operation in plans]
    manual = [
        {
            "file": _relative_path(operation, root),
            "message": operation.reason,
            "related_files": [],
            "category": _manual_category(operation),
            "action": operation.action,
        }
        for operation in plans
        if operation.action in {"manual", "skip-unsafe"}
    ]
    existing = [item for item in operations if item["action"] == "skip-existing"]
    changes: dict[str, str] = {}
    for operation in plans:
        if operation.action != "create":
            continue
        relative = _relative_path(operation, root)
        # _context_plan only emits safe relative paths.  Keeping the guard here
        # makes this boundary robust if a future fixer operation is introduced.
        if relative.startswith("/") or relative == ".." or relative.startswith("../"):
            continue
        changes[relative] = operation.content

    projected = OverlayContext(context, changes)
    score_after = int(report(".", context=projected)["score"])
    limitations = [
        "Only fixer operations with action=create are scored in the in-memory overlay.",
        "A proposed repair is not a verified build or test run.",
    ]
    limitations.extend(f"Snapshot limitation: {item}" for item in getattr(context, "limitations", ()))
    return {
        "score_before": int(baseline_score),
        "score_after": score_after,
        "score_delta": score_after - int(baseline_score),
        "operations": operations,
        "existing": existing,
        "patch": _patch(plans, root),
        "manual": manual,
        "limitations": sorted(set(limitations)),
    }


def _stable_report(context: RepoContext) -> dict[str, object]:
    """Return a report whose path does not leak the caller's absolute root."""

    result = dict(report(".", context=context))
    result["path"] = "."
    return result


def _runtime_version(value: str, *, node: bool = False) -> str | None:
    match = re.search(r"(?<!\d)v?(\d+)(?:\.(\d+))?(?:\.(\d+))?(?!\d)", value.strip())
    if match is None:
        return None
    values = [item for item in match.groups() if item is not None]
    if not node and len(values) < 2:
        return None
    return ".".join(values)


def _runtime_pin(context: RepoContext) -> tuple[str, str, str] | None:
    for path in _RUNTIME_PIN_FILES:
        text = context.read_text(path)
        if text is None:
            continue
        node = path in {".nvmrc", ".node-version"}
        version = _runtime_version(text, node=node)
        if version:
            return path, text, "node" if node else "python"

    tools = context.read_text(".tool-versions")
    if tools is not None:
        for line in tools.splitlines(keepends=True):
            match = re.match(r"\s*(python|nodejs)\s+(.+?)\s*(?:#.*)?(?:\r?\n)?$", line)
            if match:
                ecosystem = "node" if match.group(1) == "nodejs" else "python"
                if _runtime_version(match.group(2), node=ecosystem == "node"):
                    return ".tool-versions", tools, ecosystem
    return None


def _runtime_pin_change(context: RepoContext) -> tuple[list[dict[str, str]], dict[str, str | None], str] | None:
    selected = _runtime_pin(context)
    if selected is None:
        return None
    path, text, ecosystem = selected
    if path != ".tool-versions":
        return (
            [{"path": path, "operation": "remove", "detail": "Remove the observed runtime pin."}],
            {path: None},
            f"Remove the {ecosystem} runtime pin from {path}.",
        )

    lines = text.splitlines(keepends=True)
    changed = [
        line
        for line in lines
        if not re.match(rf"\s*{'nodejs' if ecosystem == 'node' else 'python'}\s+", line)
    ]
    replacement = "".join(changed)
    changes: dict[str, str | None] = {path: replacement if replacement else None}
    return (
        [{"path": path, "operation": "replace", "detail": "Remove the observed runtime pin entry."}],
        changes,
        f"Remove the {ecosystem} runtime pin entry from .tool-versions.",
    )


def _drift_version(value: str, *, node: bool) -> str:
    current = _runtime_version(value, node=node)
    if current is None:
        return "3.11" if not node else "18"
    parts = [int(part) for part in current.split(".")]
    if node:
        major = parts[0]
        return str(18 if major != 18 else 20)
    major, minor = (parts + [0, 0])[:2]
    if (major, minor) == (3, 11):
        return "3.12"
    return "3.11"


def _workflow_drift_change(context: RepoContext) -> tuple[list[dict[str, str]], dict[str, str], str] | None:
    selected = _runtime_pin(context)
    if selected is None:
        return None
    _pin_path, pin_text, ecosystem = selected
    runtime_value = _runtime_version(pin_text, node=ecosystem == "node")
    if selected[0] == ".tool-versions":
        tool_name = "nodejs" if ecosystem == "node" else "python"
        runtime_value = next(
            (
                _runtime_version(line, node=ecosystem == "node")
                for line in pin_text.splitlines()
                if re.match(rf"\s*{tool_name}\s+", line)
                and _runtime_version(line, node=ecosystem == "node")
            ),
            runtime_value,
        )

    field, _action_name = _WORKFLOW_RUNTIME[ecosystem]
    workflow_entries = [
        entry
        for entry in context
        if entry.path.startswith(".github/workflows/") and entry.readable
    ]
    workflow_entries.sort(key=lambda entry: entry.path)
    field_pattern = re.compile(rf"(?m)^(\s*(?:-\s+)?{re.escape(field)}\s*:\s*)([^#\r\n]+)")
    for entry in workflow_entries:
        text = context.read_text(entry)
        if text is None:
            continue
        match = field_pattern.search(text)
        if match is None:
            continue
        old = match.group(2).strip().strip("\"'")
        drift = _drift_version(runtime_value or old, node=ecosystem == "node")
        replacement = match.group(1) + match.group(2).replace(old, drift, 1)
        virtual = text[: match.start()] + replacement + text[match.end() :]
        return (
            [{"path": entry.path, "operation": "replace", "detail": f"Change {field} to {drift}."}],
            {entry.path: virtual},
            f"Introduce a {ecosystem} CI/runtime mismatch in {entry.path}.",
        )

    path = ".github/workflows/repo-ready-runtime-drift.yml"
    if ecosystem == "python":
        drift = _drift_version(runtime_value or "3.12", node=False)
        content = (
            "name: Repo Ready runtime drift\n\n"
            "on: [push, pull_request]\n\n"
            "jobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - uses: actions/setup-python@v5\n"
            "        with:\n"
            f"          python-version: '{drift}'\n"
            "      - run: python -m pytest -q\n"
        )
    else:
        drift = _drift_version(runtime_value or "20", node=True)
        content = (
            "name: Repo Ready runtime drift\n\n"
            "on: [push, pull_request]\n\n"
            "jobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - uses: actions/setup-node@v4\n"
            "        with:\n"
            f"          node-version: '{drift}'\n"
            "      - run: npm test\n"
        )
    return (
        [{"path": path, "operation": "add", "detail": f"Add a CI runtime declaration at {drift}."}],
        {path: content},
        f"Introduce a {ecosystem} CI/runtime mismatch with a virtual workflow.",
    )


def _dependency_file_change(
    context: RepoContext,
    graph: ContractGraph,
) -> tuple[list[dict[str, str]], dict[str, None], str] | None:
    candidates: list[str] = []
    for edge in graph.edges:
        if edge.relation != "references" or edge.status != "satisfied" or not edge.expected:
            continue
        path = edge.expected.replace("\\", "/")
        if context.entry(path) is not None and context.read_text(path) is not None:
            candidates.append(path)
    if not candidates:
        return None
    path = sorted(set(candidates))[0]
    return (
        [{"path": path, "operation": "remove", "detail": "Remove a file explicitly referenced by an install command."}],
        {path: None},
        f"Remove explicitly referenced dependency file {path}.",
    )


def _remove_make_test(text: str) -> str:
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if not re.match(r"^\s*test\s*:", line):
            continue
        end = index + 1
        while end < len(lines):
            candidate = lines[end]
            if candidate.startswith((" ", "\t")) or not candidate.strip() or candidate.lstrip().startswith("#"):
                end += 1
                continue
            break
        return "".join(lines[:index] + lines[end:])
    return text


def _test_entry_change(context: RepoContext) -> tuple[list[dict[str, str]], dict[str, str | None], str] | None:
    for path in ("Makefile", "makefile", "GNUmakefile"):
        text = context.read_text(path)
        if text is None or not re.search(r"(?m)^\s*test\s*:", text):
            continue
        replacement = _remove_make_test(text)
        return (
            [{"path": path, "operation": "replace", "detail": "Remove the Make test target."}],
            {path: replacement},
            f"Remove the test entry point from {path}.",
        )

    text = context.read_text("package.json")
    if text is not None:
        try:
            package = json.loads(text)
        except json.JSONDecodeError:
            package = None
        scripts = package.get("scripts") if isinstance(package, dict) else None
        if isinstance(scripts, dict) and "test" in scripts:
            scripts = dict(scripts)
            scripts.pop("test", None)
            package = dict(package)
            package["scripts"] = scripts
            replacement = json.dumps(package, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
            return (
                [{"path": "package.json", "operation": "replace", "detail": "Remove scripts.test."}],
                {"package.json": replacement},
                "Remove the package test entry point from package.json.",
            )

    for path in ("pytest.ini", "tox.ini", "noxfile.py"):
        if context.read_text(path) is not None:
            return (
                [{"path": path, "operation": "remove", "detail": "Remove the discovered test configuration entry point."}],
                {path: None},
                f"Remove the test entry point from {path}.",
            )
    return None


def _diagnostic_delta(before: ContractGraph, after: ContractGraph) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    old = {(_canonical_json(item.as_dict())): item.as_dict() for item in before.diagnostics}
    new = {(_canonical_json(item.as_dict())): item.as_dict() for item in after.diagnostics}
    added = [new[key] for key in sorted(new.keys() - old.keys())]
    resolved = [old[key] for key in sorted(old.keys() - new.keys())]
    return added, resolved


def _unavailable(
    identifier: str,
    label: str,
    description: str,
    reason: str,
) -> dict[str, object]:
    return {
        "id": identifier,
        "label": label,
        "description": description,
        "status": "unavailable",
        "reason": reason,
        "changes": [],
        "report": None,
        "graph": None,
        "score_delta": None,
        "comparison": None,
        "new_diagnostics": [],
        "resolved_diagnostics": [],
        "affected_files": [],
        "repair": None,
    }


def _available(
    identifier: str,
    label: str,
    description: str,
    changes: list[dict[str, str]],
    virtual_changes: Mapping[str, str | bytes | None],
    baseline_report: dict[str, object],
    baseline_graph: ContractGraph,
    context: SnapshotContext,
) -> dict[str, object]:
    overlay = OverlayContext(context, virtual_changes)
    scenario_report = _stable_report(overlay)
    scenario_graph = build_contract_graph(overlay)
    comparison = compare_reports(baseline_report, scenario_report)
    added, resolved = _diagnostic_delta(baseline_graph, scenario_graph)
    affected = sorted({item["path"] for item in changes})
    return {
        "id": identifier,
        "label": label,
        "description": description,
        "status": "available",
        "reason": None,
        "changes": changes,
        "report": scenario_report,
        "graph": scenario_graph.as_dict(),
        "score_delta": int(scenario_report["score"]) - int(baseline_report["score"]),
        "comparison": comparison.as_dict(),
        "new_diagnostics": added,
        "resolved_diagnostics": resolved,
        "affected_files": affected,
        "repair": _repair_preview(overlay, int(scenario_report["score"])),
    }


def _scenario(
    identifier: str,
    label: str,
    description: str,
    proposal: tuple[list[dict[str, str]], dict[str, str | bytes | None], str] | None,
    baseline_report: dict[str, object],
    baseline_graph: ContractGraph,
    context: SnapshotContext,
    unavailable_reason: str,
) -> dict[str, object]:
    if proposal is None:
        return _unavailable(identifier, label, description, unavailable_reason)
    changes, virtual, _detail = proposal
    return _available(identifier, label, description, changes, virtual, baseline_report, baseline_graph, context)


def _scenario_list(
    context: SnapshotContext,
    baseline_report: dict[str, object],
    baseline_graph: ContractGraph,
) -> list[dict[str, object]]:
    runtime = _runtime_pin_change(context)
    dependency = _dependency_file_change(context, baseline_graph)
    test_entry = _test_entry_change(context)
    return [
        _scenario(
            "remove-runtime-pin",
            "Remove runtime pin",
            "Remove the observed runtime pin and recompute the static readiness contract.",
            runtime,
            baseline_report,
            baseline_graph,
            context,
            "No explicit runtime pin was observed.",
        ),
        _scenario(
            "ci-runtime-drift",
            "Introduce CI/runtime drift",
            "Introduce a CI runtime declaration that conflicts with the repository pin.",
            _workflow_drift_change(context),
            baseline_report,
            baseline_graph,
            context,
            "No explicit runtime pin was observed for a CI/runtime comparison.",
        ),
        _scenario(
            "remove-referenced-dependency-file",
            "Remove referenced dependency file",
            "Remove a dependency or lock file explicitly referenced by an install command.",
            dependency,
            baseline_report,
            baseline_graph,
            context,
            "No readable dependency file explicitly referenced by an install command was observed.",
        ),
        _scenario(
            "remove-test-entry-point",
            "Remove test entry point",
            "Remove the statically discovered project test entry point.",
            test_entry,
            baseline_report,
            baseline_graph,
            context,
            "No statically discoverable project test entry point was observed.",
        ),
    ]


def _scope_context(root: str | Path, scope: str) -> tuple[RepoContext, TrackedReport | None]:
    if scope not in {"filesystem", "tracked"}:
        raise ValueError("scope must be 'filesystem' or 'tracked'")
    tracked: TrackedReport | None = None
    include_paths = None
    if scope == "tracked":
        tracked = inspect_tracked(root)
        include_paths = (item.path for item in tracked.files)
    return RepoContext(root, include_paths=include_paths), tracked


def build_showcase(
    root: str | Path,
    scope: str = "filesystem",
    scenarios: bool = True,
) -> dict[str, object]:
    """Build the version-1 JSON-serializable static showcase payload.

    ``root`` is inspected once.  The returned payload contains a sanitized
    report path (``.``) and repository-relative operation/evidence paths, so it
    can be serialized or fingerprinted without leaking a machine-specific
    checkout path.
    """

    if not isinstance(scenarios, bool):
        raise TypeError("scenarios must be a bool")
    live, tracked = _scope_context(root, scope)
    frozen = snapshot_context(live)
    baseline_report = _stable_report(frozen)
    baseline_graph = build_contract_graph(frozen)
    baseline_score = int(baseline_report["score"])
    repair = _repair_preview(frozen, baseline_score)

    limitations = list(_STATIC_LIMITATIONS)
    limitations.extend(f"Snapshot limitation: {item}" for item in frozen.limitations)
    complete = not frozen.limitations and not frozen.truncated
    if tracked is not None:
        if tracked.index_version is None:
            complete = False
            limitations.append("Tracked scope is unavailable because the Git index could not be read.")
        if tracked.findings:
            complete = False
            for finding in tracked.findings:
                limitations.append(f"Tracked scope finding {finding['code']}: {finding['detail']}")

    analysis: dict[str, object] = {
        "mode": "static",
        "repository": {
            "name": frozen.root.name or "repository",
            "scope": scope,
            "files": len(frozen.files),
            "complete": complete,
        },
        "limitations": sorted(set(limitations)),
        "baseline": {
            "report": baseline_report,
            "graph": baseline_graph.as_dict(),
            "repair": repair,
        },
        "scenarios": _scenario_list(frozen, baseline_report, baseline_graph) if scenarios else [],
    }
    return {
        "schema_version": SHOWCASE_SCHEMA_VERSION,
        "tool": "repo-ready",
        "tool_version": __version__,
        "analysis": analysis,
        "receipt": {
            "schema_version": SHOWCASE_SCHEMA_VERSION,
            "algorithm": "sha256",
            "fingerprint": analysis_fingerprint(analysis),
            "statement": "Fingerprint of the supplied canonical static analysis.",
        },
    }


__all__ = ["SHOWCASE_SCHEMA_VERSION", "analysis_fingerprint", "build_showcase"]
