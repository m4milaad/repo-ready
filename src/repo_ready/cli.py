"""Command-line interface for deterministic offline repository checks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .context import RepoContext
from .fixer import apply_fixes, plan_fixes
from .scoring import report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="repo-ready")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check", help="score a local repository")
    check.add_argument("path", nargs="?", type=Path, default=None)
    check.add_argument("--json", action="store_true", dest="as_json")
    check.add_argument("--min-score", type=int, default=None)
    check.add_argument("--no-color", action="store_true")

    fix = commands.add_parser("fix", help="scaffold missing repository files")
    fix.add_argument("path", nargs="?", type=Path, default=None)
    fix.add_argument("--dry-run", action="store_true")
    fix.add_argument("--license", choices=("mit",), dest="license_name")
    fix.add_argument("--holder", default=None)
    fix.add_argument("--runtime-version", default=None)
    return parser


def _resolve_repository(path: Path) -> Path | None:
    try:
        resolved = path.expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return resolved if resolved.is_dir() else None


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
        print(f"{status:7} {check['id']} {check['title']}: {check['message']} ({check['earned']}/{check['weight']})")
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
    if args.as_json:
        print(json.dumps(result, indent=2))
    else:
        _print_human_report(result, no_color=args.no_color)
    context = RepoContext(repository)
    threshold = args.min_score if args.min_score is not None else context.min_score
    if threshold is not None and int(result["score"]) < threshold:
        return 1
    return 0


def _run_fix(args: argparse.Namespace, repository: Path) -> int:
    plans = plan_fixes(
        repository,
        license_name=args.license_name,
        holder=args.holder,
        runtime_version=args.runtime_version,
    )
    completed = apply_fixes(plans, root=repository, dry_run=args.dry_run)
    action = "Would create" if args.dry_run else "Created"
    for path in completed:
        print(f"{action} {path.relative_to(repository)}")
    if not completed:
        print("No safe missing files can be scaffolded.")
    else:
        print(f"{action} {len(completed)} file(s).")
    print("Skipped existing files: none were overwritten.")
    print("Manual items: lockfiles, test commands, and machine-specific paths require human changes.")
    if args.license_name != "mit" or not args.holder:
        print("Manual item: LICENSE is created only with --license mit --holder NAME.")
    if args.dry_run:
        print("Dry run: no files were written.")
    return 0


def main(argv: list[str] | None = None) -> int:
    if argv is not None and argv == ["--version"]:
        print(__version__)
        return 0
    args = _parser().parse_args(argv)
    supplied_path = args.path if args.path is not None else Path.cwd()
    repository = _resolve_repository(supplied_path)
    if repository is None:
        print(f"repo-ready: repository path is not a directory or is unreadable: {supplied_path}", file=sys.stderr)
        return 2
    if args.command == "check":
        args.path = supplied_path
        return _run_check(args, repository)
    return _run_fix(args, repository)


__all__ = ["main"]
