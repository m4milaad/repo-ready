from __future__ import annotations

import base64
import copy
import hashlib
from html.parser import HTMLParser
from importlib import resources
import json
from pathlib import Path
import re
import zipfile

import pytest

from repo_ready.scoring import report
from repo_ready import showcase_html
from repo_ready.showcase_html import render_showcase


class Document(HTMLParser):
    """Inspect the actual HTML boundary, including raw script/style contents."""

    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self.elements: list[tuple[str, dict[str, str | None]]] = []
        self.scripts: dict[str, str] = {}
        self.styles: list[str] = []
        self.text: list[str] = []
        self.active: tuple[str, str] | None = None
        self.feed(source)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        self.elements.append((tag, attributes))
        if tag == "script":
            identifier = attributes.get("id") or "anonymous"
            assert identifier not in self.scripts, "duplicate script ID"
            self.scripts[identifier] = ""
            self.active = (tag, identifier)
        elif tag == "style":
            self.styles.append("")
            self.active = (tag, "")

    def handle_data(self, data: str) -> None:
        if self.active and self.active[0] == "script":
            self.scripts[self.active[1]] += data
        elif self.active and self.active[0] == "style":
            self.styles[-1] += data
        else:
            self.text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.active and tag == self.active[0]:
            self.active = None


