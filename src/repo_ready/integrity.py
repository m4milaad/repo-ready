"""Static repository-integrity observations kept separate from readiness score."""

from __future__ import annotations

import fnmatch
import re
from collections import defaultdict

from .context import RepoContext
from .tracked import TrackedReport


_LOCK_NAMES = {
    "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml",
    "bun.lock", "bun.lockb", "uv.lock", "poetry.lock", "Pipfile.lock",
    "pdm.lock", "Cargo.lock", "Gemfile.lock", "go.sum", "packages.lock.json",
}
_GENERATED_SUFFIXES = (".tsbuildinfo", ".pyc", ".pyo")
_SECRET_NAME = re.compile(r"(?i)(?:api[_-]?key|secret|password|private[_-]?key|access[_-]?token)")


def _ignored(path: str, patterns: set[str]) -> bool:
    for pattern in patterns:
        value = pattern.strip().lstrip("/")
        if not value or value.startswith("!"):
            continue
        if fnmatch.fnmatchcase(path, value) or fnmatch.fnmatchcase(path, f"*/{value}"):
            return True
        if "/" not in value and value in path.split("/"):
            return True
    return False


def inspect_integrity(context: RepoContext, tracked: TrackedReport | None = None) -> dict[str, object]:
    """Return bounded integrity findings without changing the readiness score."""

    findings: list[dict[str, object]] = []
    tracked_paths = {item.path for item in tracked.files} if tracked is not None else set()
    ignore_text = context.read_text(".gitignore") or ""
    patterns = {line.strip() for line in ignore_text.splitlines() if line.strip() and not line.lstrip().startswith("#")}

    generated = sorted(path for path in tracked_paths if path.casefold().endswith(_GENERATED_SUFFIXES))
    for path in generated[:20]:
        findings.append({"code": "RI001", "severity": "warning", "file": path, "detail": "Generated build/cache artifact is tracked."})

    ignored_tracked = sorted(path for path in tracked_paths if _ignored(path, patterns))
    for path in ignored_tracked[:20]:
        findings.append({"code": "RI002", "severity": "warning", "file": path, "detail": "Tracked file matches a .gitignore pattern."})

    lock_groups: dict[str, list[str]] = defaultdict(list)
    for path in context:
        name = path.name
        if name in _LOCK_NAMES:
            lock_groups[name].append(path.path)
    for name, paths in sorted(lock_groups.items()):
        if len(paths) > 1:
            findings.append({"code": "RI003", "severity": "info", "file": paths[0], "detail": f"Multiple {name} files exist: {', '.join(paths)}."})

    for entry in context:
        if entry.name in {".env", ".env.local"}:
            text = context.read_indexed_text(entry) or ""
            if _SECRET_NAME.search(entry.name) or re.search(r"(?i)(?:key|secret|password|token)\s*=\s*[^\s#]+", text):
                findings.append({"code": "RI004", "severity": "warning", "file": entry.path, "detail": "Potential secret-bearing environment file is present in the scan."})
        if entry.size > 10 * 1024 * 1024 and entry.path in tracked_paths:
            findings.append({"code": "RI005", "severity": "warning", "file": entry.path, "detail": "Tracked file is larger than 10 MiB."})

    return {
        "findings": sorted(findings, key=lambda item: (str(item["code"]), str(item.get("file", "")), str(item["detail"]))),
        "counts": {code: sum(1 for item in findings if item["code"] == code) for code in sorted({str(item["code"]) for item in findings})},
    }


__all__ = ["inspect_integrity"]
