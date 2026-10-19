from __future__ import annotations

from pathlib import Path

from repo_ready.showcase import build_showcase


FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "showcase-demo"


def _fixture_bytes() -> dict[str, bytes]:
    return {
        path.relative_to(FIXTURE).as_posix(): path.read_bytes()
        for path in FIXTURE.rglob("*")
        if path.is_file()
    }


def test_showcase_demo_fixture_has_all_breakage_lab_scenarios_without_writes() -> None:
    before = _fixture_bytes()

    payload = build_showcase(FIXTURE)

    scenarios = payload["analysis"]["scenarios"]
    assert {item["id"] for item in scenarios} == {
        "remove-runtime-pin",
        "ci-runtime-drift",
        "remove-referenced-dependency-file",
        "remove-test-entry-point",
    }
    assert all(item["status"] == "available" for item in scenarios)
    assert any(item["new_diagnostics"] and item["score_delta"] < 0 for item in scenarios)

    repair = payload["analysis"]["baseline"]["repair"]
    create_operations = [item for item in repair["operations"] if item["action"] == "create"]
    assert create_operations
    assert repair["patch"]
    assert all(item["action"] == "create" for item in create_operations)
    assert {item["path"] for item in create_operations} >= {
        "CONTRIBUTING.md",
        ".github/ISSUE_TEMPLATE/bug_report.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
    }

    assert _fixture_bytes() == before
