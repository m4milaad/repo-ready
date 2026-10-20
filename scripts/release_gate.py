"""Run the repeatable checks required before publishing a release."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from repo_ready.artifact import inspect_artifact
from scripts.release_check import ReleaseCheckError, validate_release


class ReleaseGateError(RuntimeError):
    """Raised when a release gate command fails."""


def _command(*parts: str) -> list[str]:
    return [sys.executable, *parts]


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    print("release gate: $ " + " ".join(command))
    completed = subprocess.run(command, cwd=cwd, env=env, check=False)
    if completed.returncode:
        raise ReleaseGateError(f"command failed with exit code {completed.returncode}: {' '.join(command)}")


def _inspect(path: Path, root: Path) -> None:
    result = inspect_artifact(path, project=root)
    if not result.safe:
        findings = "; ".join(f"{item.code}: {item.detail}" for item in result.findings)
        raise ReleaseGateError(f"artifact is unsafe: {path.name}: {findings}")
    print(f"release gate: artifact safe: {path.name} ({len(result.members)} members)")


def run_gate(
    root: str | Path,
    *,
    expected_version: str | None = None,
    output_dir: str | Path | None = None,
    skip_docker: bool = False,
    skip_browser: bool = False,
) -> str:
    """Run release checks and return the validated package version."""

    repository = Path(root).resolve()
    try:
        messages = validate_release(repository, expected_version)
    except ReleaseCheckError as error:
        raise ReleaseGateError(str(error)) from error
    for message in messages:
        print(f"release gate: {message}")
    version = messages[0].split(": ", 1)[1]

    destination = Path(output_dir).resolve() if output_dir else repository / "dist" / "release-gate"
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True, exist_ok=True)

    _run(_command("-m", "build", "--sdist", "--wheel", "--outdir", str(destination)), cwd=repository)
    artifacts = sorted(path for path in destination.iterdir() if path.is_file())
    wheels = [path for path in artifacts if path.suffix == ".whl"]
    sdists = [path for path in artifacts if path.name.endswith((".tar.gz", ".zip"))]
    if len(wheels) != 1 or len(sdists) != 1:
        raise ReleaseGateError(
            f"expected one wheel and one source distribution, found {len(wheels)} wheel(s) and {len(sdists)} sdist(s)"
        )
    for artifact in (wheels[0], sdists[0]):
        _inspect(artifact, repository)
    _run(_command("-m", "twine", "check", str(wheels[0]), str(sdists[0])), cwd=repository)

    _run(_command("scripts/showcase_smoke.py"), cwd=repository)
    if not skip_browser:
        report = destination / "repo-report.html"
        _run(_command("-m", "repo_ready", "showcase", "examples/showcase-demo", "--output", str(report)), cwd=repository)
        _run(_command("scripts/browser_smoke.py", str(report)), cwd=repository)

    if not skip_docker:
        image = f"repo-ready:release-gate-{version}"
        _run(["docker", "build", "--pull", "-t", image, "."], cwd=repository)
        _run(["docker", "run", "--rm", "--network", "none", image, "python", "-m", "pytest", "-q"], cwd=repository)

    print(f"release gate: passed for {version}")
    return version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run repo-ready's release verification gate.")
    parser.add_argument("expected", nargs="?", help="expected release version, with or without a leading v")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--skip-docker", action="store_true")
    parser.add_argument("--skip-browser", action="store_true")
    args = parser.parse_args(argv)
    try:
        run_gate(
            args.root,
            expected_version=args.expected,
            output_dir=args.output_dir,
            skip_docker=args.skip_docker,
            skip_browser=args.skip_browser,
        )
    except (OSError, ReleaseGateError) as error:
        print(f"release gate: failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
