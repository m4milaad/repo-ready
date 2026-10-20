"""Render a computed showcase payload as a self-contained, offline HTML string.

This module only reads its packaged presentation assets. Analysis, simulations,
receipt fingerprints, and repair plans belong to the caller; rendering never
executes repository code or writes an output file.
"""

from __future__ import annotations

import base64
import hashlib
import html
from importlib import resources
import json
import re

from . import __version__


_SLOTS = re.compile(r"@@([A-Z_]+)@@")


def _asset(name: str) -> str:
    try:
        return resources.files("repo_ready").joinpath("showcase_assets", name).read_text(
            encoding="utf-8"
        )
    except (OSError, UnicodeError) as error:
        raise RuntimeError(f"Showcase asset is missing or unreadable: {name}") from error


def _json(value: object) -> str:
    # ASCII JSON also escapes U+2028/U+2029. Escape HTML delimiters even inside
    # application/json scripts: the HTML parser still recognizes </script>.
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _hash(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return "'sha256-" + base64.b64encode(digest).decode("ascii") + "'"


def _list(items: list[object], empty: str) -> str:
    if not items:
        return f'<p class="empty">{_escape(empty)}</p>'
    return '<ul class="plain-list">' + "".join(f"<li>{_escape(item)}</li>" for item in items) + "</ul>"


_REPAIR_CATEGORY_LABELS = {
    "license": "License decision",
    "runtime": "Runtime choice",
    "container": "Container setup",
    "lockfile": "Lockfiles",
    "test": "Tests and verification",
    "machine-specific": "Machine-specific paths",
    "score-protection": "Score protection",
    "other": "Other review",
}
_REPAIR_CATEGORY_ORDER = tuple(_REPAIR_CATEGORY_LABELS)


def _repair_category(item: dict) -> str:
    supplied = str(item.get("category", "")).strip().casefold()
    if supplied in _REPAIR_CATEGORY_LABELS:
        return supplied
    haystack = " ".join(str(item.get(key, "")) for key in ("file", "message", "reason")).casefold()
    if "license" in haystack or "copyright" in haystack:
        return "license"
    if any(value in haystack for value in ("runtime", "python", "node", "version", "pin")):
        return "runtime"
    if "docker" in haystack or "container" in haystack:
        return "container"
    if "lockfile" in haystack or "lock file" in haystack:
        return "lockfile"
    if "test" in haystack or "verification" in haystack:
        return "test"
    if "machine" in haystack or "absolute path" in haystack:
        return "machine-specific"
    if "score" in haystack or "regress" in haystack:
        return "score-protection"
    return "other"


def _static_baseline(payload: dict) -> str:
    """Useful baseline content even when the controller is disabled or blocked."""
    analysis = payload["analysis"]
    baseline = analysis["baseline"]
    report = baseline["report"]
    graph = baseline["graph"]
    repair = baseline.get("repair")
    parts = [
        '<section class="overview" aria-labelledby="static-title">',
        '<div><p class="eyebrow">01 / Baseline</p><h2 id="static-title">Observed (static)</h2>',
        f'<p class="score">{_escape(report.get("score", "—"))}'
        f'<span class="score-denominator"> / {_escape(report.get("max", 100))}</span></p>',
        f'<p>{_escape(report.get("grade", ""))}</p></div>',
        '<div class="margin-note"><p>Static observations, not an execution result. Enable JavaScript for '
        'evidence selection, precomputed scenarios, and downloads.</p></div></section>',
        '<section class="report-section"><h2>Readiness checks</h2>',
    ]
    checks = report.get("checks", [])
    if not checks:
        parts.append('<p class="empty">No readiness checks were included in this report.</p>')
    for check in checks:
        parts.append(
            '<article class="ledger-item">'
            f'<h3>{_escape(check.get("id", ""))} · {_escape(check.get("title", ""))}</h3>'
            f'<p>{_escape(check.get("status", ""))} · {_escape(check.get("earned", 0))}'
            f' / {_escape(check.get("weight", 0))}</p><p>{_escape(check.get("message", ""))}</p>'
        )
        parts.append(_list([
            f'{finding.get("file", "")}:{finding.get("line") or "—"} — {finding.get("detail", "")}'
            for finding in check.get("findings", [])
        ], "No file-level findings for this check."))
        if check.get("fix_hint"):
            parts.append(f'<p>{_escape(check["fix_hint"])}</p>')
        parts.append("</article>")
    parts.append('</section><section class="report-section"><h2>Contract diagnostics</h2>')
    parts.append(_list([
        f'{item.get("code", "")} · {item.get("severity", "")} — {item.get("message", "")} '
        f'| Evidence: {", ".join(item.get("evidence", [])) or "none"}'
        for item in graph.get("diagnostics", [])
    ], "No contract diagnostics were included. This is not proof of runtime correctness."))
    parts.append('</section><section class="report-section"><h2>Evidence ledger</h2>')
    evidence = graph.get("evidence", [])
    if evidence:
        parts.append('<div class="table-scroll" tabindex="0" role="region" '
                     'aria-label="Static evidence ledger"><table><caption>Baseline evidence</caption>'
                     '<thead><tr><th scope="col">File / line</th><th scope="col">Kind / subject</th>'
                     '<th scope="col">Value</th></tr></thead><tbody>')
        for item in evidence:
            parts.append(
                f'<tr><th scope="row"><code>{_escape(item.get("file", ""))}'
                f':{_escape(item.get("line") or "—")}</code></th>'
                f'<td>{_escape(item.get("kind", ""))} / {_escape(item.get("subject", ""))}</td>'
                f'<td><pre>{_escape(item.get("value", ""))}</pre>'
                f'<small>ID: {_escape(item.get("id", ""))}</small></td></tr>'
            )
        parts.append("</tbody></table></div>")
    else:
        parts.append('<p class="empty">No file evidence was included; there is no schematic to trace.</p>')
    parts.append('<h3>Evidence map / accessible relationship list</h3>')
    parts.append('<p class="caption">JavaScript adds the visual map. This deterministic relationship list is the static, accessible equivalent and remains available when the controller is disabled.</p>')
    parts.append(_list([
        f'{edge.get("source", "")} → {edge.get("target") or "unresolved target"} · '
        f'{edge.get("relation", "")} · {edge.get("status", "")} — {edge.get("detail", "")} '
        f'| Expected: {edge.get("expected")} | Actual: {edge.get("actual")}'
        for edge in graph.get("edges", [])
    ], "No relationships were included."))
    parts.append('</section><section class="report-section"><h2>Repair notes</h2>')
    if repair:
        projected_delta = repair.get("score_delta")
        if projected_delta is None:
            try:
                projected_delta = repair["score_after"] - repair["score_before"]
            except (KeyError, TypeError):
                projected_delta = "not supplied"
        parts.append(f'<p>Before: {_escape(repair.get("score_before", "—"))} · '
                     f'Projected: {_escape(repair.get("score_after", "—"))} · '
                     f'Projected delta: {_escape(projected_delta)}</p>')
        operations = repair.get("operations", [])
        creates = [item for item in operations if item.get("action") == "create"]
        existing = repair.get("existing") or [item for item in operations if item.get("action") == "skip-existing"]
        parts.append('<h3>Safe create operations</h3>')
        parts.append(_list([
            f'{item.get("action", "")} {item.get("path", "")} — {item.get("reason", "")}'
            for item in creates
        ], "No safe create operations were proposed."))
        parts.append('<h3>Already present / unchanged</h3>')
        parts.append(_list([
            f'{item.get("path", "")} — {item.get("reason", "")}'
            for item in existing
        ], "No existing files were carried forward in this view."))
        if repair.get("patch"):
            parts.append(f'<pre class="patch" tabindex="0">{_escape(repair["patch"])}</pre>')
        else:
            parts.append('<p class="empty">No downloadable patch was proposed for this view.</p>')
        parts.append('<h3>Remaining manual blockers</h3>')
        manual = repair.get("manual", [])
        if not manual:
            parts.append('<p class="empty">No remaining manual blockers were included.</p>')
        else:
            grouped: dict[str, list[dict]] = {}
            for item in manual:
                grouped.setdefault(_repair_category(item), []).append(item)
            for category in _REPAIR_CATEGORY_ORDER:
                items = grouped.get(category)
                if not items:
                    continue
                parts.append(f'<h4>{_escape(_REPAIR_CATEGORY_LABELS[category])}</h4>')
                parts.append(_list([
                    f'{item.get("file", "")} — {item.get("message", "")} '
                    f'| Related files: {", ".join(item.get("related_files", [])) or "none"}'
                    for item in items
                ], "No manual blockers in this category."))
        parts.append(_list(repair.get("limitations", []), "No additional repair limitations were supplied."))
    else:
        parts.append('<p class="empty">No repair plan was included in this report.</p>')
    parts.append('</section><section class="report-section"><h2>Breakage Lab</h2>')
    parts.append('<p>Scenarios are precomputed simulations. JavaScript enables switching between them.</p>')
    parts.append(_list([
        f'{item.get("label", "")} — Simulated · {item.get("status", "")} · '
        f'{item.get("description", "")} {item.get("reason") or ""}'
        for item in analysis.get("scenarios", [])
    ], "No precomputed scenarios were included."))
    parts.append('</section><section class="report-section"><h2>Analysis limits</h2>')
    parts.append(_list(analysis.get("limitations", []), "No additional analysis limitations were supplied."))
    parts.append('</section>')
    return "".join(parts)


def render_showcase(payload: dict) -> str:
    """Return an offline HTML document for the version-1 showcase data contract.

    ``payload`` must be JSON serializable and contain the precomputed analysis
    and receipt. Invalid envelopes raise ValueError; missing packaged assets
    raise RuntimeError. The payload and its receipt are preserved, not recomputed.
    """
    if not isinstance(payload, dict):
        raise TypeError("Showcase payload must be a dict")
    if (payload.get("schema_version") != 1 or payload.get("tool") != "repo-ready"
            or payload.get("tool_version") != __version__):
        raise ValueError(
            f"Expected a repo-ready {__version__} showcase payload with schema_version 1"
        )
    analysis = payload.get("analysis")
    receipt = payload.get("receipt")
    if (not isinstance(analysis, dict) or analysis.get("mode") != "static"
            or not isinstance(analysis.get("repository"), dict)
            or not isinstance(analysis.get("baseline"), dict) or not isinstance(receipt, dict)):
        raise ValueError("Showcase payload requires a static analysis, baseline, repository, and receipt")
    if (not isinstance(analysis["baseline"].get("report"), dict)
            or not isinstance(analysis["baseline"].get("graph"), dict)):
        raise ValueError("Showcase baseline requires a report and graph")

    template, style, script = (_asset(name) for name in ("report.html", "report.css", "report.js"))
    encoded_payload = _json(payload)
    encoded_receipt = _json({**receipt, "analysis": analysis})
    policy = "; ".join([
        "default-src 'none'",
        "script-src " + " ".join(_hash(value) for value in (script, encoded_payload, encoded_receipt)),
        "script-src-attr 'none'",
        "style-src " + _hash(style),
        "style-src-attr 'none'",
        "connect-src 'none'",
        "font-src 'none'",
        "img-src data:",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-src 'none'",
        "worker-src 'none'",
    ])
    repository = analysis["repository"]
    slots = {
        "CSP": _escape(policy),
        "STYLE": style,
        "SCRIPT": script,
        "PAYLOAD": encoded_payload,
        "RECEIPT_JSON": encoded_receipt,
        "NAME": _escape(repository.get("name", "Repository")),
        "SCOPE": _escape(repository.get("scope", "filesystem")),
        "FILES": _escape(repository.get("files", 0)),
        "COMPLETENESS": "Complete inventory" if repository.get("complete") else "Partial inventory",
        "FINGERPRINT": _escape(receipt.get("fingerprint", "Not supplied")),
        "RECEIPT_STATEMENT": _escape(receipt.get("statement", "")),
        "STATIC": _static_baseline(payload),
    }
    # Substitute only original template slots; repository strings resembling a
    # slot must remain literal text, never trigger another interpolation pass.
    return _SLOTS.sub(lambda match: slots[match.group(1)], template)


__all__ = ["render_showcase"]
