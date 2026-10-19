"""Repeatable, dependency-free smoke gate for the checked-in showcase fixture."""

from __future__ import annotations

from contextlib import redirect_stdout
from importlib import resources
from io import StringIO
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from repo_ready.cli import main
from repo_ready.showcase import build_showcase
from repo_ready.showcase_html import render_showcase


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = PROJECT_ROOT / "examples" / "showcase-demo"
EXPECTED_SCENARIOS = {
    "remove-runtime-pin",
    "ci-runtime-drift",
    "remove-referenced-dependency-file",
    "remove-test-entry-point",
}
PACKAGED_ASSETS = ("report.html", "report.css", "report.js")


def _fixture_bytes() -> dict[str, bytes]:
    return {
        path.relative_to(FIXTURE).as_posix(): path.read_bytes()
        for path in FIXTURE.rglob("*")
        if path.is_file()
    }


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def run() -> None:
    _assert(FIXTURE.is_dir(), f"showcase fixture is missing: {FIXTURE}")
    before = _fixture_bytes()

    packaged = resources.files("repo_ready").joinpath("showcase_assets")
    for name in PACKAGED_ASSETS:
        asset = packaged.joinpath(name)
        _assert(asset.is_file(), f"packaged showcase asset is missing: {name}")
        _assert(bool(asset.read_text(encoding="utf-8")), f"packaged showcase asset is empty: {name}")

    payload = build_showcase(FIXTURE)
    _assert(payload == build_showcase(FIXTURE), "showcase payload is not deterministic")

    scenarios = payload["analysis"]["scenarios"]
    scenario_ids = {item["id"] for item in scenarios}
    _assert(scenario_ids == EXPECTED_SCENARIOS, f"unexpected scenario IDs: {scenario_ids!r}")
    _assert(len(scenarios) == len(EXPECTED_SCENARIOS), "showcase scenarios contain duplicate IDs")
    _assert(
        all(item["status"] == "available" for item in scenarios),
        "one or more showcase scenarios are unavailable",
    )

    rendered = render_showcase(payload)
    _assert(rendered.startswith("<!doctype html>"), "rendered showcase does not begin with doctype")

    with TemporaryDirectory(prefix="repo-ready-showcase-") as temporary:
        output = Path(temporary) / "report.html"
        cli_output = StringIO()
        with redirect_stdout(cli_output):
            status = main(["showcase", str(FIXTURE), "--output", str(output)])
        _assert(status == 0, f"showcase CLI returned {status}: {cli_output.getvalue()!r}")
        cli_document = output.read_text(encoding="utf-8")
        _assert(cli_document.startswith("<!doctype html>"), "CLI report does not begin with doctype")
        _assert(cli_document == rendered, "CLI showcase output differs from render_showcase output")

    _assert(_fixture_bytes() == before, "showcase flow modified the target fixture bytes")
    print(
        "showcase smoke: passed "
        f"({len(EXPECTED_SCENARIOS)} scenarios, {len(PACKAGED_ASSETS)} packaged assets, "
        "doctype, fixture unchanged)"
    )


if __name__ == "__main__":
    try:
        run()
    except Exception as error:
        print(f"showcase smoke: failed: {error}", file=sys.stderr)
        raise SystemExit(1)
