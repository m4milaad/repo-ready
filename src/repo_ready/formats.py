"""Machine and review-friendly renderers for readiness reports."""

from __future__ import annotations

import json
from typing import Mapping


def render_markdown(report: Mapping[str, object]) -> str:
    checks = report.get("checks", [])
    lines = [
        f"# Repository readiness: {report.get('score', 0)}/{report.get('max', 0)}",
        "",
        f"- Grade: **{report.get('grade', 'unknown')}**",
        f"- Ecosystems: {', '.join(report.get('ecosystems', [])) or 'none'}",
        "",
        "| ID | Check | Status | Score | Message |",
        "| --- | --- | --- | ---: | --- |",
    ]
    if isinstance(checks, list):
        for item in checks:
            if not isinstance(item, dict):
                continue
            message = str(item.get("message", "")).replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {item.get('id', '')} | {item.get('title', '')} | {item.get('status', '')} | "
                f"{item.get('earned', 0)}/{item.get('weight', 0)} | {message} |"
            )
    lines.append("")
    return "\n".join(lines)


def render_sarif(report: Mapping[str, object]) -> str:
    results: list[dict[str, object]] = []
    checks = report.get("checks", [])
    if isinstance(checks, list):
        for item in checks:
            if not isinstance(item, dict) or item.get("status") in {"pass", "na"}:
                continue
            findings = item.get("findings", [])
            findings = findings if isinstance(findings, list) else []
            locations = []
            for finding in findings:
                if not isinstance(finding, dict):
                    continue
                location: dict[str, object] = {
                    "physicalLocation": {
                        "artifactLocation": {"uri": finding.get("file", ".")},
                    }
                }
                line = finding.get("line")
                if isinstance(line, int) and line > 0:
                    location["physicalLocation"]["region"] = {"startLine": line}  # type: ignore[index]
                locations.append(location)
            result: dict[str, object] = {
                "ruleId": item.get("id", "unknown"),
                "level": "error" if item.get("status") == "fail" else "warning",
                "message": {"text": str(item.get("message", ""))},
            }
            if locations:
                result["locations"] = locations
            results.append(result)
    payload = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "repo-ready", "version": str(report.get("version", "unknown"))}},
                "results": results,
            }
        ],
    }
    return json.dumps(payload, indent=2)


__all__ = ["render_markdown", "render_sarif"]
