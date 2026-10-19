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
  const lanes = ["Documentation", "Build & runtime", "Automation", "Source & other"];
  const repairCategoryOrder = ["license", "runtime", "container", "lockfile", "test", "machine-specific", "score-protection", "other"];
  const repairCategoryLabels = {
    license: "License decision",
    runtime: "Runtime choice",
    container: "Container setup",
    lockfile: "Lockfiles",
    test: "Tests and verification",
    "machine-specific": "Machine-specific paths",
    "score-protection": "Score protection",
    other: "Other review",
  };

  const svgNode = (tag, attributes = {}, value) => {
    const element = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attributes).forEach(([name, attribute]) => element.setAttribute(name, text(attribute)));
    if (value !== undefined) element.textContent = text(value);
    return element;
  };

  const shorten = (value, limit) => {
    const source = text(value);
    return source.length > limit ? source.slice(0, Math.max(0, limit - 1)) + "…" : source;
  };

  const laneFor = (path) => {
    const lower = text(path).toLowerCase();
    if (lower.startsWith(".github/") || /(^|\/)(ci|workflows)\//.test(lower)) return 2;
    if (/(readme|contributing|license|^docs\/)/.test(lower)) return 0;
    if (/(dockerfile|makefile|lock|package\.json|pyproject|requirements|version|\.nvmrc|\.env)/.test(lower)) return 1;
    return 3;
  };

  const repairCategory = (item) => {
    const supplied = text(item.category || "").trim().toLowerCase();
    if (repairCategoryOrder.includes(supplied)) return supplied;
    const haystack = (text(item.file) + " " + text(item.message) + " " + text(item.reason)).toLowerCase();
    if (/license|copyright/.test(haystack)) return "license";
    if (/runtime|python|node|version|pin/.test(haystack)) return "runtime";
    if (/docker|container/.test(haystack)) return "container";
    if (/lockfile|lock file|lockfiles/.test(haystack)) return "lockfile";
    if (/test|verification/.test(haystack)) return "test";
    if (/machine|absolute path|machine-specific/.test(haystack)) return "machine-specific";
    if (/score|lower the score|regress/.test(haystack)) return "score-protection";
    return "other";
  };

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
    if (!repair) {
      byId("repair-projection").textContent = "No repair plan was included in this view.";
      fillList(byId("repair-operations"), [], "No safe create operations were proposed.");
      fillList(byId("repair-existing"), [], "No existing files were carried forward in this view.");
    } else {
      const delta = repair.score_delta == null
        ? Number(repair.score_after) - Number(repair.score_before)
        : repair.score_delta;
      const numericDelta = Number(delta);
      const deltaText = Number.isFinite(numericDelta) ? (numericDelta > 0 ? "+" : "") + text(numericDelta) : "not supplied";
      byId("repair-projection").textContent = "Before: " + text(repair.score_before)
        + " / Projected: " + text(repair.score_after) + " / Projected delta: " + deltaText;
      const operations = repair.operations || [];
      fillList(byId("repair-operations"), operations.filter((item) => item.action === "create").map((item) =>
        text(item.path) + " — " + text(item.reason)), "No safe create operations were proposed.");
      const existing = repair.existing || operations.filter((item) => item.action === "skip-existing");
      fillList(byId("repair-existing"), existing.map((item) =>
        text(item.path) + " — " + text(item.reason)), "No existing files were carried forward in this view.");
    }
    const patch = repair && repair.patch;
    byId("repair-patch").textContent = patch || "";
    byId("repair-patch").hidden = !patch;
    byId("patch-empty").hidden = Boolean(patch);
    byId("patch-empty").textContent = "No downloadable patch was proposed for this view.";
    download("download-patch", patch, "text/plain;charset=utf-8");
    const manual = repair ? (repair.manual || []) : [];
    const manualContainer = byId("manual-notes");
    manualContainer.replaceChildren();
    if (!manual.length) {
      manualContainer.append(node("p", "No remaining manual blockers were included.", "empty"));
    } else {
      const grouped = new Map();
      manual.forEach((item) => {
        const category = repairCategory(item);
        if (!grouped.has(category)) grouped.set(category, []);
        grouped.get(category).push(item);
      });
      repairCategoryOrder.filter((category) => grouped.has(category)).forEach((category) => {
        const group = node("section", undefined, "manual-group");
        group.setAttribute("aria-labelledby", "manual-group-title-" + category);
        const heading = node("h4", repairCategoryLabels[category]);
        heading.id = "manual-group-title-" + category;
        const notes = node("ul", undefined, "plain-list");
        grouped.get(category).slice().sort((left, right) =>
          compare(text(left.file) + text(left.message), text(right.file) + text(right.message))).forEach((item) => {
            notes.append(node("li", text(item.file) + " — " + text(item.message)
              + " / Related: " + ((item.related_files || []).join(", ") || "none")));
          });
        group.append(heading, notes);
        manualContainer.append(group);
      });
    }
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

  function renderEvidenceMap(facts, edges) {
    const container = byId("evidence-map");
    container.replaceChildren();
    if (!facts.length) {
      container.append(node("p", "No evidence records were included; there is no visual map to trace.", "empty"));
      return;
    }

    const laneWidth = 220;
    const laneGap = 20;
    const margin = 24;
    const headerHeight = 38;
    const nodeHeight = 52;
    const rowGap = 14;
    const width = margin * 2 + lanes.length * laneWidth + (lanes.length - 1) * laneGap;
    const groups = lanes.map((_label, laneIndex) => facts.filter((item) => laneFor(item.file) === laneIndex)
      .slice().sort((left, right) => compare(text(left.file) + "\u0000" + text(left.id), text(right.file) + "\u0000" + text(right.id))));
    const rows = Math.max(1, ...groups.map((items) => items.length));
    const height = margin + headerHeight + rows * nodeHeight + Math.max(0, rows - 1) * rowGap + margin;
    const svg = svgNode("svg", {
      viewBox: "0 0 " + width + " " + height,
      role: "group",
      "aria-labelledby": "evidence-map-svg-title evidence-map-svg-description",
    });
    svg.append(
      svgNode("title", {id: "evidence-map-svg-title"}, "Visual evidence map"),
      svgNode("desc", {id: "evidence-map-svg-description"},
        text(facts.length) + " evidence records and " + text(edges.length)
        + " precomputed relationships. Selected chains are highlighted in blue.")
    );
    const coordinates = new Map();
    groups.forEach((items, laneIndex) => {
      const x = margin + laneIndex * (laneWidth + laneGap);
      const label = svgNode("text", {x: x, y: margin + 14, class: "map-lane-label"}, lanes[laneIndex]);
      const rule = svgNode("line", {x1: x, y1: margin + 24, x2: x + laneWidth, y2: margin + 24, class: "map-lane-rule"});
      svg.append(label, rule);
      items.forEach((item, itemIndex) => {
        coordinates.set(item.id, {
          x,
          y: margin + headerHeight + itemIndex * (nodeHeight + rowGap),
          width: laneWidth,
          height: nodeHeight,
        });
      });
    });

    const edgeLayer = svgNode("g", {"aria-hidden": "true"});
    const unresolved = [];
    edges.forEach((edge) => {
      const source = coordinates.get(edge.source);
      if (!source) return;
      const target = edge.target == null ? null : coordinates.get(edge.target);
      const startX = source.x + source.width;
      const startY = source.y + source.height / 2;
      const endX = target ? target.x : width - margin;
      const endY = target ? target.y + target.height / 2 : startY;
      const bend = Math.max(18, Math.abs(endX - startX) / 2);
      const path = svgNode("path", {
        d: "M " + startX + " " + startY + " C " + (startX + bend) + " " + startY + ", "
          + (endX - bend) + " " + endY + ", " + endX + " " + endY,
        class: "map-edge map-edge-" + text(edge.status || "unknown")
          + (state.selected.has(edge.source) || (edge.target && state.selected.has(edge.target)) ? " is-related" : ""),
      });
      edgeLayer.append(path);
      if (!target) unresolved.push({x: endX, y: endY, related: state.selected.has(edge.source)});
    });
    svg.append(edgeLayer);
    unresolved.forEach((point) => {
      svg.append(
        svgNode("circle", {cx: point.x, cy: point.y, r: 5, class: "map-unresolved"}),
        svgNode("text", {x: point.x - 4, y: point.y - 9, class: "map-unresolved-label"}, "?")
      );
    });

    groups.forEach((items) => items.forEach((item) => {
      const point = coordinates.get(item.id);
      const related = state.selected.has(item.id);
      const group = svgNode("g", {
        class: "map-node" + (related ? " is-related" : ""),
        role: "button",
        tabindex: "0",
        "aria-label": location(item) + ", " + text(item.kind) + ", " + text(item.subject),
        "aria-pressed": String(related),
      });
      group.append(
        svgNode("title", {}, location(item) + " / " + text(item.kind) + " / " + text(item.subject)),
        svgNode("rect", {x: point.x, y: point.y, width: point.width, height: point.height, rx: 3}),
        svgNode("text", {x: point.x + 10, y: point.y + 20}, shorten(location(item), 31)),
        svgNode("text", {x: point.x + 10, y: point.y + 38, class: "map-node-meta"}, shorten(text(item.kind) + " / " + text(item.subject), 31))
      );
      const activate = () => selectEvidence([item.id], {record: item.id});
      group.addEventListener("click", activate);
      group.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          activate();
        }
      });
      svg.append(group);
    }));
    container.append(svg);
  }

  function renderGraph() {
    const facts = evidence();
    const diagnostics = byId("diagnostic-list");
    diagnostics.replaceChildren();
    (graph().diagnostics || []).forEach((item, index) => {
      const button = node("button", undefined, "diagnostic-button");
      button.id = "diagnostic-" + index;
      button.type = "button";
      button.setAttribute("aria-controls", "file-schematic evidence-map evidence-rows");
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
    const files = new Map();
    facts.forEach((item) => {
      if (!files.has(item.file)) files.set(item.file, []);
      files.get(item.file).push(item);
    });
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

    renderEvidenceMap(facts, graph().edges || []);

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
