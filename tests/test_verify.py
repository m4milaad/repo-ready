from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from repo_ready.checks import discover_test_command
from repo_ready.context import RepoContext
from scripts import verify


def command_text(*arguments: str) -> str:
    return shlex.join((sys.executable, *arguments))


class FakeProcess:
    last_kwargs: dict[str, object] = {}

    def __init__(self, argv: list[str], **_kwargs: object) -> None:
        self.argv = argv
        self.last_kwargs = _kwargs
        type(self).last_kwargs = _kwargs
        self.pid = 123
        self.returncode = 0
        self.killed = False

    def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
        del timeout
        code = self.argv[-1]
        if "sleep(1)" in code and not self.killed:
            raise subprocess.TimeoutExpired(self.argv, 0.05)
        if "sys.exit(7)" in code:
            self.returncode = 7
            return b"failure\n", b""
        if "json ok" in code:
            return b"json ok\n", b""
        return b"verified\n", b""

    def kill(self) -> None:
        self.killed = True


@pytest.fixture
def fake_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(verify.subprocess, "Popen", FakeProcess)


def test_explicit_verification_passes_and_captures_output(tmp_path: Path, fake_process: None) -> None:
    del fake_process
    result = verify.run_verification(
        tmp_path,
        command=(sys.executable, "-c", "print('verified')"),
    )

    assert result.status == "passed"
    assert result.returncode == 0
    assert result.source == "explicit"
    assert FakeProcess.last_kwargs["shell"] is False
    assert result.stdout == "verified\n"
    assert result.stderr == ""


def test_failed_verification_returns_the_project_exit_code(tmp_path: Path, fake_process: None) -> None:
    del fake_process
    result = verify.run_verification(
        tmp_path,
        command=(sys.executable, "-c", "import sys; print('failure'); sys.exit(7)"),
    )

    assert result.status == "failed"
    assert result.returncode == 7
    assert result.stdout == "failure\n"


def test_verification_timeout_is_bounded(tmp_path: Path, fake_process: None) -> None:
    del fake_process
    result = verify.run_verification(
        tmp_path,
        command=(sys.executable, "-c", "import time; time.sleep(1)"),
        timeout=0.05,
    )

    assert result.status == "timed-out"
    assert result.returncode is None
    assert result.duration_seconds < 1


def test_detection_reuses_rr09_command_selection(tmp_path: Path, fake_process: None) -> None:
    del fake_process
    (tmp_path / "Makefile").write_text("test:\n\tpython -m pytest -q\n", encoding="utf-8")

    assert discover_test_command(RepoContext(tmp_path)) == "make test"
    result = verify.run_verification(tmp_path)
    assert result.source == "detected"
    assert result.command == ("make", "test")


def test_missing_detected_command_requires_explicit_opt_in(tmp_path: Path) -> None:
    with pytest.raises(verify.VerificationError, match="no supported test command"):
        verify.run_verification(tmp_path)


def test_cli_verify_json_is_machine_readable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    fake_process: None,
) -> None:
    del fake_process
    assert verify.verify_main(
        [
            str(tmp_path),
            "--command",
            command_text("-c", "print('json ok')"),
            "--json",
        ]
    ) == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["status"] == "passed"
    assert payload["source"] == "explicit"
    assert payload["stdout"] == "json ok\n"
