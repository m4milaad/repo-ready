from pathlib import Path
import socket
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))




@pytest.fixture(autouse=True)
def offline_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every test fail loudly if application code leaves the sandbox."""

    def blocked(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network and subprocess access are forbidden in tests")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(subprocess, "Popen", blocked)


# Fixture repositories are data, not pytest packages.  The list is kept for
# compatibility with the original layout while the current fixtures all use a
# dedicated ``repo/`` child directory.
collect_ignore = [
    str(PROJECT_ROOT / "tests" / "fixtures" / "python-complete" / "tests" / "test_example.py"),
]
collect_ignore_glob = ["fixtures/**/repo/**/test_*.py"]
