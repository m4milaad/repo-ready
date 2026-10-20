"""Opt-in execution verification for a local repository.

The installed ``repo-ready`` CLI remains static and safe by default. This
separate checkout script is the explicit boundary for running a detected or
supplied test command. It never invokes a shell, uses a bounded timeout, and
limits captured output.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_MAX_OUTPUT_CHARS = 12_000
VerificationStatus = Literal["passed", "failed", "timed-out", "not-found", "error"]


class VerificationError(ValueError):
    """Raised when verification cannot be started safely."""


@dataclass(frozen=True)
class VerificationResult:
    path: str
    command: tuple[str, ...]
    source: Literal["detected", "explicit"]
    status: VerificationStatus
    returncode: int | None
    duration_seconds: float
    timeout_seconds: float
    stdout: str
    stderr: str
    output_truncated: bool

    @property
    def command_display(self) -> str:
        return shlex.join(self.command)

    def as_dict(self) -> dict[str, object]:
        return {
            "tool": "repo-ready",
            "path": self.path,
            "command": self.command_display,
            "argv": list(self.command),
            "source": self.source,
            "status": self.status,
            "returncode": self.returncode,
            "duration_seconds": round(self.duration_seconds, 3),
            "timeout_seconds": self.timeout_seconds,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "output_truncated": self.output_truncated,
        }


def _parse_command(value: str) -> tuple[str, ...]:
    try:
        command = tuple(shlex.split(value, posix=True))
    except ValueError as error:
        raise VerificationError(f"invalid command quoting: {error}") from error
    if not command:
        raise VerificationError("the verification command must not be empty")
    return command


def _command_argv(
    command: str | Sequence[str] | None,
    root: Path,
) -> tuple[tuple[str, ...], Literal["detected", "explicit"]]:
    if command is not None:
        if isinstance(command, str):
            return _parse_command(command), "explicit"
        values = tuple(str(part) for part in command)
        if not values:
            raise VerificationError("the verification command must not be empty")
        return values, "explicit"

    from repo_ready.checks import discover_test_command
    from repo_ready.context import RepoContext

    detected = discover_test_command(RepoContext(root))
    if detected is None:
        raise VerificationError(
            "no supported test command was detected; supply one with --command"
        )
    return _parse_command(detected), "detected"


def _text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _bounded(value: bytes | str | None, limit: int) -> tuple[str, bool]:
    text = _text(value)
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n...[output truncated by repo-ready]", True


def _terminate(process: subprocess.Popen[bytes]) -> None:
    if os.name == "nt":
        process.kill()
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        try:
            process.kill()
        except OSError:
            pass


def run_verification(
    root: str | os.PathLike[str] | Path,
    *,
    command: str | Sequence[str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
) -> VerificationResult:
    """Run one explicit verification command from *root* without a shell."""

    repository = Path(root).expanduser().resolve()
    if not repository.is_dir():
        raise VerificationError(f"repository path is not a directory: {root}")
    if timeout <= 0:
        raise VerificationError("timeout must be greater than zero")
    if max_output_chars < 0:
        raise VerificationError("max output chars must not be negative")

    argv, source = _command_argv(command, repository)
    started = time.monotonic()
    process: subprocess.Popen[bytes] | None = None
    try:
        options: dict[str, object] = {
            "cwd": repository,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "shell": False,
        }
        if os.name == "nt":
            options["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            options["start_new_session"] = True
        process = subprocess.Popen(list(argv), **options)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            _terminate(process)
            stdout, stderr = process.communicate()
            if not stdout:
                stdout = error.stdout
            if not stderr:
                stderr = error.stderr
            stdout_text, stdout_truncated = _bounded(stdout, max_output_chars)
            stderr_text, stderr_truncated = _bounded(stderr, max_output_chars)
            return VerificationResult(
                path=str(repository),
                command=argv,
                source=source,
                status="timed-out",
                returncode=None,
                duration_seconds=time.monotonic() - started,
                timeout_seconds=float(timeout),
                stdout=stdout_text,
                stderr=stderr_text,
                output_truncated=stdout_truncated or stderr_truncated,
            )
    except FileNotFoundError as error:
        return VerificationResult(
            path=str(repository),
            command=argv,
            source=source,
            status="not-found",
            returncode=None,
            duration_seconds=time.monotonic() - started,
            timeout_seconds=float(timeout),
            stdout="",
            stderr=f"executable not found: {error.filename or argv[0]}",
            output_truncated=False,
        )
    except OSError as error:
        return VerificationResult(
            path=str(repository),
            command=argv,
            source=source,
            status="error",
            returncode=None,
            duration_seconds=time.monotonic() - started,
            timeout_seconds=float(timeout),
            stdout="",
            stderr=str(error),
            output_truncated=False,
        )

    assert process is not None
    stdout_text, stdout_truncated = _bounded(stdout, max_output_chars)
    stderr_text, stderr_truncated = _bounded(stderr, max_output_chars)
    return VerificationResult(
        path=str(repository),
        command=argv,
        source=source,
        status="passed" if process.returncode == 0 else "failed",
        returncode=process.returncode,
        duration_seconds=time.monotonic() - started,
        timeout_seconds=float(timeout),
        stdout=stdout_text,
        stderr=stderr_text,
        output_truncated=stdout_truncated or stderr_truncated,
    )


def _print_result(result: VerificationResult, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result.as_dict(), indent=2))
        return
    print(f"Path: {result.path}")
    print(f"Command: {result.command_display}")
    print(f"Source: {result.source}")
    print(f"Status: {result.status}")
    if result.returncode is not None:
        print(f"Exit code: {result.returncode}")
    print(f"Duration: {result.duration_seconds:.2f}s")
    if result.stdout:
        print("--- stdout ---")
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print("--- stderr ---", file=sys.stderr)
        print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")
    if result.output_truncated:
        print("Output was truncated.", file=sys.stderr)


def verify_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Opt-in execution verification for a local repository.")
    parser.add_argument("path", nargs="?", type=Path, default=Path.cwd())
    parser.add_argument("--command", help="command to run without a shell")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--max-output-chars", type=int, default=DEFAULT_MAX_OUTPUT_CHARS)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run_verification(
            args.path,
            command=args.command,
            timeout=args.timeout,
            max_output_chars=args.max_output_chars,
        )
    except VerificationError as error:
        print(f"verify: error: {error}", file=sys.stderr)
        return 2
    _print_result(result, as_json=args.json)
    return 0 if result.status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(verify_main())
