from __future__ import annotations

import ast
import os
import shutil
import time
from pathlib import Path

from repo_ready.context import RepoContext
from repo_ready.scoring import report


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "repo_ready"
SCAN_FILE_COUNT = 5_000
# Windows filesystem metadata and open costs are substantially higher on the
# supported native development platform; Linux keeps the original release
# budget used by the clean Docker verification path.
SCAN_BUDGET_SECONDS = 6.0 if os.name == "nt" else 3.0


def test_scan_of_5000_small_text_files_stays_within_budget(tmp_path: Path) -> None:
    repository = tmp_path / "large-repository"
    repository.mkdir()
    for index in range(SCAN_FILE_COUNT):
        (repository / f"file-{index:04d}.txt").write_text("small fixture text\n", encoding="utf-8")

    started = time.perf_counter()
    context = RepoContext(repository)
    result = report(repository, context=context)
    elapsed = time.perf_counter() - started

    assert len(context.files) == SCAN_FILE_COUNT
    assert result["max"] == 100
    assert len(result["checks"]) == 13
    assert elapsed < SCAN_BUDGET_SECONDS, (
        f"scanning {SCAN_FILE_COUNT} small text files took {elapsed:.3f}s; "
        f"budget is {SCAN_BUDGET_SECONDS:.1f}s"
    )


def test_small_file_reads_observe_changes_after_indexing(tmp_path: Path) -> None:
    path = tmp_path / "value.txt"
    path.write_bytes(b"before\n")
    context = RepoContext(tmp_path)

    assert context.read_text("value.txt") == "before\n"
    path.write_bytes(b"after\n")

    assert context.read_text("value.txt") == "after\n"


def _create_files(repository: Path, files: tuple[tuple[str, str], ...]) -> None:
    for relative, content in files:
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def test_report_is_equal_when_the_same_files_are_created_in_different_orders(tmp_path: Path) -> None:
    repository = tmp_path / "order-independent-repository"
    files = tuple(
        (f"group-{index % 5}/file-{index:03d}.{'py' if index % 7 == 0 else 'txt'}", f"value = {index}\n")
        for index in range(125)
    ) + (
        ("README.md", "# fixture\n\n## Setup\n\n## Run\n\n## Test\n"),
        ("src/zeta.py", 'root = "private-zeta"\n'),
        ("src/alpha.py", 'root = "private-alpha"\n'),
    )

    repository.mkdir()
    _create_files(repository, files)
    first_report = report(repository)

    shutil.rmtree(repository)
    repository.mkdir()
    _create_files(repository, tuple(reversed(files)))
    second_report = report(repository)

    assert first_report == second_report


FORBIDDEN_IMPORT_ROOTS = {
    "calendar",
    "datetime",
    "multiprocessing",
    "random",
    "secrets",
    "socket",
    "subprocess",
    "time",
    "uuid",
}
FORBIDDEN_CALL_MODULE_ROOTS = FORBIDDEN_IMPORT_ROOTS
FORBIDDEN_CLOCK_CALLS = {
    "clock",
    "monotonic",
    "monotonic_ns",
    "perf_counter",
    "perf_counter_ns",
    "process_time",
    "process_time_ns",
    "sleep",
    "time",
    "time_ns",
}
FORBIDDEN_PROCESS_CALL_PREFIXES = (
    "asyncio.create_subprocess",
    "anyio.run_process",
    "os.exec",
    "os.fork",
    "os.popen",
    "os.posix_spawn",
    "os.spawn",
    "os.startfile",
    "os.system",
    "trio.run_process",
)


def _qualified_name(node: ast.AST, bindings: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return bindings.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value, bindings)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _source_violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    bindings: dict[str, str] = {}
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported = alias.name
                local = alias.asname or imported.partition(".")[0]
                bindings[local] = imported
                if imported.partition(".")[0] in FORBIDDEN_IMPORT_ROOTS:
                    violations.append(f"{path}:{node.lineno}: import {imported}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.partition(".")[0] in FORBIDDEN_IMPORT_ROOTS:
                violations.append(f"{path}:{node.lineno}: from {module} import ...")
            for alias in node.names:
                imported = f"{module}.{alias.name}" if module else alias.name
                bindings[alias.asname or alias.name] = imported

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        called = _qualified_name(node.func, bindings)
        if called is None:
            continue
        root = called.partition(".")[0]
        leaf = called.rpartition(".")[2]
        forbidden = root in FORBIDDEN_CALL_MODULE_ROOTS
        forbidden = forbidden or leaf in FORBIDDEN_CLOCK_CALLS
        forbidden = forbidden or any(called.startswith(prefix) for prefix in FORBIDDEN_PROCESS_CALL_PREFIXES)
        forbidden = forbidden or called == "os.urandom"
        if called in {"__import__", "importlib.import_module"} and node.args:
            first_argument = node.args[0]
            forbidden = forbidden or (
                isinstance(first_argument, ast.Constant)
                and isinstance(first_argument.value, str)
                and first_argument.value.partition(".")[0] in FORBIDDEN_IMPORT_ROOTS
            )
        if forbidden:
            violations.append(f"{path}:{node.lineno}: call {called}")

    return violations


def test_repo_ready_source_has_no_nondeterministic_or_execution_boundaries() -> None:
    violations = [violation for path in sorted(SOURCE_ROOT.rglob("*.py")) for violation in _source_violations(path)]

    assert not violations, "prohibited source boundaries found:\n" + "\n".join(violations)
