from pathlib import Path
import os
import socket
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if os.environ.get("REPO_READY_TEST_NO_SRC") != "1" and str(SRC_ROOT) not in sys.path:
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


# Fixture repositories are data, not pytest packages.
collect_ignore_glob = ["fixtures/**/repo/**/test_*.py"]
