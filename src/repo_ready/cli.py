"""Command-line interface for offline repository readiness checks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .fixer import apply_fixes, plan_fixes
from .scoring import report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="repo-ready")
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check", help="score a local repository")
    check.add_argument("path", type=Path)
    check.add_argument("--json", action="store_true", dest="as_json")

    fix = commands.add_parser("fix", help="scaffold missing repository files")
    fix.add_argument("path", type=Path)
    fix.add_argument("--dry-run", action="store_true")
    return parser


def _resolve_repository(path: Path) -> Path | None:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        return None
    return resolved if resolved.is_dir() else None


def _print_human_report(repository: Path, result: dict[str, object]) -> None:
    print(f"Repository: {repository}")
    print(f"Score: {result['score']}/{result.get('max', 100)} (grade {result.get('grade', 'N/A')})")
    ecosystems = result["ecosystems"]
    print(f"Ecosystems: {', '.join(ecosystems) if ecosystems else 'none'}")
    checks = result["checks"]
    if isinstance(checks, list):
        for check in checks:
            if isinstance(check, dict):
                print(f"  {str(check.get('status', '')).upper():<7} {check.get('id', '')} {check.get('message', '')}")
    else:
        for name, passed in checks.items():
            print(f"  {'PASS' if passed else 'FAIL'} {name}")


def _run_check(repository: Path, as_json: bool) -> int:
    result = report(repository)
    if as_json:
        print(json.dumps(result, indent=2))
    else:
        _print_human_report(repository, result)
    return 0


def _run_fix(repository: Path, dry_run: bool) -> int:
    plans = plan_fixes(repository)
    if not plans:
        print("No missing files can be scaffolded.")
        return 0
    action = "Would create" if dry_run else "Created"
    completed = apply_fixes(plans, dry_run=dry_run)
    for path in completed:
        print(f"{action} {path.relative_to(repository)}")
    if dry_run:
        print(f"Dry run: {len(completed)} file(s) planned.")
    else:
        print(f"Created {len(completed)} file(s).")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repository = _resolve_repository(args.path)
    if repository is None:
        print(f"repo-ready: repository path is not a directory: {args.path}", file=sys.stderr)
        return 2
    if args.command == "check":
        return _run_check(repository, args.as_json)
    return _run_fix(repository, args.dry_run)