def fingerprint(analysis: dict) -> str:
    canonical = json.dumps(analysis, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def seal(payload: dict) -> dict:
    payload["receipt"]["fingerprint"] = fingerprint(payload["analysis"])
    return payload


@pytest.fixture
def payload(tmp_path: Path) -> dict:
    """Use the real legacy report API rather than an invented score shape."""
    return seal({
        "schema_version": 1,
        "tool": "repo-ready",
        "tool_version": "0.1.0",
        "analysis": {
            "repository": {"name": "field-notes", "scope": "filesystem", "files": 0, "complete": True},
            "mode": "static",
            "limitations": ["Static inspection only; no repository commands were executed."],
            "baseline": {
                "report": report(tmp_path),
                "graph": {"version": 1, "evidence": [], "edges": [], "diagnostics": []},
                "repair": {
                    "score_before": 0, "score_after": 14,
                    "operations": [{"action": "create", "path": "README.md", "reason": "Document setup."}],
                    "patch": "--- /dev/null\n+++ b/README.md\n@@ -0,0 +1 @@\n+# Setup\n",
                    "manual": [{"file": "LICENSE", "message": "Choose license terms.", "related_files": ["README.md"]}],
                    "limitations": ["A proposed patch is not a verified build."],
                },
            },
            "scenarios": [],
        },
        "receipt": {
            "schema_version": 1, "algorithm": "sha256", "fingerprint": "",
            "statement": "Fingerprint of the supplied static analysis.",
        },
    })


def test_real_minimal_report_renders_without_writing_or_mutating(payload: dict, tmp_path: Path) -> None:
    original = copy.deepcopy(payload)
    before = sorted(tmp_path.rglob("*"))
    result = render_showcase(payload)
    document = Document(result)
    assert isinstance(result, str)
    assert result.startswith("<!doctype html>")
    assert payload == original
    assert sorted(tmp_path.rglob("*")) == before
    assert result == render_showcase(payload)
    assert json.loads(document.scripts["report-payload"]) == payload
    visible_text = " ".join(document.text)
    assert "Observed (static)" in visible_text
    assert "README" in visible_text  # Real legacy checks are present without JS.
    assert "# Setup" in visible_text
    assert "Projected: 14" in visible_text
    assert "field-notes" in visible_text
    assert "@@CSP@@" not in result


def test_embedded_receipt_covers_analysis_only_and_retains_scenarios(payload: dict) -> None:
    scenario = {
        "id": "remove-readme", "label": "Remove README", "description": "A supplied simulation.",
        "status": "available", "reason": None,
        "changes": [{"path": "README.md", "operation": "remove", "detail": "Remove the documentation."}],
        "report": copy.deepcopy(payload["analysis"]["baseline"]["report"]),
        "graph": {"version": 1, "evidence": [], "edges": [], "diagnostics": []},
        "score_delta": 0, "new_diagnostics": [], "resolved_diagnostics": [],
        "affected_files": ["README.md"], "repair": None,
    }
    payload["analysis"]["scenarios"].append(scenario)
    payload["analysis"]["repository"]["name"] = "café / 笔记"
    seal(payload)
    document = Document(render_showcase(payload))
    receipt = json.loads(document.scripts["report-receipt"])
    assert receipt == {**payload["receipt"], "analysis": payload["analysis"]}
    assert receipt["fingerprint"] == fingerprint(receipt["analysis"])
    assert receipt["analysis"]["scenarios"][0] == scenario
    assert "tool_version" not in receipt
    payload["receipt"]["statement"] = "A changed statement does not change the analysis fingerprint."
    assert json.loads(Document(render_showcase(payload)).scripts["report-receipt"])["fingerprint"] == receipt["fingerprint"]


def test_untrusted_values_cannot_escape_json_scripts_or_static_markup(payload: dict) -> None:
    attack = '</script><script src="https://attacker.invalid/x">evil()</script><img src=x onerror=evil()>&\u2028\u2029@@SCRIPT@@'
    analysis = payload["analysis"]
    analysis["repository"]["name"] = attack
    analysis["limitations"] = [attack]
    baseline = analysis["baseline"]
    baseline["report"]["checks"][0]["message"] = attack
    baseline["repair"]["patch"] = attack
    baseline["repair"]["manual"][0]["message"] = attack
    baseline["graph"] = {
        "version": 1,
        "evidence": [{"id": attack, "kind": "document", "subject": attack, "file": attack, "line": 1,
                      "value": attack, "normalized": attack, "attributes": {attack: attack}}],
        "edges": [{"id": "edge-1", "relation": attack, "source": attack, "target": None,
                   "status": "missing", "expected": attack, "actual": None, "detail": attack}],
        "diagnostics": [{"code": attack, "severity": "warning", "message": attack,
                         "evidence": [attack], "related_checks": [attack]}],
    }
    payload["receipt"]["statement"] = attack
    seal(payload)
    document = Document(render_showcase(payload))
    assert set(document.scripts) == {"report-payload", "report-receipt", "report-controller"}
    assert not any(tag == "img" for tag, _attrs in document.elements)
    assert not any(key.startswith("on") for _tag, attrs in document.elements for key in attrs)
    for identifier in ("report-payload", "report-receipt"):
        raw_json = document.scripts[identifier]
        assert not any(character in raw_json for character in "<>&\u2028\u2029")
        assert "\\u003c/script\\u003e" in raw_json
        assert "\\u0026\\u2028\\u2029" in raw_json
    assert json.loads(document.scripts["report-payload"]) == payload
    assert json.loads(document.scripts["report-receipt"]) == {**payload["receipt"], "analysis": analysis}
    assert attack in "".join(document.text)  # It is text, including template-like strings.


def test_strict_csp_hashes_match_every_inline_block(payload: dict) -> None:
    document = Document(render_showcase(payload))
    policies = [attrs["content"] for tag, attrs in document.elements
                if tag == "meta" and attrs.get("http-equiv") == "Content-Security-Policy"]
    assert len(policies) == 1
    policy = policies[0]
    assert policy is not None
    directives = {parts[0]: parts[1:] for directive in policy.split(";") if (parts := directive.split())}
    for directive in ("default-src", "connect-src", "font-src", "base-uri", "form-action", "object-src",
                      "frame-src", "worker-src", "script-src-attr", "style-src-attr"):
        assert directives[directive] == ["'none'"]
    assert directives["img-src"] == ["data:"]
    assert "'unsafe-inline'" not in policy and "'unsafe-eval'" not in policy
    assert "https:" not in policy and "http:" not in policy and "*" not in policy

    def source_hash(source: str) -> str:
        digest = base64.b64encode(hashlib.sha256(source.encode("utf-8")).digest()).decode("ascii")
        return f"'sha256-{digest}'"

    assert set(directives["script-src"]) == {source_hash(source) for source in document.scripts.values()}
    assert directives["style-src"] == [source_hash(document.styles[0])]
    assert len(document.styles) == 1
    tags = [tag for tag, _attrs in document.elements]
    assert tags.index("meta") < tags.index("style") < tags.index("script")


def test_document_has_no_remote_dependencies_or_unsafe_dom_sinks(payload: dict) -> None:
    document = Document(render_showcase(payload))
    for tag, attrs in document.elements:
        assert tag not in {"iframe", "object", "embed", "base", "form", "link"}
        assert "src" not in attrs and "srcset" not in attrs and "style" not in attrs
        if "href" in attrs:
            assert attrs["href"].startswith("#")
        assert not any(key.startswith("on") for key in attrs)
    script = document.scripts["report-controller"]
    assert not re.search(r"\b(?:innerHTML|outerHTML|insertAdjacentHTML|fetch|XMLHttpRequest|WebSocket|EventSource|localStorage|sessionStorage|eval)\b", script)
    assert not re.search(r"\b(?:setTimeout|setInterval|Date|Math\.random)\b", script)
    assert not re.search(r"document\.write|window\.open|\.click\s*\(", script)
    assert not re.search(r"@import|url\s*\(", document.styles[0])
    downloads = {attrs.get("id"): attrs for tag, attrs in document.elements if tag == "a" and "download" in attrs}
    assert set(downloads) == {"download-patch", "download-receipt"}
    assert all("href" not in attrs for attrs in downloads.values())  # Prepared as Blob links in the browser.


@pytest.mark.parametrize("missing", ["report.html", "report.css", "report.js"])
def test_missing_packaged_asset_has_clear_failure(payload: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    source = resources.files("repo_ready").joinpath("showcase_assets")
    target = tmp_path / "showcase_assets"
    target.mkdir()
    for name in ("report.html", "report.css", "report.js"):
        if name != missing:
            (target / name).write_text(source.joinpath(name).read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(showcase_html.resources, "files", lambda package: tmp_path)
    with pytest.raises(RuntimeError, match=re.escape(missing)) as error:
        render_showcase(payload)
    assert isinstance(error.value.__cause__, FileNotFoundError)


def test_assets_are_read_via_resource_traversables_including_zip(payload: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    expected = render_showcase(payload)
    source = resources.files("repo_ready").joinpath("showcase_assets")
    with zipfile.ZipFile(tmp_path / "presentation.zip", "w") as archive:
        for name in ("report.html", "report.css", "report.js"):
            archive.writestr("repo_ready/showcase_assets/" + name, source.joinpath(name).read_text(encoding="utf-8"))
        calls = []

        def packaged_files(package: str) -> zipfile.Path:
            calls.append(package)
            return zipfile.Path(archive, "repo_ready/")

        monkeypatch.setattr(showcase_html.resources, "files", packaged_files)
        assert render_showcase(payload) == expected
        assert calls == ["repo_ready", "repo_ready", "repo_ready"]


def test_empty_baseline_no_repair_and_unavailable_scenario_are_readable_without_js(payload: dict) -> None:
    baseline = payload["analysis"]["baseline"]
    baseline["report"]["checks"] = []
    baseline["repair"] = None
    payload["analysis"]["repository"].update(scope="tracked", complete=False)
    payload["analysis"]["scenarios"] = [{
        "id": "missing-docker", "label": "Remove Dockerfile", "description": "Remove container setup.",
        "status": "unavailable", "reason": "No Dockerfile was observed.", "changes": [],
        "report": None, "graph": None, "score_delta": None, "new_diagnostics": [], "resolved_diagnostics": [],
        "affected_files": [], "repair": None,
    }]
    document = Document(render_showcase(seal(payload)))
    visible = " ".join(document.text)
    for explanation in ("No readiness checks", "No file evidence", "No relationships", "No repair plan",
                        "No Dockerfile was observed.", "unavailable", "Simulated", "Partial inventory", "tracked scope"):
        assert explanation in visible
    elements = {attrs["id"]: attrs for _tag, attrs in document.elements if "id" in attrs}
    assert "hidden" not in elements["static-report"]
    assert "hidden" in elements["interactive-report"]
    assert "disabled" in elements["chain-only"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_json_numbers_are_rejected(payload: dict, value: float) -> None:
    payload["analysis"]["baseline"]["report"]["score"] = value
    with pytest.raises(ValueError, match="JSON"):
        render_showcase(payload)


def test_invalid_envelopes_fail_explicitly(payload: dict) -> None:
    with pytest.raises(TypeError, match="dict"):
        render_showcase([])
    with pytest.raises(ValueError, match="schema_version"):
        render_showcase({})
    broken_baseline = copy.deepcopy(payload)
    broken_baseline["analysis"]["baseline"] = {}
    with pytest.raises(ValueError, match="report and graph"):
        render_showcase(broken_baseline)
    payload["analysis"]["mode"] = "execution"
    with pytest.raises(ValueError, match="static analysis"):
        render_showcase(payload)
