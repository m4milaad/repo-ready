"""Optional browser smoke gate for a generated offline showcase report.

This script is intentionally separate from the standard-library smoke gate:
the project runtime does not require a browser automation package. Install
Playwright and a browser locally when recording or reviewing the visual flow::

    python -m pip install playwright
    python -m playwright install chromium
    python scripts/browser_smoke.py path/to/repo-report.html
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke-test an offline repo-ready report in a browser.")
    parser.add_argument("report", type=Path, help="generated repo-ready showcase HTML file")
    return parser


def run(report: Path) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("browser smoke: skipped (install playwright to run this optional gate)")
        return

    if not report.is_file():
        raise FileNotFoundError(f"showcase report is missing: {report}")

    errors: list[str] = []
    console: list[str] = []
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except Exception as error:  # browser binaries are optional developer tooling
            print(f"browser smoke: skipped (browser launch unavailable: {error})")
            return
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "console",
                lambda message: console.append(f"{message.type}: {message.text}")
                if message.type in {"error", "warning"}
                else None,
            )
            page.goto(report.resolve().as_uri(), wait_until="load")
            page.wait_for_timeout(200)

            available = page.locator("#scenario-options button:not([disabled])")
            if not page.locator("#interactive-report").is_visible():
                raise AssertionError("interactive report is not visible")
            if page.locator("#static-report").is_visible():
                raise AssertionError("static fallback remained visible after controller startup")
            if available.count() < 2:
                raise AssertionError("the report has no available simulated scenario")
            if page.locator("#evidence-map svg").count() != 1:
                raise AssertionError("evidence map SVG is missing")
            if page.locator("#evidence-map .map-node").count() == 0:
                raise AssertionError("evidence map has no nodes")
            if not page.locator("#download-receipt").is_visible():
                raise AssertionError("receipt download is not available")

            available.nth(1).click()
            page.wait_for_timeout(100)
            if page.locator("#overview-title").inner_text() != "Simulated":
                raise AssertionError("scenario did not switch to Simulated view")
            if not page.locator("#scenario-details").is_visible():
                raise AssertionError("scenario details did not open")

            map_node = page.locator("#evidence-map .map-node").first
            map_node.focus()
            page.keyboard.press("Enter")
            page.wait_for_timeout(100)
            if "evidence records in the selected chain" not in page.locator("#selection-status").inner_text():
                raise AssertionError("map node selection did not highlight evidence")

            page.set_viewport_size({"width": 390, "height": 844})
            if page.evaluate("document.documentElement.scrollWidth > window.innerWidth"):
                raise AssertionError("mobile viewport has document overflow")

            with page.expect_download() as receipt:
                page.locator("#download-receipt").click()
            if receipt.value.suggested_filename != "repo-ready-receipt.json":
                raise AssertionError("receipt download has an unexpected filename")
        finally:
            browser.close()

    if errors or console:
        raise AssertionError(f"browser reported errors: page={errors!r}, console={console!r}")
    print("browser smoke: passed (desktop, scenario, evidence map, receipt, mobile)")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        run(args.report)
    except Exception as error:
        print(f"browser smoke: failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
