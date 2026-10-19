/* Offline presentation only: every score, scenario, graph, and repair is supplied. */
"use strict";
(() => {
  const byId = (id) => document.getElementById(id);
  const text = (value) => value == null ? "—" : String(value);
  const node = (tag, value, className) => {
    const element = document.createElement(tag);
    if (value !== undefined) element.textContent = text(value);
    if (className) element.className = className;
    return element;
  };
  const fillList = (container, values, empty) => {
    container.replaceChildren();
    (values.length ? values : [empty]).forEach((value) => container.append(node("li", value)));
  };
  const payload = JSON.parse(byId("report-payload").textContent);
  const analysis = payload.analysis;
  const scenarios = analysis.scenarios || [];
  const state = {
    scenario: null, selected: new Set(), record: null, diagnostic: null,
    file: null, query: "", kind: "all", chainOnly: false, page: 0
  };
  const pageSize = 40;
  const current = () => state.scenario || analysis.baseline;
  const graph = () => current().graph || {evidence: [], edges: [], diagnostics: []};
  const evidence = () => graph().evidence || [];
  const compare = (left, right) => left < right ? -1 : left > right ? 1 : 0;
  const location = (item) => text(item.file) + (item.line == null ? "" : ":" + text(item.line));
  const downloads = new Map();

  function download(id, content, type) {
    const link = byId(id);
    const previous = downloads.get(id);
    link.hidden = true;
    link.removeAttribute("href");
    if (previous) URL.revokeObjectURL(previous);
    downloads.delete(id);
    if (content == null || content === "") return;
    try {
      const url = URL.createObjectURL(new Blob([content], {type}));
      downloads.set(id, url);
      link.href = url;
      link.hidden = false;
    } catch (_error) {
      byId("download-status").textContent = "This browser could not prepare downloads. The report and patch text remain readable here.";
    }
  }

  function renderOverview() {
    const report = current().report || {};
    byId("overview-title").textContent = state.scenario ? "Simulated" : "Observed (static)";
    byId("score-value").textContent = text(report.score);
    byId("score-max").textContent = text(report.max == null ? 100 : report.max);
    byId("score-grade").textContent = text(report.grade || "No grade supplied");
    byId("view-description").textContent = state.scenario
      ? text(state.scenario.label) + ": " + text(state.scenario.description)
      : "The original static observation. Scenarios change this view only.";
    const delta = state.scenario && state.scenario.score_delta;
    byId("score-delta").textContent = state.scenario
      ? "Supplied score delta: " + (delta == null ? "not supplied" : (delta > 0 ? "+" : "") + text(delta))
      : "Baseline / original observation";
  }

  function renderChecks() {
    const container = byId("check-list");
    container.replaceChildren();
    const checks = (current().report || {}).checks || [];
    if (!checks.length) container.append(node("p", "No readiness checks were included in this view.", "empty"));
    checks.forEach((check) => {
      const item = node("details", undefined, "check-item");
      const summary = node("summary");
      summary.append(node("span", text(check.id) + " / " + text(check.title)),
        node("span", text(check.status) + " · " + text(check.earned) + " / " + text(check.weight), "mono"));
      item.append(summary, node("p", check.message));
      const findings = node("ul", undefined, "plain-list");
      fillList(findings, (check.findings || []).map((finding) => location(finding) + " — " + text(finding.detail)),
        "No file-level findings for this check.");
      item.append(findings);
      if (check.fix_hint) item.append(node("p", "Repair hint: " + text(check.fix_hint)));
      container.append(item);
    });
  }

  function buildScenarios() {
    const container = byId("scenario-options");
    const original = node("button", "Observed (static) / original", "scenario-button");
    original.type = "button";
    original.addEventListener("click", () => selectView(null));
    container.append(original);
    scenarios.forEach((scenario, index) => {
      const entry = node("div", undefined, "scenario-entry");
      const button = node("button", undefined, "scenario-button");
      button.type = "button";
      button.append(node("span", scenario.label), node("small", "Simulated"));
      button.disabled = scenario.status !== "available" || !scenario.report || !scenario.graph;
      const description = node("p", button.disabled
        ? "Unavailable: " + text(scenario.reason || "No precomputed result was supplied.")
        : scenario.description, "caption");
      description.id = "scenario-description-" + index;
      button.setAttribute("aria-describedby", description.id);
      button.addEventListener("click", () => selectView(scenario));
      entry.append(button, description);
      container.append(entry);
    });
    if (!scenarios.length) container.append(node("p", "No precomputed scenarios were included.", "empty"));
  }

  function renderScenarioDetails() {
    const buttons = byId("scenario-options").querySelectorAll("button");
    buttons.forEach((button, index) => button.setAttribute("aria-pressed",
      String(index === 0 ? state.scenario === null : scenarios[index - 1] === state.scenario)));
    byId("scenario-details").hidden = !state.scenario;
    if (!state.scenario) return;
    const scenario = state.scenario;
    fillList(byId("scenario-changes"), (scenario.changes || []).map((change) =>
      text(change.operation) + " " + text(change.path) + " — " + text(change.detail)), "No file changes were supplied.");
    byId("affected-files").textContent = "Affected files: " + ((scenario.affected_files || []).join(", ") || "none supplied");
    const comparison = scenario.comparison;
    byId("scenario-comparison").textContent = comparison
      ? "Baseline comparison: " + text((comparison.changes || []).length) + " semantic change(s) / "
        + (comparison.regressed ? "regression detected" : "no regression detected") + "."
      : "No baseline comparison was supplied for this scenario.";
    const describe = (item) => text(item.code) + " · " + text(item.severity) + " — " + text(item.message);
    fillList(byId("new-diagnostics"), (scenario.new_diagnostics || []).map(describe), "No new diagnostics.");
    fillList(byId("resolved-diagnostics"), (scenario.resolved_diagnostics || []).map(describe), "No resolved diagnostics.");
  }

  function renderRepair() {
    const repair = current().repair;
    byId("repair-projection").textContent = repair
      ? "Before: " + text(repair.score_before) + " / Projected: " + text(repair.score_after)
      : "No repair plan was included in this view.";
    fillList(byId("repair-operations"), repair ? (repair.operations || []).map((item) =>
      text(item.action) + " " + text(item.path) + " — " + text(item.reason)) : [], "No automatic repair operations were proposed.");
    const patch = repair && repair.patch;
    byId("repair-patch").textContent = patch || "";
    byId("repair-patch").hidden = !patch;
    byId("patch-empty").hidden = Boolean(patch);
    byId("patch-empty").textContent = "No downloadable patch was proposed for this view.";
    download("download-patch", patch, "text/plain;charset=utf-8");
    fillList(byId("manual-notes"), repair ? (repair.manual || []).map((item) =>
      text(item.file) + " — " + text(item.message) + " / Related: " + ((item.related_files || []).join(", ") || "none")) : [],
    "No manual repair notes were included.");
    fillList(byId("repair-limits"), repair ? repair.limitations || [] : [], "No additional repair limitations were supplied.");
  }

  function selectView(scenario) {
    if (scenario && (scenario.status !== "available" || !scenario.report || !scenario.graph)) return;
    state.scenario = scenario;
    resetSelection();
    state.query = "";
    state.kind = "all";
    state.page = 0;
    byId("evidence-search").value = "";
    byId("evidence-kind").value = "all";
    renderOverview();
    renderScenarioDetails();
    renderChecks();
    renderRepair();
    renderGraph();
    renderEvidence();
    byId("view-status").textContent = "Showing " + (scenario ? "Simulated: " + text(scenario.label) : "Observed (static)")
      + ". Score " + text((current().report || {}).score) + ".";
  }

  function resetSelection() {
    state.selected = new Set();
    state.record = null;
    state.diagnostic = null;
    state.file = null;
    state.chainOnly = false;
    byId("chain-only").checked = false;
  }

  function trace(ids) {
    const known = new Set(evidence().map((item) => item.id));
    const selected = new Set(ids.filter((id) => known.has(id)));
    const adjacency = new Map();
    (graph().edges || []).forEach((edge) => {
      if (!known.has(edge.source) || !known.has(edge.target)) return;
      if (!adjacency.has(edge.source)) adjacency.set(edge.source, []);
      if (!adjacency.has(edge.target)) adjacency.set(edge.target, []);
      adjacency.get(edge.source).push(edge.target);
      adjacency.get(edge.target).push(edge.source);
    });
    const queue = Array.from(selected);
    for (let index = 0; index < queue.length; index += 1) {
      (adjacency.get(queue[index]) || []).forEach((id) => {
        if (!selected.has(id)) { selected.add(id); queue.push(id); }
      });
    }
    state.selected = selected;
  }

  function selectEvidence(ids, options = {}) {
    const focusId = document.activeElement && document.activeElement.id;
    state.diagnostic = options.diagnostic == null ? null : options.diagnostic;
    state.file = options.file == null ? null : options.file;
    state.record = options.record == null ? null : options.record;
    trace(ids);
    if (!state.selected.size) {
      state.chainOnly = false;
      byId("chain-only").checked = false;
    }
    if (options.record == null) state.page = 0;
    renderGraph();
    renderEvidence();
    const focused = focusId && byId(focusId);
    if (focused) focused.focus({preventScroll: true});
  }

  function renderGraph() {
    const facts = evidence();
    const diagnostics = byId("diagnostic-list");
    diagnostics.replaceChildren();
    (graph().diagnostics || []).forEach((item, index) => {
      const button = node("button", undefined, "diagnostic-button");
      button.id = "diagnostic-" + index;
      button.type = "button";
      button.setAttribute("aria-controls", "file-schematic evidence-rows");
      button.setAttribute("aria-pressed", String(state.diagnostic === index));
      button.append(node("span", text(item.code) + " / " + text(item.severity), "mono"), node("span", item.message),
        node("small", "Related checks: " + ((item.related_checks || []).join(", ") || "none supplied")));
      button.addEventListener("click", () => selectEvidence(item.evidence || [], {diagnostic: index}));
      diagnostics.append(button);
    });
    if (!diagnostics.children.length) diagnostics.append(node("p",
      "No contract diagnostics were included. This is not proof of runtime correctness.", "empty"));

    const selectedCount = state.selected.size;
    byId("clear-selection").disabled = !selectedCount && state.diagnostic === null && state.file === null;
    byId("chain-only").disabled = !selectedCount;
    byId("selection-status").textContent = selectedCount
      ? text(selectedCount) + " evidence records in the selected chain. Related files, rows, and relationships are marked."
      : state.diagnostic !== null ? "This diagnostic has no matching evidence records in the supplied graph."
      : "No evidence chain selected. Select a diagnostic, file, or record to trace relationships.";

    const schematic = byId("file-schematic");
    schematic.replaceChildren();
    const lanes = ["Documentation", "Build & runtime", "Automation", "Source & other"];
    const files = new Map();
    facts.forEach((item) => {
      if (!files.has(item.file)) files.set(item.file, []);
      files.get(item.file).push(item);
    });
    const laneFor = (path) => {
      const lower = text(path).toLowerCase();
      if (lower.startsWith(".github/") || /(^|\/)(ci|workflows)\//.test(lower)) return 2;
      if (/(readme|contributing|license|^docs\/)/.test(lower)) return 0;
      if (/(dockerfile|makefile|lock|package\.json|pyproject|requirements|version|\.nvmrc|\.env)/.test(lower)) return 1;
      return 3;
    };
    lanes.forEach((label, laneIndex) => {
      const entries = Array.from(files).filter(([path]) => laneFor(path) === laneIndex).sort(([a], [b]) => compare(a, b));
      if (!entries.length) return;
      const lane = node("section", undefined, "file-lane");
      lane.append(node("h4", label));
      entries.forEach(([path, items], fileIndex) => {
        const button = node("button", undefined, "file-button");
        button.id = "schematic-file-" + laneIndex + "-" + fileIndex;
        button.type = "button";
        const related = items.some((item) => state.selected.has(item.id));
        button.setAttribute("aria-pressed", String(state.file === path));
        button.classList.toggle("is-related", related);
        button.append(node("span", path, "mono"), node("small", text(items.length) + " facts" + (related ? " / related" : "")));
        button.addEventListener("click", () => selectEvidence(items.map((item) => item.id), {file: path}));
        lane.append(button);
      });
      schematic.append(lane);
    });
    if (!facts.length) schematic.append(node("p", "No file evidence was included; there is no schematic to trace.", "empty"));

    const relationships = byId("relationship-list");
    relationships.replaceChildren();
    const lookup = new Map(facts.map((item) => [item.id, item]));
    (graph().edges || []).forEach((edge) => {
      const item = node("li", undefined, "relationship");
      const related = state.selected.has(edge.source) || state.selected.has(edge.target);
      item.classList.toggle("is-related", related);
      const endpoint = (id) => lookup.has(id) ? location(lookup.get(id)) : text(id || "unresolved target");
      item.append(node("p", endpoint(edge.source) + " → " + endpoint(edge.target), "mono"),
        node("p", text(edge.relation) + " / " + text(edge.status) + (related ? " / related" : "")), node("p", edge.detail),
        node("small", "Expected: " + text(edge.expected) + " / Actual: " + text(edge.actual)));
      relationships.append(item);
    });
    if (!relationships.children.length) relationships.append(node("li", "No relationships were included in this view.", "empty"));
  }

  function renderEvidence() {
    const facts = evidence();
    const query = state.query.trim().toLowerCase();
    const filtered = facts.filter((item) => (state.kind === "all" || item.kind === state.kind)
      && (!state.chainOnly || state.selected.has(item.id))
      && (!query || JSON.stringify(item).toLowerCase().includes(query)));
    const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
    state.page = Math.min(state.page, pages - 1);
    const first = state.page * pageSize;
    const rows = byId("evidence-rows");
    rows.replaceChildren();
    filtered.slice(first, first + pageSize).forEach((item) => {
      const row = node("tr");
      const related = state.selected.has(item.id);
      row.classList.toggle("is-related", related);
      const file = node("th");
      file.scope = "row";
      file.append(node("code", location(item)));
      const kind = node("td");
      kind.append(node("span", item.kind, "mono"), node("p", item.subject));
      const value = node("td");
      value.append(node("pre", item.value, "evidence-value"));
      if (related) value.append(node("small", "Related to selection"));
      const action = node("td");
      const button = node("button", undefined, "inspect-button");
      button.id = "inspect-record-" + facts.indexOf(item);
      button.type = "button";
      button.setAttribute("aria-pressed", String(state.record === item.id));
      button.setAttribute("aria-controls", "evidence-detail");
      button.setAttribute("aria-expanded", String(state.record === item.id));
      button.append(node("span", "Inspect"), node("small", item.id));
      button.addEventListener("click", () => selectEvidence([item.id], {record: item.id}));
      action.append(button);
      row.append(file, kind, value, action);
      rows.append(row);
    });
    byId("filter-status").textContent = text(filtered.length) + " of " + text(facts.length) + " evidence records"
      + (state.chainOnly ? " / selected chain" : "") + ".";
    byId("evidence-table-region").hidden = !filtered.length;
    byId("evidence-pagination").hidden = filtered.length <= pageSize;
    byId("page-status").textContent = "Page " + text(state.page + 1) + " of " + text(pages);
    byId("previous-page").disabled = state.page === 0;
    byId("next-page").disabled = state.page >= pages - 1;
    byId("evidence-empty").hidden = Boolean(filtered.length);
    byId("evidence-empty").textContent = facts.length
      ? "No evidence matches" + (state.query ? " “" + state.query + "”" : " these filters") + ". Use Clear filters to show all records."
      : "No evidence records were included in this view.";
    const record = facts.find((item) => item.id === state.record);
    byId("evidence-detail").hidden = !record;
    const detail = byId("detail-fields");
    detail.replaceChildren();
    if (record) {
      [["ID", record.id], ["File / line", location(record)], ["Kind", record.kind], ["Subject", record.subject],
        ["Value", record.value], ["Normalized", record.normalized], ["Attributes", JSON.stringify(record.attributes || {}, null, 2)]]
        .forEach(([label, value]) => {
          const entry = node("dd");
          entry.append(node("pre", value));
          detail.append(node("dt", label), entry);
        });
    }
  }

  byId("evidence-search").addEventListener("input", (event) => {
    state.query = event.target.value; state.page = 0; renderEvidence();
  });
  byId("evidence-kind").addEventListener("change", (event) => {
    state.kind = event.target.value; state.page = 0; renderEvidence();
  });
  byId("chain-only").addEventListener("change", (event) => {
    state.chainOnly = event.target.checked; state.page = 0; renderEvidence();
  });
  byId("clear-filters").addEventListener("click", () => {
    state.query = ""; state.kind = "all"; state.chainOnly = false; state.page = 0;
    byId("evidence-search").value = ""; byId("evidence-kind").value = "all"; byId("chain-only").checked = false;
    renderEvidence(); byId("evidence-search").focus();
  });
  byId("clear-selection").addEventListener("click", () => {
    resetSelection(); state.page = 0; renderGraph(); renderEvidence();
    const next = byId("diagnostic-list").querySelector("button") || byId("file-schematic").querySelector("button") || byId("evidence-search");
    next.focus({preventScroll: true});
  });
  byId("reset-view").addEventListener("click", () => selectView(null));
  byId("previous-page").addEventListener("click", () => {
    state.page -= 1; renderEvidence(); byId("evidence-table-region").focus({preventScroll: true});
  });
  byId("next-page").addEventListener("click", () => {
    state.page += 1; renderEvidence(); byId("evidence-table-region").focus({preventScroll: true});
  });
  window.addEventListener("pagehide", () => {
    downloads.forEach((url) => URL.revokeObjectURL(url)); downloads.clear();
  });
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) {
      renderRepair();
      download("download-receipt", byId("report-receipt").textContent, "application/json;charset=utf-8");
    }
  });
  fillList(byId("analysis-limits"), analysis.limitations || [], "No additional analysis limitations were supplied.");
  buildScenarios();
  selectView(null);
  download("download-receipt", byId("report-receipt").textContent, "application/json;charset=utf-8");
  byId("interactive-report").hidden = false;
  byId("static-report").hidden = true;
})();
