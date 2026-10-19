"""Command-line interface for deterministic offline repository checks."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .artifact import inspect_artifact
from .baseline import BaselineError, compare_repository
from .contracts import diagnose
from .context import ConfigError, RepoContext
from .fixer import PlannedFile, apply_fix_plan, plan_fixes, project_fix_score, protect_score
from .formats import render_markdown, render_sarif
from .tracked import inspect_tracked
from .scoring import report


_ANSI_RESET = "\033[0m"
_STATUS_COLORS = {
    "PASS": "\033[32m",
    "PARTIAL": "\033[33m",
    "FAIL": "\033[31m",
    "NA": "\033[90m",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="repo-ready")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check", help="score a local repository")
    check.add_argument("path", nargs="?", type=Path, default=None)
    check.add_argument("--json", action="store_true", dest="as_json")
    check.add_argument("--format", choices=("text", "json", "markdown", "sarif"), default=None)
    check.add_argument("--min-score", type=int, default=None)
    check.add_argument("--no-color", action="store_true")

    diagnose_command = commands.add_parser("diagnose", help="inspect cross-file repository contracts")
    diagnose_command.add_argument("path", nargs="?", type=Path, default=None)
    diagnose_command.add_argument("--json", action="store_true", dest="as_json")

    compare = commands.add_parser("compare", help="compare a repository report with a baseline")
    compare.add_argument("path", nargs="?", type=Path, default=None)
    compare.add_argument("--baseline", required=True, type=Path)
    compare.add_argument("--json", action="store_true", dest="as_json")

    artifact = commands.add_parser("artifact", help="inspect a wheel or source distribution")
    artifact.add_argument("path", type=Path)
    artifact.add_argument("--project", type=Path, default=None)
    artifact.add_argument("--json", action="store_true", dest="as_json")

    tracked = commands.add_parser("tracked", help="list paths recorded in the Git index")
    tracked.add_argument("path", nargs="?", type=Path, default=None)
    tracked.add_argument("--json", action="store_true", dest="as_json")

    fix = commands.add_parser("fix", help="scaffold missing repository files")
    fix.add_argument("path", nargs="?", type=Path, default=None)
    fix.add_argument("--dry-run", action="store_true")
    fix.add_argument("--license", choices=("mit",), dest="license_name")
    fix.add_argument("--holder", default=None)
    fix.add_argument("--runtime-version", default=None)
    fix.add_argument("--json", action="store_true", dest="as_json")
    return parser


def _resolve_repository(path: Path) -> Path | None:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return resolved if resolved.is_dir() else None


def _status_label(status: str, *, no_color: bool) -> str:
    """Format a fixed-width status label for human-readable terminal output."""

    padding = " " * max(0, 7 - len(status))
    if no_color or "NO_COLOR" in os.environ:
        return f"{status}{padding}"
    isatty = getattr(sys.stdout, "isatty", None)
    if not callable(isatty) or not isatty():
        return f"{status}{padding}"
    color = _STATUS_COLORS.get(status)
    if color is None:
        return f"{status}{padding}"
    return f"{color}{status}{_ANSI_RESET}{padding}"


def _print_human_report(result: dict[str, object], *, no_color: bool = False) -> None:
    print(f"Path: {result['path']}")
    ecosystems = result["ecosystems"]
    print(f"Ecosystems: {', '.join(ecosystems) if ecosystems else 'none (no supported project manifest found)'}")
    print(f"Score: {result['score']}/{result['max']} — {result['grade']}")
    checks = result["checks"]
    if not isinstance(checks, list):
        return
    for check in checks:
        if not isinstance(check, dict):
            continue
        status = str(check["status"]).upper()
        print(f"{_status_label(status, no_color=no_color)} {check['id']} {check['title']}: {check['message']} ({check['earned']}/{check['weight']})")
        for finding in check.get("findings", []):
            if isinstance(finding, dict):
                line = f":{finding['line']}" if finding.get("line") else ""
                print(f"         - {finding['file']}{line}: {finding['detail']}")
        if check.get("status") not in {"pass", "na"} and check.get("fix_hint"):
            print(f"         Fix: {check['fix_hint']}")
    fixes = sorted(
        (check for check in checks if isinstance(check, dict) and check.get("earned", 0) < check.get("weight", 0)),
        key=lambda check: (-int(check["weight"]) + int(check["earned"]), str(check["id"])),
    )
    if fixes:
        print("Top fixes:")
        for check in fixes[:5]:
            print(f"  {check['id']} ({int(check['weight']) - int(check['earned'])} points): {check.get('fix_hint') or check['message']}")


def _run_check(args: argparse.Namespace, repository: Path) -> int:
    result = report(args.path)
    output_format = "json" if args.as_json else (args.format or "text")
    if output_format == "json":
        print(json.dumps(result, indent=2))
    elif output_format == "markdown":
        print(render_markdown(result))
    elif output_format == "sarif":
        print(render_sarif(result))
    else:
        _print_human_report(result, no_color=args.no_color)
    context = RepoContext(repository)
    threshold = args.min_score if args.min_score is not None else context.min_score
    if threshold is not None and int(result["score"]) < threshold:
        return 1
    return 0


def _run_diagnose(args: argparse.Namespace, repository: Path) -> int:
    graph = diagnose(repository)
    if args.as_json:
        print(json.dumps(graph.as_dict(), indent=2))
    else:
        print(f"Path: {repository}")
        print(f"Contract graph: v{graph.version}")
        print(f"Evidence: {len(graph.evidence)}")
        print(f"Edges: {len(graph.edges)}")
        print(f"Diagnostics: {len(graph.diagnostics)}")
        for diagnostic in graph.diagnostics:
            print(f"{diagnostic.severity.upper()} {diagnostic.code}: {diagnostic.message}")
    return 1 if any(item.severity == "error" for item in graph.diagnostics) else 0


def _run_compare(args: argparse.Namespace, repository: Path) -> int:
    comparison = compare_repository(repository, args.baseline)
    if args.as_json:
        print(json.dumps(comparison.as_dict(), indent=2))
    else:
        print(f"Path: {repository}")
        print(f"Baseline score: {comparison.baseline_score}/{comparison.max_score}")
        print(f"Current score: {comparison.current_score}/{comparison.max_score}")
        print(f"Score delta: {comparison.score_delta:+d}")
        if comparison.changes:
            print("Changes:")
            for change in comparison.changes:
                print(f"  {change.direction.upper()} {change.kind} {change.identifier}: {change.detail}")
        else:
            print("Changes: none")
    return 1 if comparison.regressed else 0


def _run_artifact(args: argparse.Namespace) -> int:
    result = inspect_artifact(args.path, project=args.project)
    if args.as_json:
        print(json.dumps(result.as_dict(), indent=2))
    else:
        print(f"Artifact: {result.artifact}")
        print(f"Format: {result.format}")
        print(f"Safe: {'yes' if result.safe else 'no'}")
        print(f"Members: {len(result.members)}")
        for finding in result.findings:
            member = f" ({finding.member})" if finding.member else ""
            print(f"{finding.severity.upper()} {finding.code}{member}: {finding.detail}")
    return 0 if result.safe else 1


def _run_tracked(args: argparse.Namespace, repository: Path) -> int:
    result = inspect_tracked(repository)
    if args.as_json:
        print(json.dumps(result.as_dict(), indent=2))
    else:
        print(f"Path: {repository}")
        print(f"Index version: {result.index_version or 'unavailable'}")
        print(f"Tracked files: {len(result.files)}")
        for finding in result.findings:
            print(f"{finding['severity'].upper()} {finding['code']}: {finding['detail']}")
    return 0 if result.safe else 1


def _run_fix(args: argparse.Namespace, repository: Path) -> int:
    before = report(repository)
    score_before = int(before["score"])
    plans = plan_fixes(
        repository,
        license_name=args.license_name,
        holder=args.holder,
        runtime_version=args.runtime_version,
    )
    plans = protect_score(repository, plans, score_before)
    outcomes = apply_fix_plan(plans, root=repository, dry_run=args.dry_run)
    if args.dry_run:
        score_after = project_fix_score(repository, outcomes)
    else:
        after = report(repository)
        score_after = int(after["score"])
    if args.as_json:
        print(
            json.dumps(
                _fix_result(
                    repository,
                    outcomes,
                    dry_run=args.dry_run,
                    score_before=score_before,
                    score_after=score_after,
                ),
                indent=2,
            )
        )
    else:
        _print_fix_report(
            repository,
            outcomes,
            dry_run=args.dry_run,
            score_before=score_before,
            score_after=score_after,
        )
    return 0


def _fix_item(operation: PlannedFile, repository: Path) -> dict[str, str]:
    return operation.as_dict(repository)


def _fix_result(
    repository: Path,
    outcomes: list[PlannedFile],
    *,
    dry_run: bool,
    score_before: int,
    score_after: int,
) -> dict[str, object]:
    creates = [operation for operation in outcomes if operation.action == "create"]
    existing = [operation for operation in outcomes if operation.action == "skip-existing"]
    unsafe = [operation for operation in outcomes if operation.action == "skip-unsafe"]
    manual = [operation for operation in outcomes if operation.action == "manual"]
    return {
        "tool": "repo-ready",
        "path": str(repository),
        "dry_run": dry_run,
        "operations": [_fix_item(operation, repository) for operation in outcomes],
        "created": [] if dry_run else [_fix_path(operation, repository) for operation in creates],
        "would_create": [_fix_path(operation, repository) for operation in creates] if dry_run else [],
        "skipped_existing": [_fix_item(operation, repository) for operation in existing],
        "skipped_unsafe": [_fix_item(operation, repository) for operation in unsafe],
        "manual": [_fix_item(operation, repository) for operation in manual],
        "score_before": score_before,
        "score_after": score_after,
        "created_count": len(creates),
        "manual_count": len(manual),
    }


def _print_fix_report(
    repository: Path,
    outcomes: list[PlannedFile],
    *,
    dry_run: bool,
    score_before: int,
    score_after: int,
) -> None:
    creates = [operation for operation in outcomes if operation.action == "create"]
    existing = [operation for operation in outcomes if operation.action == "skip-existing"]
    unsafe = [operation for operation in outcomes if operation.action == "skip-unsafe"]
    manual = [operation for operation in outcomes if operation.action == "manual"]

    action = "Would create" if dry_run else "Created"
    if creates:
        print(f"{action}:")
        for operation in creates:
            print(f"  {_fix_path(operation, repository)} — {operation.reason}")
        print(f"{action} {len(creates)} file(s).")
    else:
        print("No safe missing files can be scaffolded.")

    if existing:
        print("Skipped existing files:")
        for operation in existing:
            print(f"  {_fix_path(operation, repository)} — {operation.reason}")
    else:
        print("Skipped existing files: none were overwritten.")

    if unsafe:
        print("Skipped unsafe files:")
        for operation in unsafe:
            print(f"  {_fix_path(operation, repository)} — {operation.reason}")
    else:
        print("Skipped unsafe files: none.")

    if manual:
        print("Manual items:")
        for operation in manual:
            print(f"  {_fix_path(operation, repository)} — {operation.reason}")
    else:
        print("Manual items: none.")

    print(f"Score before: {score_before}/100")
    projection = " (projected)" if dry_run else ""
    print(f"Score after: {score_after}/100{projection}")
    print(f"Created count: {len(creates)}")
    print(f"Manual count: {len(manual)}")

    if dry_run:
        print("Dry run: no files were written.")


def _fix_path(operation: PlannedFile, repository: Path) -> str:
    try:
        return operation.path.relative_to(repository).as_posix()
    except ValueError:
        return operation.path.as_posix()


def main(argv: list[str] | None = None) -> int:
    if argv is not None and argv == ["--version"]:
        print(__version__)
        return 0
    args = _parser().parse_args(argv)
    supplied_path = args.path if args.path is not None else Path.cwd()
    if args.command == "artifact":
        try:
            return _run_artifact(args)
        except OSError as error:
            print(f"repo-ready: artifact error: {error}", file=sys.stderr)
            return 2
    repository = _resolve_repository(supplied_path)
    if repository is None:
        print(f"repo-ready: repository path is not a directory or is unreadable: {supplied_path}", file=sys.stderr)
        return 2
    try:
        if args.command == "check":
            args.path = supplied_path
            return _run_check(args, repository)
        if args.command == "diagnose":
            args.path = supplied_path
            return _run_diagnose(args, repository)
        if args.command == "compare":
            args.path = supplied_path
            return _run_compare(args, repository)
        if args.command == "tracked":
            args.path = supplied_path
            return _run_tracked(args, repository)
        return _run_fix(args, repository)
    except ConfigError as error:
        print(f"repo-ready: configuration error: {error}", file=sys.stderr)
        return 2
    except BaselineError as error:
        print(f"repo-ready: baseline error: {error}", file=sys.stderr)
        return 2


__all__ = ["main"]
