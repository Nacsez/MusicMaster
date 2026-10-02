"use strict";

(() => {
  const runtime = window.__MMT_CONFIG__;
  if (
    !runtime ||
    typeof runtime.token !== "string" ||
    typeof runtime.origin !== "string"
  ) {
    throw new Error("The private portal bootstrap configuration is missing.");
  }

  const state = {
    bootstrap: null,
    defaults: null,
    capabilities: null,
    diagnostics: null,
    preferences: null,
    tracks: [],
    masterLibrary: { sources: [], summary: {} },
    catalogRows: [],
    referenceSets: [],
    runs: [],
    logs: [],
    jobTargets: [],
    jobReferences: [],
    audition: {
      active: "a",
      a: null,
      b: null,
      preview: null,
      loadSequence: 0,
    },
    selectedTrackId: null,
    selectedMasterVersionId: null,
    selectedMasterExports: new Map(),
    preferredMasterBySource: new Map(),
    preferredDeliverableByVersion: new Map(),
    automaticComparisonSourceId: null,
    selectedSetId: null,
    selectedRunId: null,
    selectedRun: null,
    runArtifacts: [],
    runManifest: null,
    runManifestError: null,
    selectedLog: null,
    currentTask: null,
    events: [],
    hiddenEventKeys: new Set(),
    selectedEventKey: null,
    lastTerminalTaskId: null,
    jobTaskId: null,
    jobTaskTargets: [],
    jobRevision: 0,
    jobDirty: false,
    validating: false,
    submitting: false,
    polling: false,
    stopped: false,
  };

  const POLL_INTERVAL_MS = 850;
  const browserSession = {
    documentId: null,
    enabled: false,
    active: false,
    generation: 0,
    controller: null,
    heartbeatIntervalMs: 15000,
  };
  const MAX_REFERENCES_FALLBACK = 32;
  const MAX_TARGETS_FALLBACK = 32;
  const PRIMARY_VIEW_FOR_PANEL = {
    dashboard: "master-job",
    "master-job": "master-job",
    catalog: "catalog",
    "reference-sets": "master-job",
    runs: "runs",
    events: "runs",
    diagnostics: "runs",
  };

  const $ = (id) => {
    const value = document.getElementById(id);
    if (!value) {
      throw new Error(`Required portal element is missing: ${id}`);
    }
    return value;
  };

  const all = (selector, root = document) =>
    Array.from(root.querySelectorAll(selector));

  async function browserSessionRequest(path, body, options = {}) {
    const response = await fetch(path, {
      method: "POST",
      headers: { "X-MMT-Token": runtime.token, "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
      ...options,
    });
    if (!response.ok) {
      throw new Error(`Browser session request failed (${response.status}).`);
    }
    const payload = await response.json();
    if (!payload.ok) {
      throw new Error("Browser session request was refused.");
    }
    return payload.data;
  }

  async function maintainBrowserSession(generation) {
    const documentId = browserSession.documentId;
    const current = () => browserSession.active &&
      browserSession.generation === generation && !state.stopped;
    let registered = false;
    while (current()) {
      const controller = new AbortController();
      browserSession.controller = controller;
      try {
        if (!registered) {
          const opened = await browserSessionRequest("/api/browser-session", {
            document_id: documentId,
            event: "open",
          }, { signal: controller.signal });
          if (!current()) return;
          if (!opened.enabled) {
            browserSession.enabled = false;
            browserSession.active = false;
            return;
          }
          if (!opened.registered) {
            restartBrowserSession();
            return;
          }
          registered = opened.registered;
          console.debug("[MMT browser-session] Document registered.");
        }
        // The server holds this request as presence. Immediately await the next
        // poll through promises, so hidden-tab timer throttling cannot expire a
        // healthy document merely because the browser is minimized.
        const presence = await browserSessionRequest("/api/browser-session/wait", {
          document_id: documentId,
        }, { signal: controller.signal });
        if (!current()) return;
        if (!presence.enabled) {
          browserSession.enabled = false;
          browserSession.active = false;
          return;
        }
        if (!presence.registered) {
          restartBrowserSession();
          return;
        }
        registered = presence.registered;
      } catch (error) {
        if (!current() || error.name === "AbortError") return;
        console.warn("[MMT browser-session] Presence transport interrupted; retrying.", error);
        try {
          const heartbeat = await browserSessionRequest("/api/browser-session", {
            document_id: documentId,
            event: "heartbeat",
          }, { signal: controller.signal });
          if (!current()) return;
          if (!heartbeat.enabled) {
            browserSession.enabled = false;
            browserSession.active = false;
            return;
          }
          if (!heartbeat.registered) {
            restartBrowserSession();
            return;
          }
        } catch {
          // The retained server log explains connectivity or process shutdown;
          // the lease supplies crash cleanup when a browser cannot send close.
        }
        registered = false;
        await new Promise((resolve) => setTimeout(resolve, browserSession.heartbeatIntervalMs));
      }
    }
  }

  function startBrowserSession() {
    if (!browserSession.enabled || browserSession.active || state.stopped) return;
    // A BFCache pageshow represents a new visit even though its DOM is reused.
    // A fresh lease ID keeps a delayed close from the old visit from releasing
    // this restored visit. Each asynchronous loop captures its own ID above.
    browserSession.documentId = crypto.randomUUID();
    browserSession.active = true;
    const generation = ++browserSession.generation;
    maintainBrowserSession(generation).catch((error) => {
      console.warn("[MMT browser-session] Presence loop stopped unexpectedly.", error);
    });
  }

  function restartBrowserSession() {
    browserSession.active = false;
    ++browserSession.generation;
    browserSession.controller?.abort();
    browserSession.controller = null;
    startBrowserSession();
  }

  function configureBrowserSession(options) {
    browserSession.enabled = options?.enabled === true;
    if (!browserSession.enabled) return;
    const interval = Number(options.heartbeat_interval_seconds);
    if (Number.isFinite(interval)) {
      browserSession.heartbeatIntervalMs = Math.max(5000, Math.min(60000, interval * 1000));
    }
    startBrowserSession();
  }

  function closeBrowserSession() {
    if (!browserSession.active) return;
    browserSession.active = false;
    ++browserSession.generation;
    browserSession.controller?.abort();
    browserSession.controller = null;
    if (state.stopped) return;
    // pagehide runs only after navigation proceeds; a canceled beforeunload
    // prompt therefore never closes the lease. keepalive preserves the small
    // authenticated release request after the document's network context ends.
    browserSessionRequest("/api/browser-session", {
      document_id: browserSession.documentId,
      event: "close",
    }, { keepalive: true }).catch(() => {
      console.debug("[MMT browser-session] Close notification unavailable; lease cleanup remains active.");
    });
  }

  window.addEventListener("pagehide", closeBrowserSession);
  window.addEventListener("pageshow", startBrowserSession);

  function element(tag, options = {}, children = []) {
    const node = document.createElement(tag);
    if (options.className) {
      node.className = options.className;
    }
    if (options.text !== undefined && options.text !== null) {
      node.textContent = String(options.text);
    }
    if (options.title) {
      node.title = options.title;
    }
    if (options.type) {
      node.type = options.type;
    }
    if (options.value !== undefined) {
      node.value = String(options.value);
    }
    if (options.attrs) {
      for (const [name, value] of Object.entries(options.attrs)) {
        if (value !== null && value !== undefined) {
          node.setAttribute(name, String(value));
        }
      }
    }
    for (const child of children) {
      if (child !== null && child !== undefined) {
        node.append(child instanceof Node ? child : document.createTextNode(String(child)));
      }
    }
    return node;
  }

  function clear(node) {
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function appendTextCell(row, value, options = {}) {
    const cell = element("td", {
      className: options.className || "",
      text: display(value, options.fallback),
      title: options.title || "",
    });
    if (options.code) {
      const code = element("code", { text: display(value, options.fallback) });
      clear(cell);
      cell.append(code);
    }
    row.append(cell);
    return cell;
  }

  function emptyRow(body, columns, message) {
    clear(body);
    const row = element("tr");
    row.append(element("td", {
      className: "empty-cell",
      text: message,
      attrs: { colspan: columns },
    }));
    body.append(row);
  }

  function display(value, fallback = "—") {
    if (value === null || value === undefined || value === "") {
      return fallback;
    }
    return String(value);
  }

  function shortId(value, length = 18) {
    const text = display(value);
    if (text.length <= length + 4) {
      return text;
    }
    return `${text.slice(0, length)}…`;
  }

  function shortHash(value) {
    const text = display(value);
    return text === "—" ? text : shortId(text, 15);
  }

  function formatBytes(value) {
    const number = Number(value);
    if (!Number.isFinite(number) || number < 0) {
      return "—";
    }
    const units = ["B", "KiB", "MiB", "GiB", "TiB"];
    let selected = number;
    let unit = 0;
    while (selected >= 1024 && unit < units.length - 1) {
      selected /= 1024;
      unit += 1;
    }
    return `${selected.toFixed(unit === 0 ? 0 : selected >= 10 ? 1 : 2)} ${units[unit]}`;
  }

  function formatDuration(value) {
    const number = Number(value);
    if (!Number.isFinite(number) || number < 0) {
      return "—";
    }
    if (number < 60) {
      return `${number.toFixed(2)} s`;
    }
    const minutes = Math.floor(number / 60);
    const seconds = number - minutes * 60;
    return `${minutes}:${seconds.toFixed(1).padStart(4, "0")}`;
  }

  function formatDate(value) {
    if (!value) {
      return "—";
    }
    const parsed = new Date(value);
    return Number.isNaN(parsed.valueOf()) ? String(value) : parsed.toLocaleString();
  }

  function formatNanosecondDate(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) {
      return "—";
    }
    return new Date(number / 1_000_000).toLocaleString();
  }

  function formatNumber(value, digits = 4) {
    const number = Number(value);
    if (!Number.isFinite(number)) {
      return "—";
    }
    return number.toLocaleString(undefined, { maximumFractionDigits: digits });
  }

  function basename(path) {
    const text = display(path, "");
    const parts = text.split(/[\\/]/);
    return parts[parts.length - 1] || text || "Unnamed";
  }

  function safeJson(value) {
    return JSON.stringify(value, null, 2);
  }

  class PortalApiError extends Error {
    constructor(message, details = {}) {
      super(message);
      this.name = "PortalApiError";
      this.details = details;
    }
  }

  async function api(path, options = {}) {
    const headers = {
      Accept: "application/json",
      "X-MMT-Token": runtime.token,
    };
    const request = {
      method: options.method || "GET",
      headers,
      cache: "no-store",
      credentials: "same-origin",
    };
    if (options.body !== undefined) {
      headers["Content-Type"] = "application/json";
      request.body = JSON.stringify(options.body);
    }
    const response = await fetch(path, request);
    let payload;
    try {
      payload = await response.json();
    } catch (error) {
      throw new PortalApiError(
        `The local portal returned an unreadable ${response.status} response.`,
        { cause: String(error) },
      );
    }
    if (!response.ok || !payload || payload.ok !== true) {
      const problem = payload && payload.error ? payload.error : {};
      const requestSuffix = problem.request_id ? ` (request ${problem.request_id})` : "";
      throw new PortalApiError(
        `${problem.message || response.statusText || "Portal request failed"}${requestSuffix}`,
        problem,
      );
    }
    return payload.data;
  }

  function setStatus(message, tone = "neutral") {
    const host = $("global-status");
    const dot = host.querySelector(".status-dot");
    const text = host.querySelector("span:last-child");
    if (dot) {
      dot.className = `status-dot ${tone}`;
    }
    if (text) {
      text.textContent = message;
    }
  }

  function toast(message, tone = "neutral", duration = 6000) {
    const item = element("div", {
      className: `toast ${tone === "neutral" ? "" : tone}`.trim(),
      text: message,
      attrs: { role: tone === "danger" ? "alert" : "status" },
    });
    $("toast-region").append(item);
    window.setTimeout(() => item.remove(), duration);
  }

  function reportError(error, context = "Operation failed") {
    const message = error instanceof Error ? error.message : String(error);
    setStatus(`${context}: ${message}`, "danger");
    toast(`${context}: ${message}`, "danger", 10000);
    console.error(context, error);
  }

  function closeMenus() {
    all(".command-menu[open]").forEach((menu) => {
      const returnFocus = menu.contains(document.activeElement);
      menu.removeAttribute("open");
      if (returnFocus) {
        const summary = menu.querySelector("summary");
        if (summary) {
          summary.focus();
        }
      }
    });
  }

  function activateTab(name, focus = false) {
    const resolvedName = name === "dashboard" ? "master-job" : name;
    const requestedPanel = document.querySelector(`[data-panel="${resolvedName}"]`);
    const primaryName = PRIMARY_VIEW_FOR_PANEL[resolvedName] || resolvedName;
    const requested = document.querySelector(`[data-tab="${primaryName}"]`);
    if (!requested || !requestedPanel) {
      return;
    }
    const priorFocus = document.activeElement;
    const priorPanel =
      priorFocus instanceof HTMLElement && priorFocus.closest("[data-panel]");
    const focusWasInMenu = Boolean(
      priorFocus instanceof HTMLElement && priorFocus.closest(".command-menu"),
    );
    const focusWouldBeHidden = Boolean(
      (priorPanel && priorPanel !== requestedPanel) || focusWasInMenu,
    );
    all("[data-tab]").forEach((tab) => {
      const active = tab === requested;
      tab.classList.toggle("active", active);
      if (active) {
        tab.setAttribute("aria-current", "page");
        tab.setAttribute("aria-controls", requestedPanel.id);
      } else {
        tab.removeAttribute("aria-current");
        tab.removeAttribute("aria-controls");
      }
    });
    all("[data-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.panel !== resolvedName;
    });
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}#${resolvedName}`,
    );
    closeMenus();
    if (focus) {
      requested.focus();
    } else if (focusWouldBeHidden) {
      const heading = requestedPanel.querySelector("h1");
      if (heading) {
        heading.setAttribute("tabindex", "-1");
        heading.focus();
      } else {
        requested.focus();
      }
    }
  }

  function activateJobSection(name, focus = false) {
    const requested = document.querySelector(`[data-job-section="${name}"]`);
    if (!requested) {
      return;
    }
    all("[data-job-section]").forEach((step) => {
      const active = step === requested;
      step.classList.toggle("active", active);
      if (active) {
        step.setAttribute("aria-current", "step");
      } else {
        step.removeAttribute("aria-current");
      }
    });
    all("[data-job-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.jobPanel !== name;
    });
    if (focus) {
      requested.focus();
    }
  }

  function activateRunSubtab(name, focus = false) {
    const requested = document.querySelector(`[data-run-subtab="${name}"]`);
    if (!requested) {
      return;
    }
    all("[data-run-subtab]").forEach((tab) => {
      const active = tab === requested;
      tab.classList.toggle("active", active);
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
    });
    all("[data-run-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.runPanel !== name;
    });
    if (focus) {
      requested.focus();
    }
  }

  function bindLinearTabKeys(selector, activation) {
    const tabs = all(selector);
    tabs.forEach((tab, index) => {
      tab.addEventListener("keydown", (event) => {
        let next = null;
        if (event.key === "ArrowRight" || event.key === "ArrowDown") {
          next = (index + 1) % tabs.length;
        } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
          next = (index - 1 + tabs.length) % tabs.length;
        } else if (event.key === "Home") {
          next = 0;
        } else if (event.key === "End") {
          next = tabs.length - 1;
        }
        if (next !== null) {
          event.preventDefault();
          activation(tabs[next].dataset.tab || tabs[next].dataset.runSubtab, true);
        }
      });
    });
  }

  function option(value, label) {
    return element("option", { value, text: label });
  }

  function selectedTrack() {
    return state.tracks.find((track) => track.track_id === state.selectedTrackId) || null;
  }

  function selectedLibrarySource() {
    return (
      (state.masterLibrary.sources || []).find(
        (entry) =>
          entry.source && entry.source.track_id === state.selectedTrackId,
      ) || null
    );
  }

  function activeMasterVersions(entry) {
    return (entry && Array.isArray(entry.versions) ? entry.versions : []).filter(
      (version) =>
        version.library_status === "available" &&
        (version.deliverables || []).some(
          (deliverable) => deliverable.playable,
        ),
    );
  }

  function selectedMasterVersion(entry = selectedLibrarySource()) {
    if (!entry) {
      return null;
    }
    const versions = activeMasterVersions(entry);
    const preferred =
      state.preferredMasterBySource.get(entry.source.track_id) ||
      state.selectedMasterVersionId;
    return (
      versions.find((version) => version.version_id === preferred) ||
      versions[0] ||
      null
    );
  }

  function masterExportKey(sourceTrackId, versionId, artifactOrdinal = null) {
    return `${sourceTrackId}\u0000${versionId}\u0000${
      artifactOrdinal === null || artifactOrdinal === undefined
        ? "preferred"
        : artifactOrdinal
    }`;
  }

  function selectedSet() {
    return (
      state.referenceSets.find((referenceSet) => referenceSet.set_id === state.selectedSetId) ||
      null
    );
  }

  function maxReferences() {
    return (
      state.defaults &&
      state.defaults.limits &&
      Number(state.defaults.limits.maximum_references)
    ) || MAX_REFERENCES_FALLBACK;
  }

  function maxTargets() {
    return (
      state.defaults &&
      state.defaults.limits &&
      Number(state.defaults.limits.maximum_targets)
    ) || MAX_TARGETS_FALLBACK;
  }

  function activeTracksForRole(role) {
    return state.tracks.filter(
      (track) =>
        !track.archived &&
        Boolean(track.preferred_path) &&
        Array.isArray(track.roles) &&
        track.roles.includes(role),
    );
  }

  function queuedTrackIsUsable(item, role) {
    const track = state.tracks.find(
      (candidate) => candidate.track_id === item.track_id,
    );
    return Boolean(
      track &&
        !track.archived &&
        track.preferred_path &&
        Array.isArray(track.roles) &&
        track.roles.includes(role),
    );
  }

  function referenceWeightsAreUsable() {
    const weightsAreFinite = state.jobReferences.every(
      (reference) =>
        Number.isFinite(Number(reference.level_weight)) &&
        Number(reference.level_weight) >= 0 &&
        Number.isFinite(Number(reference.frequency_weight)) &&
        Number(reference.frequency_weight) >= 0,
    );
    if (!weightsAreFinite || !state.jobReferences.length) {
      return false;
    }
    return (
      state.jobReferences.reduce(
        (sum, reference) => sum + Number(reference.level_weight),
        0,
      ) > 0 &&
      state.jobReferences.reduce(
        (sum, reference) => sum + Number(reference.frequency_weight),
        0,
      ) > 0
    );
  }

  function jobRequestIsPending() {
    return state.validating || state.submitting;
  }

  function setJobRequestBusy(busy) {
    const panel = $("panel-master-job");
    const form = $("job-form");
    const rail = $("task-target-progress").closest(".validation-panel");
    panel.toggleAttribute("inert", busy);
    panel.setAttribute("aria-busy", String(busy));
    form.setAttribute("aria-busy", String(busy));
    if (rail) {
      rail.setAttribute("aria-busy", String(busy));
    }
    renderJobReadiness();
  }

  function jobReadiness() {
    return {
      inputs:
        state.jobTargets.length > 0 &&
        state.jobTargets.every((target) => queuedTrackIsUsable(target, "target")),
      references:
        state.jobReferences.length > 0 &&
        state.jobReferences.every((reference) =>
          queuedTrackIsUsable(reference, "reference"),
        ) &&
        referenceWeightsAreUsable(),
      destination: Boolean($("job-output-directory").value.trim()),
      output: ["output-limited", "output-normalized", "output-raw"].some(
        (id) => $(id).checked,
      ),
    };
  }

  function renderJobReadiness() {
    const readiness = jobReadiness();
    const inputsAvailable = state.jobTargets.every((target) =>
      queuedTrackIsUsable(target, "target"),
    );
    const referencesAvailable = state.jobReferences.every((reference) =>
      queuedTrackIsUsable(reference, "reference"),
    );
    const labels = {
      inputs: state.jobTargets.length
        ? inputsAvailable
          ? `${state.jobTargets.length} ${state.jobTargets.length === 1 ? "mix" : "mixes"}`
          : "Repair unavailable mixes"
        : "Add at least one mix",
      references: state.jobReferences.length
        ? !referencesAvailable
          ? "Repair unavailable references"
          : referenceWeightsAreUsable()
            ? `${state.jobReferences.length} ${state.jobReferences.length === 1 ? "reference" : "references"}`
            : "Set positive reference weights"
        : "Choose a reference",
      destination: readiness.destination ? "Destination ready" : "Choose a destination",
      output: readiness.output ? "Deliverable ready" : "Choose a deliverable",
    };
    for (const [name, ready] of Object.entries(readiness)) {
      const item = $(`readiness-${name}`);
      item.classList.toggle("ready", ready);
      item.querySelector("span").textContent = ready ? "✓" : "○";
      item.querySelector("strong").textContent = labels[name];
    }
    const count = state.jobTargets.length;
    $("render-button-label").textContent = count
      ? `Render ${count} ${count === 1 ? "master" : "masters"}`
      : "Render masters";
    const ready = Object.values(readiness).every(Boolean);
    const active = jobRequestIsPending() || Boolean(
      state.currentTask && ["queued", "running"].includes(state.currentTask.state),
    );
    for (const id of ["job-validate-button", "dry-run-button", "render-button"]) {
      $(id).disabled = active || !ready;
    }
    all('[data-command="validate-job"]').forEach((button) => {
      button.disabled = active || !ready;
    });
    return ready;
  }

  async function chooseCatalogTracks(role) {
    const selectedIds = new Set(
      (role === "target" ? state.jobTargets : state.jobReferences).map(
        (item) => item.track_id,
      ),
    );
    const capacity =
      (role === "target" ? maxTargets() : maxReferences()) - selectedIds.size;
    if (capacity <= 0) {
      toast(
        `This ${role === "target" ? "mix queue" : "reference profile"} is full.`,
        "warning",
      );
      return;
    }
    const available = activeTracksForRole(role).filter(
      (track) => !selectedIds.has(track.track_id),
    );
    if (!available.length) {
      toast(
        `No additional ${role === "target" ? "mixes" : "references"} are available. Import audio to add more.`,
        "warning",
      );
      return;
    }

    const selected = new Set();
    const shell = element("div", { className: "picker-dialog" });
    const searchLabel = element("label", { className: "search-field picker-search" });
    const search = element("input", {
      type: "search",
      attrs: {
        placeholder: `Search ${role === "target" ? "mixes" : "references"}`,
        "aria-label": `Search available ${role === "target" ? "mixes" : "references"}`,
      },
    });
    searchLabel.append(element("span", { text: "⌕", attrs: { "aria-hidden": "true" } }), search);
    const summary = element("span", {
      className: "selection-summary",
      text: "Nothing selected",
      attrs: { "aria-live": "polite" },
    });
    const results = element("div", {
      className: "picker-results",
      attrs: { role: "group", "aria-label": "Available tracks" },
    });
    const renderResults = () => {
      const query = search.value.trim().toLowerCase();
      clear(results);
      const matches = available.filter((track) =>
        safeJson(track).toLowerCase().includes(query),
      );
      for (const track of matches) {
        const name = track.label || basename(track.preferred_path);
        const checkbox = element("input", { type: "checkbox", value: track.track_id });
        checkbox.checked = selected.has(track.track_id);
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) {
            selected.add(track.track_id);
          } else {
            selected.delete(track.track_id);
          }
          summary.textContent = selected.size
            ? `${selected.size} selected`
            : "Nothing selected";
        });
        const copy = element("span", { className: "picker-option-copy" });
        copy.append(
          element("strong", { text: name }),
          element("small", {
            text: [
              track.audio_facts && track.audio_facts.format,
              formatDuration(track.audio_facts && track.audio_facts.duration_seconds),
            ]
              .filter((value) => value && value !== "—")
              .join(" · "),
          }),
        );
        const row = element("label", { className: "picker-option" });
        row.append(checkbox, copy);
        results.append(row);
      }
      if (!matches.length) {
        results.append(
          element("div", { className: "picker-empty", text: "No matching tracks." }),
        );
      }
    };
    search.addEventListener("input", renderResults);
    shell.append(searchLabel, summary, results);
    renderResults();

    const added = await openDialog({
      kicker: role === "target" ? "Mastering queue" : "Reference profile",
      title: role === "target" ? "Add mixes" : "Add references",
      body: shell,
      confirmText: "Add selected",
      onConfirm: () => {
        if (!selected.size) {
          throw new Error("Choose at least one track.");
        }
        if (selected.size > capacity) {
          throw new Error(
            `This queue has room for ${capacity} more ${capacity === 1 ? "track" : "tracks"}.`,
          );
        }
        for (const trackId of selected) {
          if (role === "target") {
            addJobTarget(trackId);
          } else {
            addJobReference(trackId);
          }
        }
        return selected.size;
      },
    });
    if (added) {
      toast(
        `${added} ${role === "target" ? (added === 1 ? "mix" : "mixes") : added === 1 ? "reference" : "references"} added.`,
        "good",
      );
    }
  }

  function renderSourcePickers() {
    const targetSelect = $("job-target");
    const referenceSelect = $("reference-picker");
    const setSelect = $("reference-set-picker");
    const previousTarget = targetSelect.value;
    const previousReference = referenceSelect.value;
    const previousSet = setSelect.value;

    clear(targetSelect);
    targetSelect.append(option("", "Choose a target from the catalog"));
    for (const track of activeTracksForRole("target")) {
      targetSelect.append(option(track.track_id, track.label || basename(track.preferred_path)));
    }
    if (activeTracksForRole("target").some((track) => track.track_id === previousTarget)) {
      targetSelect.value = previousTarget;
    }

    clear(referenceSelect);
    referenceSelect.append(option("", "Choose a reference"));
    for (const track of activeTracksForRole("reference")) {
      referenceSelect.append(
        option(track.track_id, track.label || basename(track.preferred_path)),
      );
    }
    if (
      activeTracksForRole("reference").some((track) => track.track_id === previousReference)
    ) {
      referenceSelect.value = previousReference;
    }

    clear(setSelect);
    setSelect.append(option("", "Choose a named set"));
    for (const referenceSet of state.referenceSets) {
      setSelect.append(
        option(
          referenceSet.set_id,
          `${referenceSet.name} (${referenceSet.members.length})`,
        ),
      );
    }
    if (state.referenceSets.some((referenceSet) => referenceSet.set_id === previousSet)) {
      setSelect.value = previousSet;
    }
    renderTargetFacts();
  }

  function renderTargetFacts() {
    const host = $("target-facts");
    host.hidden = true;
    clear(host);
    renderJobTargets();
  }

  function jobTargetFromTrack(track) {
    return {
      track_id: track.track_id,
      label: track.label || basename(track.preferred_path),
      path: track.preferred_path,
      audio_facts: track.audio_facts || {},
    };
  }

  function addJobTarget(trackId) {
    if (state.jobTargets.length >= maxTargets()) {
      throw new Error(`At most ${maxTargets()} input tracks can be selected.`);
    }
    if (state.jobTargets.some((target) => target.track_id === trackId)) {
      throw new Error("This input track is already in the mastering queue.");
    }
    const track = state.tracks.find((candidate) => candidate.track_id === trackId);
    if (
      !track ||
      track.archived ||
      !track.preferred_path ||
      !Array.isArray(track.roles) ||
      !track.roles.includes("target")
    ) {
      throw new Error("Choose an active catalog track with the target role.");
    }
    state.jobTargets.push(jobTargetFromTrack(track));
    renderJobTargets();
    invalidateValidation();
  }

  function restoreQueueControlFocus(
    bodyId,
    trackId,
    controlName,
    fallbackIndex,
    emptyControlId,
  ) {
    const rows = all(`#${bodyId} .source-row`);
    const row =
      rows.find((candidate) => candidate.dataset.queueTrackId === trackId) ||
      rows[Math.min(fallbackIndex, Math.max(0, rows.length - 1))];
    if (!row) {
      $(emptyControlId).focus();
      return;
    }
    let control = row.querySelector(`[data-queue-control="${controlName}"]`);
    if (!control || control.disabled) {
      const alternate = controlName === "up" ? "down" : "up";
      control = row.querySelector(`[data-queue-control="${alternate}"]:not(:disabled)`);
    }
    if (!control || control.disabled) {
      control = row.querySelector('[data-queue-control="remove"]');
    }
    if (control) {
      control.focus();
    }
  }

  function renderJobTargets() {
    const body = $("job-targets-body");
    clear(body);
    if (!state.jobTargets.length) {
      emptyRow(body, 6, "No input tracks selected.");
    } else {
      state.jobTargets.forEach((target, index) => {
        const track =
          state.tracks.find((candidate) => candidate.track_id === target.track_id) ||
          target;
        const facts = track.audio_facts || target.audio_facts || {};
        const row = element("tr", {
          className: "source-row",
          attrs: { "data-queue-track-id": target.track_id },
        });
        appendTextCell(row, index + 1);
        const identity = element("td");
        identity.append(
          element("strong", { text: target.label }),
          element("small", {
            text: basename(target.path),
            title: `${display(target.path)}\n${target.track_id}`,
          }),
        );
        row.append(identity);
        appendTextCell(
          row,
          [facts.format, facts.subtype].filter(Boolean).join(" / "),
        );
        appendTextCell(row, formatDuration(facts.duration_seconds));
        row.append(auditionActionCell(track));
        const actions = element("td", { className: "row-actions" });
        const up = element("button", {
          className: "icon-button",
          text: "↑",
          type: "button",
          title: "Move earlier",
          attrs: {
            "aria-label": `Move ${target.label} earlier`,
            "data-queue-control": "up",
          },
        });
        up.disabled = index === 0;
        up.addEventListener("click", () => moveJobTarget(index, -1, "up"));
        const down = element("button", {
          className: "icon-button",
          text: "↓",
          type: "button",
          title: "Move later",
          attrs: {
            "aria-label": `Move ${target.label} later`,
            "data-queue-control": "down",
          },
        });
        down.disabled = index === state.jobTargets.length - 1;
        down.addEventListener("click", () => moveJobTarget(index, 1, "down"));
        const remove = element("button", {
          className: "icon-button danger-text",
          text: "×",
          type: "button",
          title: "Remove input",
          attrs: {
            "aria-label": `Remove ${target.label}`,
            "data-queue-control": "remove",
          },
        });
        remove.addEventListener("click", () => {
          state.jobTargets.splice(index, 1);
          renderJobTargets();
          invalidateValidation();
          restoreQueueControlFocus(
            "job-targets-body",
            null,
            "remove",
            index,
            "target-add-button",
          );
        });
        actions.append(up, down, remove);
        row.append(actions);
        body.append(row);
      });
    }
    $("target-count").textContent =
      `${state.jobTargets.length} of ${maxTargets()} inputs`;
    renderJobReadiness();
  }

  function moveJobTarget(index, offset, controlName = null) {
    const destination = index + offset;
    if (destination < 0 || destination >= state.jobTargets.length) {
      return;
    }
    const [item] = state.jobTargets.splice(index, 1);
    state.jobTargets.splice(destination, 0, item);
    renderJobTargets();
    invalidateValidation();
    if (controlName) {
      restoreQueueControlFocus(
        "job-targets-body",
        item.track_id,
        controlName,
        destination,
        "target-add-button",
      );
    }
  }

  function auditionDescriptor(track, fallback = {}) {
    if (!track || !track.track_id) {
      throw new Error("This audio item is not available in the catalog.");
    }
    return {
      track_id: track.track_id,
      label:
        track.label ||
        fallback.label ||
        basename(track.preferred_path || fallback.path || ""),
      path: track.preferred_path || fallback.path || "",
      choice_key: fallback.choice_key || null,
    };
  }

  function auditionChoices() {
    const choices = [];
    for (const entry of state.masterLibrary.sources || []) {
      const source = entry && entry.source;
      if (!source) {
        continue;
      }
      const sourceName = source.label || basename(source.preferred_path);
      if (source.preferred_path) {
        choices.push({
          key: `original:${source.track_id}`,
          group: "Library audio",
          label: sourceName,
          descriptor: auditionDescriptor(source, {
            label: `Original · ${sourceName}`,
            path: source.preferred_path,
            choice_key: `original:${source.track_id}`,
          }),
        });
      }
      for (const version of activeMasterVersions(entry)) {
        for (const deliverable of (version.deliverables || []).filter(
          (item) => item.playable,
        )) {
          const mode = deliverable.mode || deliverable.manifest_role || "master";
          const key = `master:${source.track_id}:${version.version_id}:${deliverable.ordinal}`;
          choices.push({
            key,
            group: `Masters · ${sourceName}`,
            label: `${version.display_label} · ${mode}`,
            descriptor: auditionDescriptor(deliverable, {
              label: `Master · ${sourceName} · ${version.display_label} · ${mode}`,
              path: deliverable.path,
              choice_key: key,
            }),
          });
        }
      }
    }
    return choices;
  }

  function renderAuditionPicker(slot, choices) {
    const select = $(`audition-picker-${slot}`);
    const current = state.audition[slot];
    const selectedKey =
      (current && current.choice_key) ||
      (current && choices.find((choice) => choice.descriptor.track_id === current.track_id)?.key) ||
      "";
    clear(select);
    select.append(option("", `Choose ${slot.toUpperCase()}…`));
    const groups = new Map();
    for (const choice of choices) {
      if (!groups.has(choice.group)) {
        const group = element("optgroup", { attrs: { label: choice.group } });
        groups.set(choice.group, group);
        select.append(group);
      }
      groups.get(choice.group).append(option(choice.key, choice.label));
    }
    if (selectedKey && choices.some((choice) => choice.key === selectedKey)) {
      select.value = selectedKey;
    } else if (current) {
      const unavailableKey = selectedKey || `unavailable:${slot}`;
      const unavailable = option(
        unavailableKey,
        `Unavailable · ${current.label}`,
      );
      unavailable.disabled = true;
      select.append(unavailable);
      select.value = unavailableKey;
    }
  }

  function renderAuditionDock() {
    const active = state.audition.active;
    const current = state.audition[active];
    const hasAudio = Boolean(
      state.audition.a || state.audition.b || state.audition.preview,
    );
    const choices = auditionChoices();
    $("audition-dock").hidden = !hasAudio;
    document.body.classList.toggle("has-audition", hasAudio);
    for (const slot of ["a", "b"]) {
      const button = $(`audition-slot-${slot}`);
      const selected = slot === active;
      button.classList.toggle("active", selected);
      button.setAttribute("aria-pressed", String(selected));
      $(`audition-label-${slot}`).textContent =
        (state.audition[slot] && state.audition[slot].label) || "Empty";
      button.setAttribute(
        "aria-label",
        state.audition[slot]
          ? `Hear comparison ${slot.toUpperCase()}: ${state.audition[slot].label}`
          : `Comparison ${slot.toUpperCase()} is empty`,
      );
      renderAuditionPicker(slot, choices);
    }
    $("audition-now-playing").textContent = current
      ? `${active === "preview" ? "Preview" : active.toUpperCase()} · ${current.label}`
      : "Choose a populated comparison slot";
    $("audition-swap-button").disabled = !(state.audition.a || state.audition.b);
  }

  function chooseAuditionCandidate(slot, key) {
    if (!key) {
      const player = $("audition-player");
      state.audition[slot] = null;
      state.automaticComparisonSourceId = null;
      if (state.audition.active === slot) {
        const alternateSlot = slot === "a" ? "b" : "a";
        if (state.audition[alternateSlot]) {
          activateAuditionSlot(alternateSlot, { preserveTime: true });
          return;
        }
        player.pause();
        state.audition.loadSequence += 1;
        player.removeAttribute("src");
        player.dataset.trackId = "";
        player.load();
        state.audition.active = alternateSlot;
      }
      renderAuditionDock();
      return;
    }
    const choice = auditionChoices().find((candidate) => candidate.key === key);
    if (!choice) {
      return;
    }
    assignAudition(slot, choice.descriptor, {
      label: choice.descriptor.label,
      path: choice.descriptor.path,
      choice_key: choice.key,
      preserveTime: true,
      silent: true,
    });
  }

  function swapAuditionSlots() {
    const priorActive = state.audition.active;
    [state.audition.a, state.audition.b] = [state.audition.b, state.audition.a];
    state.audition.active =
      priorActive === "a" ? "b" : priorActive === "b" ? "a" : priorActive;
    state.automaticComparisonSourceId = null;
    renderAuditionDock();
    toast("Comparison A and B swapped.", "good");
  }

  function activateAuditionSlot(slot, options = {}) {
    const selected = state.audition[slot];
    if (!selected) {
      toast(`Comparison slot ${slot.toUpperCase()} is empty.`, "warning");
      return;
    }
    const player = $("audition-player");
    const preserveTime = options.preserveTime !== false;
    const previousTime =
      preserveTime && Number.isFinite(player.currentTime) ? player.currentTime : 0;
    const shouldPlay = Boolean(options.autoplay) || (!player.paused && !player.ended);
    state.audition.active = slot;
    if (slot !== "preview") {
      state.audition.preview = null;
    }
    renderAuditionDock();
    if (player.dataset.trackId === selected.track_id) {
      if (options.autoplay) {
        player.play().catch((error) => reportError(error, "Could not start playback"));
      }
      return;
    }
    const loadSequence = ++state.audition.loadSequence;
    player.pause();
    player.src = `/media/tracks/${encodeURIComponent(selected.track_id)}`;
    player.dataset.trackId = selected.track_id;
    player.load();
    player.addEventListener(
      "loadedmetadata",
      () => {
        if (
          loadSequence !== state.audition.loadSequence ||
          player.dataset.trackId !== selected.track_id
        ) {
          return;
        }
        if (preserveTime && previousTime > 0 && Number.isFinite(player.duration)) {
          player.currentTime = Math.min(previousTime, Math.max(0, player.duration - 0.05));
        }
        if (shouldPlay) {
          player.play().catch((error) => reportError(error, "Could not start playback"));
        }
      },
      { once: true },
    );
  }

  function assignAudition(slot, track, options = {}) {
    state.audition[slot] = auditionDescriptor(track, options);
    state.automaticComparisonSourceId = null;
    renderAuditionDock();
    if (options.autoplay || state.audition.active === slot) {
      activateAuditionSlot(slot, {
        autoplay: Boolean(options.autoplay),
        preserveTime: Boolean(options.preserveTime),
      });
    }
    if (!options.silent) {
      toast(
        `${state.audition[slot].label} loaded into ${slot.toUpperCase()}.`,
        "good",
      );
    }
  }

  function playTrack(track, fallback = {}) {
    state.audition.preview = auditionDescriptor(track, fallback);
    activateAuditionSlot("preview", {
      autoplay: true,
      preserveTime: false,
    });
  }

  function clearAudition() {
    const player = $("audition-player");
    player.pause();
    state.audition.loadSequence += 1;
    player.removeAttribute("src");
    player.dataset.trackId = "";
    player.load();
    state.audition.a = null;
    state.audition.b = null;
    state.audition.preview = null;
    state.audition.active = "a";
    state.automaticComparisonSourceId = null;
    renderAuditionDock();
  }

  function deliverableForVersion(version) {
    if (!version) {
      return null;
    }
    const ordinal = state.preferredDeliverableByVersion.get(version.version_id);
    return (
      (version.deliverables || []).find(
        (deliverable) =>
          deliverable.ordinal === ordinal && deliverable.playable,
      ) ||
      (version.preferred_audition && version.preferred_audition.playable
        ? version.preferred_audition
        : null) ||
      (version.deliverables || []).find((deliverable) => deliverable.playable) ||
      null
    );
  }

  function compareOriginalWithMaster(entry, version, options = {}) {
    if (!entry || !entry.source || !entry.source.preferred_path) {
      throw new Error(
        "The original is unavailable. Relink it before starting an A/B comparison.",
      );
    }
    const selectedVersion = version || selectedMasterVersion(entry);
    const master = deliverableForVersion(selectedVersion);
    if (!selectedVersion || !master || !master.playable) {
      throw new Error("Choose an available mastered version to compare.");
    }
    const sourceLabel =
      entry.source.label || basename(entry.source.preferred_path);
    const masterLabel =
      master.label ||
      `${selectedVersion.display_label} · ${master.mode || master.manifest_role || "master"}`;
    state.selectedMasterVersionId = selectedVersion.version_id;
    state.preferredMasterBySource.set(
      entry.source.track_id,
      selectedVersion.version_id,
    );
    state.automaticComparisonSourceId = entry.source.track_id;
    state.audition.a = auditionDescriptor(entry.source, {
      label: `Original · ${sourceLabel}`,
      path: entry.source.preferred_path,
    });
    state.audition.a.label = `Original · ${sourceLabel}`;
    state.audition.b = auditionDescriptor(master, {
      label: `Master · ${masterLabel}`,
      path: master.path,
    });
    state.audition.b.label = `Master · ${masterLabel}`;
    state.audition.preview = null;
    state.audition.active = options.startWithMaster ? "b" : "a";
    renderAuditionDock();
    activateAuditionSlot(state.audition.active, {
      autoplay: Boolean(options.autoplay),
      preserveTime: Boolean(options.preserveTime),
    });
    if (!options.silent) {
      toast(
        `A/B ready: ${sourceLabel} against ${selectedVersion.display_label}.`,
        "good",
      );
    }
  }

  function prepareAutomaticComparison(entry) {
    const comparisonHasAudio = Boolean(state.audition.a || state.audition.b);
    if (
      !entry ||
      (comparisonHasAudio && !state.automaticComparisonSourceId) ||
      state.automaticComparisonSourceId === entry.source.track_id
    ) {
      return;
    }
    const versions = activeMasterVersions(entry);
    if (!versions.length) {
      return;
    }
    try {
      compareOriginalWithMaster(entry, selectedMasterVersion(entry) || versions[0], {
        autoplay: false,
        preserveTime: false,
        silent: true,
      });
      state.automaticComparisonSourceId = entry.source.track_id;
    } catch (error) {
      console.warn("Automatic A/B preparation was skipped", error);
    }
  }

  function auditionActionCell(track, fallback = {}) {
    const cell = element("td", { className: "audition-actions" });
    const accessibleLabel = fallback.label || (track && track.label) || "track";
    const playable = Boolean(
      track && track.track_id && (track.preferred_path || track.path || fallback.path),
    );
    const play = element("button", {
      className: "icon-button play-button",
      text: "▶",
      type: "button",
      title: "Play now",
      attrs: { "aria-label": `Play ${accessibleLabel}` },
    });
    const slotA = element("button", {
      className: "slot-button",
      text: "A",
      type: "button",
      title: "Load comparison slot A",
      attrs: { "aria-label": `Load ${accessibleLabel} into comparison A` },
    });
    const slotB = element("button", {
      className: "slot-button",
      text: "B",
      type: "button",
      title: "Load comparison slot B",
      attrs: { "aria-label": `Load ${accessibleLabel} into comparison B` },
    });
    play.disabled = !playable;
    slotA.disabled = !playable;
    slotB.disabled = !playable;
    for (const button of [play, slotA, slotB]) {
      button.addEventListener("click", (event) => event.stopPropagation());
    }
    play.addEventListener("click", () => playTrack(track, fallback));
    slotA.addEventListener("click", () => assignAudition("a", track, fallback));
    slotB.addEventListener("click", () => assignAudition("b", track, fallback));
    cell.append(play, slotA, slotB);
    return cell;
  }

  function jobReferenceFromTrack(track, levelWeight = 1, frequencyWeight = 1) {
    return {
      track_id: track.track_id,
      label: track.label || basename(track.preferred_path),
      path: track.preferred_path,
      level_weight: Number(levelWeight),
      frequency_weight: Number(frequencyWeight),
    };
  }

  function addJobReference(trackId, levelWeight = 1, frequencyWeight = 1) {
    if (state.jobReferences.length >= maxReferences()) {
      throw new Error(`At most ${maxReferences()} references can be selected.`);
    }
    if (state.jobReferences.some((reference) => reference.track_id === trackId)) {
      throw new Error(
        "This reference content is already selected. Increase its weights instead of adding a duplicate row.",
      );
    }
    const track = state.tracks.find((candidate) => candidate.track_id === trackId);
    if (!track || track.archived || !track.roles.includes("reference")) {
      throw new Error("Choose an active catalog track with the reference role.");
    }
    state.jobReferences.push(jobReferenceFromTrack(track, levelWeight, frequencyWeight));
    renderJobReferences();
    invalidateValidation();
  }

  function normalizedWeights(key) {
    const total = state.jobReferences.reduce((sum, item) => sum + Number(item[key]), 0);
    return state.jobReferences.map((item) =>
      total > 0 ? Number(item[key]) / total : 0,
    );
  }

  function renderJobReferences() {
    const body = $("job-references-body");
    const singleReference = state.jobReferences.length === 1;
    if (singleReference) {
      state.jobReferences[0].level_weight = 1;
      state.jobReferences[0].frequency_weight = 1;
    }
    const levelNormalized = normalizedWeights("level_weight");
    const frequencyNormalized = normalizedWeights("frequency_weight");
    clear(body);
    if (!state.jobReferences.length) {
      emptyRow(body, 8, "No references selected.");
    } else {
      state.jobReferences.forEach((reference, index) => {
        const row = element("tr", {
          className: "source-row",
          attrs: { "data-queue-track-id": reference.track_id },
        });
        appendTextCell(row, index + 1);
        const identityCell = element("td");
        identityCell.append(
          element("strong", { text: reference.label }),
          element("small", {
            text: basename(reference.path),
            title: `${display(reference.path)}\n${reference.track_id}`,
          }),
        );
        row.append(identityCell);

        const levelCell = element("td");
        const levelInput = element("input", {
          type: "number",
          value: reference.level_weight,
          attrs: {
            min: "0",
            step: "0.1",
            "aria-label": `Level weight for ${reference.label}`,
            "data-queue-control": "level",
          },
        });
        levelInput.disabled = singleReference;
        levelInput.title = singleReference
          ? "A single reference always has an effective level weight of 1."
          : "";
        levelInput.addEventListener("change", () => {
          const value = Number(levelInput.value);
          if (!Number.isFinite(value) || value < 0) {
            levelInput.value = String(reference.level_weight);
            toast("Weights must be finite non-negative numbers.", "warning");
            return;
          }
          reference.level_weight = value;
          renderJobReferences();
          invalidateValidation();
          restoreQueueControlFocus(
            "job-references-body",
            reference.track_id,
            "level",
            index,
            "reference-add-button",
          );
        });
        levelCell.append(levelInput);
        row.append(levelCell);
        appendTextCell(row, `${(levelNormalized[index] * 100).toFixed(2)}%`);

        const frequencyCell = element("td");
        const frequencyInput = element("input", {
          type: "number",
          value: reference.frequency_weight,
          attrs: {
            min: "0",
            step: "0.1",
            "aria-label": `Frequency weight for ${reference.label}`,
            "data-queue-control": "frequency",
          },
        });
        frequencyInput.disabled = singleReference;
        frequencyInput.title = singleReference
          ? "A single reference always has an effective frequency weight of 1."
          : "";
        frequencyInput.addEventListener("change", () => {
          const value = Number(frequencyInput.value);
          if (!Number.isFinite(value) || value < 0) {
            frequencyInput.value = String(reference.frequency_weight);
            toast("Weights must be finite non-negative numbers.", "warning");
            return;
          }
          reference.frequency_weight = value;
          renderJobReferences();
          invalidateValidation();
          restoreQueueControlFocus(
            "job-references-body",
            reference.track_id,
            "frequency",
            index,
            "reference-add-button",
          );
        });
        frequencyCell.append(frequencyInput);
        row.append(frequencyCell);
        appendTextCell(row, `${(frequencyNormalized[index] * 100).toFixed(2)}%`);

        const track =
          state.tracks.find((candidate) => candidate.track_id === reference.track_id) ||
          reference;
        row.append(auditionActionCell(track));

        const actions = element("td", { className: "row-actions" });
        const up = element("button", {
          className: "icon-button",
          text: "↑",
          type: "button",
          title: "Move earlier",
          attrs: {
            "aria-label": `Move ${reference.label} earlier`,
            "data-queue-control": "up",
          },
        });
        up.disabled = index === 0;
        up.addEventListener("click", () => moveJobReference(index, -1, "up"));
        const down = element("button", {
          className: "icon-button",
          text: "↓",
          type: "button",
          title: "Move later",
          attrs: {
            "aria-label": `Move ${reference.label} later`,
            "data-queue-control": "down",
          },
        });
        down.disabled = index === state.jobReferences.length - 1;
        down.addEventListener("click", () => moveJobReference(index, 1, "down"));
        const remove = element("button", {
          className: "icon-button danger-text",
          text: "×",
          type: "button",
          title: "Remove",
          attrs: {
            "aria-label": `Remove ${reference.label}`,
            "data-queue-control": "remove",
          },
        });
        remove.addEventListener("click", () => {
          state.jobReferences.splice(index, 1);
          renderJobReferences();
          invalidateValidation();
          restoreQueueControlFocus(
            "job-references-body",
            null,
            "remove",
            index,
            "reference-add-button",
          );
        });
        actions.append(up, down, remove);
        row.append(actions);
        body.append(row);
      });
    }
    $("reference-count").textContent =
      `${state.jobReferences.length} of ${maxReferences()} references`;
    renderEngineSummary();
    renderJobReadiness();
  }

  function moveJobReference(index, offset, controlName = null) {
    const destination = index + offset;
    if (destination < 0 || destination >= state.jobReferences.length) {
      return;
    }
    const [item] = state.jobReferences.splice(index, 1);
    state.jobReferences.splice(destination, 0, item);
    renderJobReferences();
    invalidateValidation();
    if (controlName) {
      restoreQueueControlFocus(
        "job-references-body",
        item.track_id,
        controlName,
        destination,
        "reference-add-button",
      );
    }
  }

  function renderEngineSummary() {
    const count = state.jobReferences.length;
    const name = $("engine-summary-name");
    const detail = $("engine-summary-detail");
    $("execution-engine").value =
      count === 1
        ? "upstream-matchering-2.0.6"
        : count >= 2
          ? "music-mastering-tools-native"
          : "Derived from reference count";
    if (count === 0) {
      name.textContent = "Choose references";
      detail.textContent = "The server selects the truthful engine from the reference count.";
    } else if (count === 1) {
      name.textContent = "Matchering 2.0.6 compatibility";
      detail.textContent = "One reference; both effective weights are fixed to 1.";
    } else {
      name.textContent = "Native weighted profile";
      detail.textContent =
        `${count} references; level and frequency influence are normalized independently.`;
    }
  }

  function valueNumber(id, options = {}) {
    const raw = $(id).value.trim();
    if (raw === "" && options.nullable) {
      return null;
    }
    const value = Number(raw);
    if (!Number.isFinite(value)) {
      throw new Error(`${options.label || id} must be a finite number.`);
    }
    return value;
  }

  function buildJobRequest() {
    if (!state.jobTargets.length) {
      throw new Error("Add at least one input track to the mastering queue.");
    }
    if (!state.jobReferences.length) {
      throw new Error("Choose at least one reference track.");
    }
    const outputDirectory = $("job-output-directory").value.trim();
    if (!outputDirectory) {
      throw new Error("Choose a destination folder for the mastered files.");
    }
    const targetIds = state.jobTargets.map((target) => target.track_id);
    return {
      target_id: targetIds[0],
      target_ids: targetIds,
      output_directory: outputDirectory,
      references: state.jobReferences.map((reference) => ({
        track_id: reference.track_id,
        level_weight: Number(reference.level_weight),
        frequency_weight: Number(reference.frequency_weight),
      })),
      outputs: {
        limited: $("output-limited").checked,
        normalized: $("output-normalized").checked,
        raw: $("output-raw").checked,
        limited_subtype: $("output-limited-subtype").value,
        normalized_subtype: $("output-normalized-subtype").value,
        raw_subtype: $("output-raw-subtype").value,
      },
      preview: {
        enabled: $("preview-enabled").checked,
        subtype: $("preview-subtype").value,
        duration_seconds: valueNumber("preview-duration", { label: "Preview duration" }),
        analysis_step_seconds: valueNumber("preview-analysis-step", {
          label: "Preview analysis step",
        }),
        fade_seconds: valueNumber("preview-fade-seconds", { label: "Preview fade" }),
        fade_coefficient: valueNumber("preview-fade-coefficient", {
          label: "Preview fade coefficient",
        }),
      },
      settings: {
        audio: {
          internal_sample_rate: valueNumber("audio-sample-rate", {
            label: "Internal sample rate",
          }),
          max_length_seconds: valueNumber("audio-max-length", {
            label: "Maximum track length",
          }),
          allow_identical_target_and_reference: $("audio-allow-identical").checked,
          metadata_policy: "drop",
        },
        matching: {
          loudness_metric: "matchering-loud-section-rms",
          amount: 1,
          max_piece_seconds: valueNumber("matching-max-piece"),
          fft_size: valueNumber("matching-fft-size"),
          lin_log_oversampling: valueNumber("matching-oversampling"),
          rms_correction_steps: valueNumber("matching-rms-steps"),
          min_value: valueNumber("matching-min-value"),
          max_eq_gain_db: valueNumber("matching-max-eq", { nullable: true }),
          lowess_fraction: valueNumber("matching-lowess-fraction"),
          lowess_iterations: valueNumber("matching-lowess-iterations"),
          lowess_delta: valueNumber("matching-lowess-delta"),
        },
        limiter: {
          kind: $("limiter-kind").value,
          peak_mode: "sample-peak",
          threshold_linear: valueNumber("limiter-threshold"),
          true_peak_oversampling: valueNumber("limiter-true-peak-oversampling"),
          attack_ms: valueNumber("limiter-attack"),
          hold_ms: valueNumber("limiter-hold"),
          release_ms: valueNumber("limiter-release"),
          attack_filter_coefficient: valueNumber("limiter-attack-filter"),
          hold_filter_order: valueNumber("limiter-hold-filter-order"),
          hold_filter_coefficient: valueNumber("limiter-hold-filter"),
          release_filter_order: valueNumber("limiter-release-filter-order"),
          release_filter_coefficient: valueNumber("limiter-release-filter"),
          external_command: [],
        },
        detection: {
          silence_peak: valueNumber("detection-silence"),
          near_silence_peak: valueNumber("detection-near-silence"),
          clipping_peak: valueNumber("detection-clipping-peak"),
          clipping_samples_threshold: valueNumber("detection-clipping-samples"),
          limited_samples_threshold: valueNumber("detection-limited-samples"),
        },
        edge_cases: {
          silence: $("policy-silence").value,
          near_silence: $("policy-near-silence").value,
          non_finite_audio: $("policy-nonfinite").value,
          mono_input: $("policy-mono").value,
          more_than_two_channels: $("policy-multichannel").value,
          lossy_input: $("policy-lossy").value,
          sample_rate_conversion: $("policy-rate-conversion").value,
          // Portal destinations are always collision-safe. The service enforces
          // this again so an older or modified client cannot weaken the policy.
          existing_output: "error",
          create_output_directories: $("policy-create-directories").checked,
        },
      },
      notes: $("job-notes").value.trim() || null,
    };
  }

  function setControlValue(id, value) {
    if (value !== null && value !== undefined) {
      $(id).value = String(value);
    }
  }

  function applyDefaults(options = {}) {
    const defaults = state.defaults;
    if (!defaults) {
      return;
    }
    const audio = defaults.audio || {};
    const matching = defaults.matching || {};
    const limiter = defaults.limiter || {};
    const preview = defaults.preview || {};
    const detection = defaults.detection || {};
    const policies = defaults.edge_cases || {};
    const outputs = defaults.outputs || {};

    setControlValue("audio-sample-rate", audio.internal_sample_rate);
    setControlValue("audio-max-length", audio.max_length_seconds);
    $("audio-allow-identical").checked = Boolean(
      audio.allow_identical_target_and_reference,
    );
    setControlValue("matching-max-piece", matching.max_piece_seconds);
    setControlValue("matching-fft-size", matching.fft_size);
    setControlValue("matching-oversampling", matching.lin_log_oversampling);
    setControlValue("matching-rms-steps", matching.rms_correction_steps);
    setControlValue("matching-min-value", matching.min_value);
    $("matching-max-eq").value =
      matching.max_eq_gain_db === null || matching.max_eq_gain_db === undefined
        ? ""
        : String(matching.max_eq_gain_db);
    setControlValue("matching-lowess-fraction", matching.lowess_fraction);
    setControlValue("matching-lowess-iterations", matching.lowess_iterations);
    setControlValue("matching-lowess-delta", matching.lowess_delta);
    setControlValue("limiter-kind", limiter.kind);
    setControlValue("limiter-threshold", limiter.threshold_linear);
    setControlValue("limiter-true-peak-oversampling", limiter.true_peak_oversampling);
    setControlValue("limiter-attack", limiter.attack_ms);
    setControlValue("limiter-hold", limiter.hold_ms);
    setControlValue("limiter-release", limiter.release_ms);
    setControlValue("limiter-attack-filter", limiter.attack_filter_coefficient);
    setControlValue("limiter-hold-filter-order", limiter.hold_filter_order);
    setControlValue("limiter-hold-filter", limiter.hold_filter_coefficient);
    setControlValue("limiter-release-filter-order", limiter.release_filter_order);
    setControlValue("limiter-release-filter", limiter.release_filter_coefficient);
    setControlValue("detection-silence", detection.silence_peak);
    setControlValue("detection-near-silence", detection.near_silence_peak);
    setControlValue("detection-clipping-peak", detection.clipping_peak);
    setControlValue("detection-clipping-samples", detection.clipping_samples_threshold);
    setControlValue("detection-limited-samples", detection.limited_samples_threshold);
    setControlValue("policy-silence", policies.silence);
    setControlValue("policy-near-silence", policies.near_silence);
    setControlValue("policy-nonfinite", policies.non_finite_audio);
    setControlValue("policy-mono", policies.mono_input);
    setControlValue("policy-multichannel", policies.more_than_two_channels);
    setControlValue("policy-lossy", policies.lossy_input);
    setControlValue("policy-rate-conversion", policies.sample_rate_conversion);
    setControlValue("policy-existing-output", "error");
    $("policy-create-directories").checked = Boolean(
      policies.create_output_directories,
    );
    $("output-limited").checked = Boolean(outputs.limited);
    $("output-normalized").checked = Boolean(outputs.normalized);
    $("output-raw").checked = Boolean(outputs.raw);
    setControlValue("output-limited-subtype", outputs.limited_subtype);
    setControlValue("output-normalized-subtype", outputs.normalized_subtype);
    setControlValue("output-raw-subtype", outputs.raw_subtype);
    $("preview-enabled").checked = Boolean(preview.enabled);
    setControlValue("preview-subtype", preview.subtype);
    setControlValue("preview-duration", preview.duration_seconds);
    setControlValue("preview-analysis-step", preview.analysis_step_seconds);
    setControlValue("preview-fade-seconds", preview.fade_seconds);
    setControlValue("preview-fade-coefficient", preview.fade_coefficient);
    const defaultOutputDirectory =
      (state.preferences && state.preferences.default_output_directory) ||
      defaults.output_directory ||
      (state.bootstrap && state.bootstrap.workspace && state.bootstrap.workspace.outputs) ||
      "";
    setControlValue("job-output-directory", defaultOutputDirectory);
    $("job-notes").value = defaults.notes || "";
    togglePreview();

    if (options.clearSources) {
      state.jobTaskId = null;
      state.jobTaskTargets = [];
      $("job-target").value = "";
      state.jobTargets = [];
      state.jobReferences = [];
      renderJobTargets();
      renderJobReferences();
      renderTargetFacts();
    }
    invalidateValidation({ markDirty: !options.markClean });
  }

  function togglePreview() {
    const enabled = $("preview-enabled").checked;
    $("preview-fields").setAttribute("aria-disabled", String(!enabled));
    for (const id of [
      "preview-subtype",
      "preview-duration",
      "preview-analysis-step",
      "preview-fade-seconds",
      "preview-fade-coefficient",
    ]) {
      $(id).disabled = !enabled;
    }
  }

  function invalidateValidation(options = {}) {
    state.jobRevision += 1;
    state.jobDirty = options.markDirty === false ? false : true;
    $("validation-badge").className = "state-badge neutral";
    $("validation-badge").textContent = "Not checked";
    $("validation-results").hidden = true;
    $("validation-summary").textContent = renderJobReadiness()
      ? "Ready to check or render. Rendering includes an authoritative preflight."
      : "Complete the required items to continue.";
  }

  function renderBootstrap() {
    if (!state.bootstrap) {
      return;
    }
    const workspace = state.bootstrap.workspace || {};
    const catalog = state.bootstrap.catalog || {};
    const privacy = state.bootstrap.privacy || {};
    $("header-version").textContent = `v${runtime.version || "0.1.0"}`;
    $("dashboard-workspace").textContent = workspace.root || "Private workspace";
    $("metric-tracks").textContent = formatNumber(catalog.active_track_count, 0);
    $("metric-tracks-detail").textContent =
      `${formatNumber(catalog.track_count, 0)} total · revision ${display(catalog.revision, "0")}`;
    $("metric-sets").textContent = formatNumber(catalog.reference_set_count, 0);
    $("metric-runs").textContent = formatNumber(catalog.run_count, 0);
    $("metric-runs-detail").textContent =
      catalog.run_count ? "Manifest-backed audited history" : "No run data yet";
    $("status-catalog").textContent =
      `Catalog: ${formatNumber(catalog.track_count, 0)} tracks · r${display(catalog.revision, "0")}`;

    const info = $("portal-information");
    clear(info);
    const values = [
      ["Portal origin", runtime.origin],
      ["Workspace", workspace.root],
      ["Catalog", workspace.catalog_database],
      ["Runs", workspace.runs],
      ["Outputs", workspace.outputs],
      ["Exports", workspace.exports],
      ["Logs", workspace.logs],
      ["Privacy", privacy.local_only ? "Loopback only" : "Review configuration"],
    ];
    for (const [label, value] of values) {
      const group = element("div");
      group.append(element("dt", { text: label }), element("dd", { text: display(value) }));
      info.append(group);
    }
    renderDashboardRuns(state.bootstrap.recent_runs || []);
  }

  function renderDashboardRuns(runs) {
    const body = $("dashboard-runs-body");
    clear(body);
    if (!runs.length) {
      emptyRow(body, 5, "No audited runs yet.");
      return;
    }
    for (const run of runs.slice().reverse().slice(0, 5)) {
      const row = element("tr", { attrs: { tabindex: "0" } });
      appendTextCell(row, run.status);
      appendTextCell(row, shortId(run.run_id, 22), {
        code: true,
        title: run.run_id,
      });
      appendTextCell(row, shortId(run.selection_id, 18), {
        code: true,
        title: run.selection_id,
      });
      appendTextCell(row, formatDate(run.updated_at));
      const action = element("td");
      const button = element("button", {
        className: "button quiet small",
        text: "Inspect",
        type: "button",
      });
      button.addEventListener("click", () => {
        activateTab("runs");
        selectRun(run.run_id);
      });
      action.append(button);
      row.append(action);
      body.append(row);
    }
  }

  function trackState(track) {
    if (track.archived) {
      return "archived";
    }
    const locations = Array.isArray(track.locations) ? track.locations : [];
    if (locations.some((location) => location.state === "available")) {
      return "available";
    }
    return locations[0] ? locations[0].state : "no location";
  }

  function libraryStatusText(entry) {
    if (!entry || !entry.source || !entry.source.preferred_path) {
      return "Original unavailable";
    }
    if (entry.status === "unavailable") {
      return "Master unavailable";
    }
    if (entry.active_version_count > 0) {
      return entry.active_version_count === 1
        ? "Mastered"
        : `${entry.active_version_count} masters`;
    }
    if (entry.discarded_version_count > 0) {
      return "Discarded only";
    }
    return (entry.source.roles || []).includes("target")
      ? "Not mastered"
      : "Reference";
  }

  function libraryStatusTone(entry) {
    if (
      !entry ||
      !entry.source ||
      !entry.source.preferred_path ||
      entry.status === "unavailable"
    ) {
      return "danger";
    }
    if (entry.active_version_count > 0) {
      return "good";
    }
    return entry.discarded_version_count > 0 ? "warning" : "neutral";
  }

  function sourceHasSelectedExports(sourceTrackId) {
    return [...state.selectedMasterExports.values()].some(
      (item) => item.source_track_id === sourceTrackId,
    );
  }

  function removeSourceExports(sourceTrackId) {
    for (const [key, item] of state.selectedMasterExports.entries()) {
      if (item.source_track_id === sourceTrackId) {
        state.selectedMasterExports.delete(key);
      }
    }
  }

  function setVersionExportSelection(entry, version, selected) {
    const deliverable = deliverableForVersion(version);
    if (!entry || !version || !deliverable || !deliverable.playable) {
      return;
    }
    removeSourceExports(entry.source.track_id);
    if (selected) {
      state.selectedMasterExports.set(
        masterExportKey(
          entry.source.track_id,
          version.version_id,
          deliverable.ordinal,
        ),
        {
          source_track_id: entry.source.track_id,
          version_id: version.version_id,
          artifact_ordinal: deliverable.ordinal,
        },
      );
    }
  }

  function setSourceExportSelection(entry, selected) {
    removeSourceExports(entry.source.track_id);
    if (!selected) {
      return;
    }
    const versions = activeMasterVersions(entry);
    if (versions.length) {
      setVersionExportSelection(
        entry,
        selectedMasterVersion(entry) || versions[0],
        true,
      );
    }
  }

  function renderMasterExportSelection() {
    const count = state.selectedMasterExports.size;
    $("master-library-selection-summary").textContent =
      count === 0
        ? "No masters selected"
        : `${count} mastered ${count === 1 ? "version" : "versions"} selected`;
    $("master-export-button").disabled = count === 0;
    $("master-export-clear-button").disabled = count === 0;
    const selectable = state.catalogRows.filter(
      (entry) => activeMasterVersions(entry).length > 0,
    );
    const selected = selectable.filter((entry) =>
      sourceHasSelectedExports(entry.source.track_id),
    );
    $("master-library-select-all").checked =
      selectable.length > 0 && selected.length === selectable.length;
    $("master-library-select-all").indeterminate =
      selected.length > 0 && selected.length < selectable.length;
  }

  function libraryListenCell(entry) {
    const cell = element("td", { className: "master-library-actions" });
    const original = entry.source;
    const versions = activeMasterVersions(entry);
    const play = element("button", {
      className: "button quiet small",
      text: "Original",
      type: "button",
      title: "Play original",
      attrs: { "data-library-control": "play-original" },
    });
    play.disabled = !original.preferred_path;
    play.addEventListener("click", (event) => {
      event.stopPropagation();
      playTrack(original, {
        label: `Original · ${original.label || basename(original.preferred_path)}`,
      });
    });
    const compare = element("button", {
      className: "button secondary small",
      text: "Compare",
      type: "button",
      title: "Compare original and chosen master",
      attrs: { "data-library-control": "compare" },
    });
    compare.disabled = !original.preferred_path || versions.length === 0;
    compare.addEventListener("click", (event) => {
      event.stopPropagation();
      selectTrack(original.track_id);
      try {
        compareOriginalWithMaster(entry, selectedMasterVersion(entry) || versions[0], {
          autoplay: true,
          preserveTime: false,
        });
      } catch (error) {
        reportError(error, "Could not start A/B comparison");
      }
    });
    cell.append(play, compare);
    return cell;
  }

  function renderCatalog() {
    const body = $("master-library-body");
    clear(body);
    $("catalog-result-count").textContent =
      `${state.catalogRows.length} ${
        state.catalogRows.length === 1 ? "original" : "originals"
      }`;
    if (!state.catalogRows.length) {
      emptyRow(body, 9, "No original tracks match the current library filters.");
    } else {
      for (const entry of state.catalogRows) {
        const track = entry.source;
        const facts = track.audio_facts || {};
        const versions = activeMasterVersions(entry);
        const latest = versions[0] || null;
        const row = element("tr", {
          className: "master-library-row",
          attrs: {
            tabindex: "0",
            "aria-selected": String(track.track_id === state.selectedTrackId),
            "data-track-id": track.track_id,
          },
        });
        const selectCell = element("td");
        const selectMaster = element("input", {
          type: "checkbox",
          attrs: {
            "aria-label": `Select chosen master for ${
              track.label || basename(track.preferred_path)
            }`,
            "data-library-control": "export",
          },
        });
        selectMaster.disabled = versions.length === 0;
        selectMaster.checked = sourceHasSelectedExports(track.track_id);
        selectMaster.addEventListener("click", (event) => event.stopPropagation());
        selectMaster.addEventListener("change", () => {
          setSourceExportSelection(entry, selectMaster.checked);
          if (selectMaster.checked && versions.length > 1) {
            selectTrack(track.track_id);
            toast(
              "The currently chosen master was added. Choose another version below if needed.",
              "neutral",
            );
          } else {
            renderCatalog();
            restoreLibraryRowFocus(track.track_id, "export");
          }
        });
        selectCell.append(selectMaster);
        row.append(selectCell);
        const statusCell = element("td");
        statusCell.append(
          element("span", {
            className: `state-badge ${libraryStatusTone(entry)}`,
            text: libraryStatusText(entry),
          }),
        );
        row.append(statusCell);
        const nameCell = element("td", { className: "source-name-cell" });
        nameCell.append(
          element("strong", {
            text: track.label || basename(track.preferred_path),
          }),
          element("small", {
            text: basename(track.preferred_path),
            title: track.preferred_path || track.track_id,
          }),
        );
        row.append(nameCell);
        appendTextCell(
          row,
          (track.roles || [])
            .filter((role) => ["target", "reference"].includes(role))
            .map((role) => (role === "target" ? "Input" : "Reference"))
            .join(", "),
        );
        appendTextCell(
          row,
          entry.discarded_version_count
            ? `${entry.active_version_count} active · ${entry.discarded_version_count} discarded`
            : entry.active_version_count,
        );
        appendTextCell(row, latest ? formatDate(latest.created_at) : "—");
        appendTextCell(row, formatDuration(facts.duration_seconds));
        row.append(libraryListenCell(entry));
        appendTextCell(row, track.preferred_path, {
          className: "path-cell",
          title: track.preferred_path,
        });
        const select = () => selectTrack(track.track_id);
        row.addEventListener("click", select);
        row.addEventListener("keydown", (event) => {
          if (event.target !== row) {
            return;
          }
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            if (event.shiftKey && !selectMaster.disabled) {
              setSourceExportSelection(entry, !selectMaster.checked);
              renderCatalog();
            } else {
              select();
            }
          }
        });
        body.append(row);
      }
    }
    renderTrackDetail();
    renderMasterExportSelection();
    renderSourcePickers();
    renderAuditionDock();
  }

  function restoreLibraryRowFocus(trackId, controlName = null) {
    const row = all("#master-library-body .master-library-row").find(
      (candidate) => candidate.dataset.trackId === trackId,
    );
    if (!row) {
      return;
    }
    const control = controlName
      ? row.querySelector(`[data-library-control="${controlName}"]`)
      : row;
    (control || row).focus();
  }

  function restoreVersionFocus(versionId, controlName = null) {
    const row = all("#master-versions-body .version-row").find(
      (candidate) => candidate.dataset.versionId === versionId,
    );
    if (!row) {
      return;
    }
    const control = controlName
      ? row.querySelector(`[data-version-control="${controlName}"]`)
      : row;
    (control || row).focus();
  }

  function restoreSelectedRowFocus(bodyId) {
    const row = document.querySelector(`#${bodyId} tr[aria-selected="true"]`);
    if (row) {
      row.focus();
    }
  }

  function addDefinitionPair(host, label, value, title = "") {
    host.append(
      element("dt", { text: label }),
      element("dd", { text: display(value), title }),
    );
  }

  function renderMasterVersions(entry) {
    const track = entry.source;
    const versions = Array.isArray(entry.versions) ? entry.versions : [];
    const activeVersions = activeMasterVersions(entry);
    const chosenVersion = selectedMasterVersion(entry);
    if (chosenVersion) {
      state.selectedMasterVersionId = chosenVersion.version_id;
      state.preferredMasterBySource.set(track.track_id, chosenVersion.version_id);
    }
    $("master-version-summary").textContent =
      versions.length === 0
        ? "No mastered versions yet."
        : `${entry.active_version_count} active · ${entry.discarded_version_count} discarded`;
    const body = $("master-versions-body");
    clear(body);
    for (const version of versions) {
      const available = version.library_status === "available";
      const deliverable = deliverableForVersion(version);
      const row = element("tr", {
        className: "version-row",
        attrs: {
          tabindex: "0",
          "data-version-id": version.version_id,
          "aria-selected": String(
            chosenVersion && chosenVersion.version_id === version.version_id,
          ),
        },
      });
      const exportCell = element("td");
      const exportVersion = element("input", {
        type: "radio",
        attrs: {
          name: "selected-master-version",
          "aria-label": `Select ${version.display_label} for export`,
          "data-version-control": "export",
        },
      });
      exportVersion.disabled = !available || !deliverable || !deliverable.playable;
      exportVersion.checked = [...state.selectedMasterExports.values()].some(
        (item) =>
          item.source_track_id === track.track_id &&
          item.version_id === version.version_id,
      );
      exportVersion.addEventListener("click", (event) => event.stopPropagation());
      exportVersion.addEventListener("change", () => {
        setVersionExportSelection(entry, version, exportVersion.checked);
        renderCatalog();
        restoreVersionFocus(version.version_id, "export");
      });
      exportCell.append(exportVersion);
      row.append(exportCell);

      const versionCell = element("td", { className: "version-name-cell" });
      versionCell.append(
        element("strong", { text: version.display_label }),
        element("small", {
          text: shortId(version.run_id, 19),
          title: version.run_id,
        }),
      );
      row.append(versionCell);
      appendTextCell(row, formatDate(version.created_at));
      const statusCell = element("td");
      statusCell.append(
        element("span", {
          className: `state-badge ${
            available
              ? "good"
              : version.library_status === "discarded"
                ? "warning"
                : "danger"
          }`,
          text: version.library_status,
        }),
      );
      row.append(statusCell);

      const outputCell = element("td");
      const playableOutputs = (version.deliverables || []).filter(
        (item) => item.playable,
      );
      if (playableOutputs.length > 1) {
        const picker = element("select", {
          attrs: {
            "aria-label": `Output for ${version.display_label}`,
            "data-version-control": "output",
          },
        });
        for (const output of playableOutputs) {
          picker.append(
            option(
              output.ordinal,
              output.mode || output.manifest_role || basename(output.path),
            ),
          );
        }
        picker.value = String(deliverable && deliverable.ordinal);
        picker.addEventListener("click", (event) => event.stopPropagation());
        picker.addEventListener("change", () => {
          state.preferredDeliverableByVersion.set(
            version.version_id,
            Number(picker.value),
          );
          if (exportVersion.checked) {
            setVersionExportSelection(entry, version, true);
          }
          if (version.version_id === state.selectedMasterVersionId) {
            try {
              compareOriginalWithMaster(entry, version, {
                autoplay: false,
                preserveTime: true,
                silent: true,
                startWithMaster: state.audition.active === "b",
              });
            } catch (error) {
              console.warn("Comparison output could not be updated", error);
            }
          }
          renderTrackDetail();
          renderMasterExportSelection();
          restoreVersionFocus(version.version_id, "output");
        });
        outputCell.append(picker);
      } else {
        outputCell.textContent = deliverable
          ? deliverable.mode || deliverable.manifest_role || basename(deliverable.path)
          : "No playable output";
      }
      row.append(outputCell);
      appendTextCell(row, (version.deliverables || []).length);

      const reviewCell = element("td", { className: "version-actions" });
      const playMaster = element("button", {
        className: "button quiet small",
        text: "Play",
        type: "button",
        attrs: { "data-version-control": "play" },
      });
      playMaster.disabled = !available || !deliverable || !deliverable.playable;
      playMaster.addEventListener("click", (event) => {
        event.stopPropagation();
        playTrack(deliverable, {
          label: `Master · ${version.display_label}`,
          path: deliverable && deliverable.path,
        });
      });
      const compare = element("button", {
        className: "button secondary small",
        text: "Compare",
        type: "button",
        attrs: { "data-version-control": "compare" },
      });
      compare.disabled =
        !track.preferred_path || !available || !deliverable || !deliverable.playable;
      compare.addEventListener("click", (event) => {
        event.stopPropagation();
        try {
          compareOriginalWithMaster(entry, version, {
            autoplay: true,
            preserveTime: false,
          });
          renderTrackDetail();
          restoreVersionFocus(version.version_id, "compare");
        } catch (error) {
          reportError(error, "Could not start A/B comparison");
        }
      });
      reviewCell.append(playMaster, compare);
      row.append(reviewCell);

      const actions = element("td", { className: "version-actions" });
      const reveal = element("button", {
        className: "button quiet small",
        text: "Show",
        type: "button",
        attrs: { "data-version-control": "show" },
      });
      reveal.disabled = !deliverable || !deliverable.path;
      reveal.addEventListener("click", (event) => {
        event.stopPropagation();
        if (deliverable) {
          openFolder(deliverable.path);
        }
      });
      const discarded = version.library_status === "discarded";
      const lifecycle = element("button", {
        className: `button ${discarded ? "secondary" : "danger"} small`,
        text: discarded ? "Restore" : "Discard…",
        type: "button",
        attrs: { "data-version-control": "lifecycle" },
      });
      lifecycle.disabled = discarded ? !version.can_restore : !version.can_discard;
      lifecycle.addEventListener("click", (event) => {
        event.stopPropagation();
        if (discarded) {
          restoreMasterVersion(entry, version);
        } else {
          discardMasterVersion(entry, version);
        }
      });
      actions.append(reveal, lifecycle);
      row.append(actions);

      const choose = () => {
        if (!available) {
          return;
        }
        const restoreFocus = document.activeElement === row;
        state.selectedMasterVersionId = version.version_id;
        state.preferredMasterBySource.set(track.track_id, version.version_id);
        try {
          compareOriginalWithMaster(entry, version, {
            autoplay: false,
            preserveTime: true,
            silent: true,
            startWithMaster: state.audition.active === "b",
          });
        } catch (error) {
          console.warn("Comparison version could not be updated", error);
        }
        renderTrackDetail();
        if (restoreFocus) {
          restoreVersionFocus(version.version_id);
        }
      };
      row.addEventListener("click", choose);
      row.addEventListener("keydown", (event) => {
        if (event.target !== row) {
          return;
        }
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          choose();
        }
      });
      body.append(row);
    }
    if (!versions.length) {
      emptyRow(body, 8, "No mastered versions for this original.");
    }

    const selectedExport = [...state.selectedMasterExports.values()].find(
      (item) => item.source_track_id === track.track_id,
    );
    $("master-version-select-all").checked = Boolean(
      chosenVersion &&
        selectedExport &&
        selectedExport.version_id === chosenVersion.version_id,
    );
    $("master-version-select-all").indeterminate = false;
  }

  function renderTrackDetail() {
    const entry = selectedLibrarySource();
    const track = entry && entry.source;
    $("track-detail-empty").hidden = Boolean(entry);
    $("track-detail-content").hidden = !entry;
    if (!entry || !track) {
      $("track-detail-title").textContent = "Nothing selected";
      $("track-detail-state").textContent = "—";
      $("track-detail-state").className = "state-badge neutral";
      emptyRow($("master-versions-body"), 8, "Select an original track.");
      return;
    }
    $("track-detail-title").textContent = track.label || basename(track.preferred_path);
    $("track-detail-state").textContent = libraryStatusText(entry);
    $("track-detail-state").className =
      `state-badge ${libraryStatusTone(entry)}`;
    const factsHost = $("track-detail-facts");
    clear(factsHost);
    const audio = track.audio_facts || {};
    addDefinitionPair(
      factsHost,
      "Track ID",
      shortId(track.track_id, 13),
      track.track_id,
    );
    addDefinitionPair(factsHost, "Roles", (track.roles || []).join(", "));
    addDefinitionPair(factsHost, "Format", [audio.format, audio.subtype].filter(Boolean).join(" / "));
    addDefinitionPair(factsHost, "Rate", audio.sample_rate ? `${audio.sample_rate} Hz` : "—");
    addDefinitionPair(factsHost, "Channels", audio.channels);
    addDefinitionPair(factsHost, "Duration", formatDuration(audio.duration_seconds));
    addDefinitionPair(factsHost, "Active masters", entry.active_version_count);
    addDefinitionPair(factsHost, "Discarded", entry.discarded_version_count);

    const locationBody = $("track-locations-body");
    clear(locationBody);
    for (const location of track.locations || []) {
      const row = element("tr");
      appendTextCell(row, location.state);
      appendTextCell(row, location.path, { title: location.path });
      appendTextCell(row, formatDate(location.last_verified_at));
      locationBody.append(row);
    }
    if (!(track.locations || []).length) {
      emptyRow(locationBody, 3, "No cataloged locations.");
    }
    renderMasterVersions(entry);
    const activeVersions = activeMasterVersions(entry);
    $("track-role-target-button").disabled =
      !track.preferred_path || !(track.roles || []).includes("target");
    $("track-role-reference-button").disabled =
      !track.preferred_path || !(track.roles || []).includes("reference");
    $("track-play-button").disabled = !track.preferred_path;
    $("track-compare-button").disabled =
      !track.preferred_path || activeVersions.length === 0;
    $("track-compare-button").textContent =
      activeVersions.length > 1
        ? "Compare selected master"
        : "Compare original and master";
    $("track-archive-button").hidden = Boolean(track.archived);
    $("track-restore-button").hidden = !track.archived;
    renderMasterExportSelection();
  }

  function selectTrack(trackId) {
    const focused = document.activeElement;
    const focusedRow =
      focused instanceof HTMLElement && focused.closest(".master-library-row");
    const restoreFocus = Boolean(focusedRow);
    const focusedControl =
      restoreFocus && focused !== focusedRow
        ? focused.dataset.libraryControl || null
        : null;
    const changed = state.selectedTrackId !== trackId;
    const priorComparisonSourceId = state.automaticComparisonSourceId;
    if (
      changed &&
      priorComparisonSourceId &&
      priorComparisonSourceId !== trackId
    ) {
      clearAudition();
    }
    state.selectedTrackId = trackId;
    const entry = selectedLibrarySource();
    if (entry) {
      const versions = activeMasterVersions(entry);
      const remembered = state.preferredMasterBySource.get(trackId);
      const version =
        versions.find((candidate) => candidate.version_id === remembered) ||
        versions[0] ||
        null;
      state.selectedMasterVersionId = version && version.version_id;
    }
    renderCatalog();
    if (restoreFocus) {
      restoreLibraryRowFocus(trackId, focusedControl);
    }
    prepareAutomaticComparison(entry);
  }

  function renderReferenceSets() {
    const body = $("reference-sets-body");
    clear(body);
    $("set-result-count").textContent =
      `${state.referenceSets.length} named ${state.referenceSets.length === 1 ? "set" : "sets"}`;
    if (!state.referenceSets.length) {
      emptyRow(body, 4, "No named reference sets yet.");
    } else {
      for (const referenceSet of state.referenceSets) {
        const row = element("tr", {
          attrs: {
            tabindex: "0",
            "aria-selected": String(referenceSet.set_id === state.selectedSetId),
          },
        });
        appendTextCell(row, referenceSet.name);
        appendTextCell(row, referenceSet.members.length);
        appendTextCell(row, formatDate(referenceSet.updated_at));
        appendTextCell(row, shortId(referenceSet.set_id, 18), {
          code: true,
          title: referenceSet.set_id,
        });
        const select = () => {
          const restoreFocus = document.activeElement === row;
          state.selectedSetId = referenceSet.set_id;
          renderReferenceSets();
          if (restoreFocus) {
            restoreSelectedRowFocus("reference-sets-body");
          }
        };
        row.addEventListener("click", select);
        row.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            select();
          }
        });
        body.append(row);
      }
    }
    renderSetDetail();
    renderSourcePickers();
  }

  function renderSetDetail() {
    const referenceSet = selectedSet();
    $("set-detail-empty").hidden = Boolean(referenceSet);
    $("set-detail-content").hidden = !referenceSet;
    if (!referenceSet) {
      $("set-detail-title").textContent = "Nothing selected";
      return;
    }
    $("set-detail-title").textContent = referenceSet.name;
    const levelTotal = referenceSet.members.reduce(
      (sum, member) => sum + Number(member.level_weight),
      0,
    );
    const frequencyTotal = referenceSet.members.reduce(
      (sum, member) => sum + Number(member.frequency_weight),
      0,
    );
    $("set-level-total").textContent = formatNumber(levelTotal, 6);
    $("set-frequency-total").textContent = formatNumber(frequencyTotal, 6);
    const body = $("set-members-body");
    clear(body);
    referenceSet.members.forEach((member, index) => {
      const row = element("tr");
      appendTextCell(row, index + 1);
      appendTextCell(row, member.label || basename(member.path), {
        title: `${display(member.path)}\n${member.track_id}`,
      });
      appendTextCell(row, formatNumber(member.level_weight, 6));
      appendTextCell(row, `${(Number(member.normalized_level_weight) * 100).toFixed(2)}%`);
      appendTextCell(row, formatNumber(member.frequency_weight, 6));
      appendTextCell(
        row,
        `${(Number(member.normalized_frequency_weight) * 100).toFixed(2)}%`,
      );
      body.append(row);
    });
    if (!referenceSet.members.length) {
      emptyRow(body, 6, "This set has no references. Use Edit members to add some.");
    }
  }

  function renderRuns() {
    const body = $("runs-body");
    clear(body);
    $("runs-result-count").textContent =
      `${state.runs.length} audited ${state.runs.length === 1 ? "run" : "runs"}`;
    if (!state.runs.length) {
      emptyRow(body, 6, "No manifest-backed runs have been indexed.");
    } else {
      for (const run of state.runs) {
        const row = element("tr", {
          attrs: {
            tabindex: "0",
            "aria-selected": String(run.run_id === state.selectedRunId),
          },
        });
        appendTextCell(row, run.status);
        appendTextCell(row, shortId(run.run_id, 22), {
          code: true,
          title: run.run_id,
        });
        appendTextCell(row, shortId(run.selection_id, 18), {
          code: true,
          title: run.selection_id,
        });
        appendTextCell(row, formatDate(run.created_at));
        appendTextCell(row, formatDate(run.updated_at));
        appendTextCell(row, run.manifest_path ? basename(run.manifest_path) : "—", {
          title: run.manifest_path,
        });
        const select = () =>
          selectRun(run.run_id, document.activeElement === row);
        row.addEventListener("click", select);
        row.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            select();
          }
        });
        body.append(row);
      }
    }
    if (state.selectedRunId && !state.runs.some((run) => run.run_id === state.selectedRunId)) {
      state.selectedRunId = null;
      state.selectedRun = null;
      state.runArtifacts = [];
      state.runManifest = null;
      state.runManifestError = null;
    }
    renderRunDetail();
  }

  async function selectRun(runId, restoreFocus = false) {
    state.selectedRunId = runId;
    state.selectedRun = state.runs.find((run) => run.run_id === runId) || null;
    state.runArtifacts = [];
    state.runManifest = null;
    state.runManifestError = null;
    renderRuns();
    if (restoreFocus) {
      restoreSelectedRowFocus("runs-body");
    }
    const [artifactsResult, manifestResult] = await Promise.allSettled([
      api(`/api/runs/${encodeURIComponent(runId)}/artifacts`),
      api(`/api/runs/${encodeURIComponent(runId)}/manifest`),
    ]);
    if (state.selectedRunId !== runId) {
      return;
    }
    if (artifactsResult.status === "fulfilled") {
      state.runArtifacts = artifactsResult.value;
    } else {
      reportError(artifactsResult.reason, "Could not load the catalog artifact inventory");
    }
    if (manifestResult.status === "fulfilled") {
      state.runManifest = manifestResult.value;
    } else {
      state.runManifestError =
        manifestResult.reason instanceof Error
          ? manifestResult.reason.message
          : String(manifestResult.reason);
      toast(
        "The manifest could not be opened. Catalog-indexed artifacts remain visible; use recovery only after inspecting the retained files and logs.",
        "warning",
        11000,
      );
    }
    renderRunDetail();
  }

  function renderRunDetail() {
    const run = state.selectedRun;
    $("run-detail-empty").hidden = Boolean(run);
    $("run-detail-content").hidden = !run;
    $("run-open-folder-button").disabled = !run || !run.manifest_path;
    $("run-recover-button").disabled = !run || !run.manifest_path;
    if (!run) {
      $("run-detail-title").textContent = "Nothing selected";
      $("run-detail-subtitle").textContent = "Choose a run to inspect its evidence chain.";
      return;
    }
    $("run-detail-title").textContent = run.run_id;
    $("run-detail-subtitle").textContent =
      `${run.status} · ${run.artifact_count || state.runArtifacts.length} indexed artifacts · ${formatDate(run.updated_at)}`;
    const artifactBody = $("run-artifacts-body");
    clear(artifactBody);
    for (const artifact of state.runArtifacts) {
      const row = element("tr");
      appendTextCell(
        row,
        (artifact.metadata && artifact.metadata.manifest_role) || artifact.role,
      );
      appendTextCell(row, artifact.ordinal);
      appendTextCell(row, artifact.state);
      appendTextCell(row, artifact.media_type);
      appendTextCell(row, formatBytes(artifact.size_bytes));
      const catalogTrack = state.tracks.find(
        (track) => track.track_id === artifact.track_id,
      );
      if (
        artifact.track_id &&
        typeof artifact.media_type === "string" &&
        artifact.media_type.startsWith("audio/")
      ) {
        row.append(
          auditionActionCell(
            catalogTrack || {
              track_id: artifact.track_id,
              label: basename(artifact.path),
              path: artifact.path,
            },
            { label: basename(artifact.path), path: artifact.path },
          ),
        );
      } else {
        appendTextCell(row, "—");
      }
      appendTextCell(row, artifact.path, { title: artifact.path });
      appendTextCell(row, shortHash(artifact.sha256), {
        code: true,
        title: artifact.sha256,
      });
      artifactBody.append(row);
    }
    if (!state.runArtifacts.length) {
      emptyRow(artifactBody, 8, "Loading artifacts, or none were indexed.");
    }
    $("manifest-raw").textContent = state.runManifest
      ? safeJson(state.runManifest)
      : state.runManifestError
        ? `Manifest unavailable:\n${state.runManifestError}\n\nThe artifact inventory remains independently cataloged.`
        : "Loading manifest…";
    const overview = $("manifest-overview");
    clear(overview);
    if (state.runManifest) {
      const manifest = state.runManifest;
      const facts = [
        ["Run ID", manifest.run_id],
        ["Status", manifest.status],
        ["Engine", manifest.engine && (manifest.engine.engine_id || manifest.engine)],
        ["Started", manifest.started_at],
        ["Finished", manifest.finished_at],
        ["Inputs", Array.isArray(manifest.inputs) ? manifest.inputs.length : 0],
        ["Outputs", Array.isArray(manifest.outputs) ? manifest.outputs.length : 0],
        ["Warnings", Array.isArray(manifest.warnings) ? manifest.warnings.length : 0],
      ];
      for (const [label, value] of facts) {
        const card = element("div", { className: "selected-facts" });
        card.append(element("small", { text: label }), element("br"), element("strong", {
          text: display(value),
        }));
        overview.append(card);
      }
    } else if (state.runManifestError) {
      overview.append(
        element("div", {
          className: "notice danger",
          text: `Manifest unavailable: ${state.runManifestError}`,
        }),
      );
    }
  }

  function eventKey(event, operationId, index) {
    return [
      operationId,
      display(event.sequence, index),
      display(event.timestamp, ""),
      display(event.code, ""),
      display(event.message, ""),
    ].join("|");
  }

  function mergeTaskEvents(task) {
    if (!task) {
      return;
    }
    const existing = new Set(state.events.map((entry) => entry._key));
    (task.events || []).forEach((event, index) => {
      const key = eventKey(event, task.operation_id, index);
      if (!existing.has(key) && !state.hiddenEventKeys.has(key)) {
        state.events.push({ ...event, _key: key, _operation_id: task.operation_id });
        existing.add(key);
      }
    });
    if (task.state === "failed" && task.error) {
      const failure = {
        timestamp: task.finished_at || new Date().toISOString(),
        level: "error",
        sequence: null,
        stage: task.stage || "portal",
        code: "MMT-E-PORTAL-OPERATION",
        message: task.error.message || "Portal operation failed.",
        job_id: task.operation_id,
        context: task.error,
      };
      const key = eventKey(failure, task.operation_id, "failure");
      if (!existing.has(key) && !state.hiddenEventKeys.has(key)) {
        state.events.push({ ...failure, _key: key, _operation_id: task.operation_id });
      }
    }
    if (state.events.length > 5000) {
      state.events.splice(0, state.events.length - 5000);
    }
    renderEvents();
  }

  function levelRank(value) {
    return {
      debug: 10,
      info: 20,
      warning: 30,
      error: 40,
      critical: 50,
    }[String(value || "info").toLowerCase()] || 20;
  }

  function filteredEvents() {
    const query = $("events-search").value.trim().toLowerCase();
    const stage = $("events-stage-filter").value;
    const minimum = levelRank($("events-level-filter").value);
    return state.events.filter((event) => {
      if (levelRank(event.level) < minimum) {
        return false;
      }
      if (stage && event.stage !== stage) {
        return false;
      }
      if (
        query &&
        !safeJson(event).toLowerCase().includes(query)
      ) {
        return false;
      }
      return true;
    });
  }

  function renderEvents() {
    const stageFilter = $("events-stage-filter");
    const previousStage = stageFilter.value;
    const stages = Array.from(
      new Set(state.events.map((event) => event.stage).filter(Boolean)),
    ).sort();
    clear(stageFilter);
    stageFilter.append(option("", "All stages"));
    stages.forEach((stage) => stageFilter.append(option(stage, stage)));
    if (stages.includes(previousStage)) {
      stageFilter.value = previousStage;
    }

    const events = filteredEvents();
    const body = $("events-body");
    clear(body);
    $("events-visible-count").textContent =
      `${events.length} ${events.length === 1 ? "event" : "events"}`;
    $("event-count-badge").textContent = String(state.events.length);
    $("event-count-badge").hidden = state.events.length === 0;
    if (!events.length) {
      emptyRow(body, 7, "No events match the current view.");
    } else {
      for (const event of events) {
        const row = element("tr", {
          attrs: {
            tabindex: "0",
            "aria-selected": String(event._key === state.selectedEventKey),
          },
        });
        appendTextCell(row, formatDate(event.timestamp));
        appendTextCell(row, event.level);
        appendTextCell(row, event.sequence);
        appendTextCell(row, event.stage);
        appendTextCell(row, event.code, { code: true });
        appendTextCell(row, event.message);
        appendTextCell(row, shortId(event.job_id || event._operation_id, 16), {
          code: true,
          title: event.job_id || event._operation_id,
        });
        const select = () => {
          const restoreFocus = document.activeElement === row;
          state.selectedEventKey = event._key;
          renderEvents();
          renderEventDetail();
          if (restoreFocus) {
            restoreSelectedRowFocus("events-body");
          }
        };
        row.addEventListener("click", select);
        row.addEventListener("keydown", (keyboardEvent) => {
          if (keyboardEvent.key === "Enter" || keyboardEvent.key === " ") {
            keyboardEvent.preventDefault();
            select();
          }
        });
        body.append(row);
      }
    }
    renderEventDetail();
    if ($("events-autoscroll").checked && events.length) {
      $("events-table-shell").scrollTop = $("events-table-shell").scrollHeight;
    }
  }

  function renderEventDetail() {
    const selected = state.events.find((event) => event._key === state.selectedEventKey);
    $("event-detail-empty").hidden = Boolean(selected);
    $("event-detail-json").hidden = !selected;
    if (!selected) {
      $("event-detail-title").textContent = "Nothing selected";
      $("event-detail-json").textContent = "";
      return;
    }
    $("event-detail-title").textContent = selected.code || "Event";
    const copy = { ...selected };
    delete copy._key;
    delete copy._operation_id;
    $("event-detail-json").textContent = safeJson(copy);
  }

  function renderCapabilities() {
    const body = $("capabilities-body");
    clear(body);
    if (!state.capabilities) {
      emptyRow(body, 3, "Capability data is unavailable.");
      return;
    }
    const upstream = state.capabilities["upstream-matchering-2.0.6"] || {};
    const native = state.capabilities["music-mastering-tools-native"] || {};
    const keys = Array.from(new Set([...Object.keys(upstream), ...Object.keys(native)]));
    const preferred = [
      "implementation_status",
      "runnable",
      "maximum_references",
      "independent_reference_weights",
      "partial_matching_amount",
      "ebu_r128_loudness",
      "spectral_gain_ceiling",
      "sample_peak_limiter",
      "true_peak_limiter",
      "external_limiter",
      "dither",
      "previews",
      "thread_safe",
      "planned_capabilities",
      "engine_version",
    ];
    keys.sort((first, second) => {
      const left = preferred.indexOf(first);
      const right = preferred.indexOf(second);
      return (left < 0 ? 999 : left) - (right < 0 ? 999 : right) ||
        first.localeCompare(second);
    });
    for (const key of keys.filter((item) => item !== "engine_id")) {
      const row = element("tr");
      appendTextCell(row, key.replaceAll("_", " "));
      appendTextCell(row, capabilityValue(upstream[key]));
      appendTextCell(row, capabilityValue(native[key]));
      body.append(row);
    }
  }

  function capabilityValue(value) {
    if (typeof value === "boolean") {
      return value ? "Yes" : "No";
    }
    if (Array.isArray(value)) {
      return value.length ? value.join(", ") : "None";
    }
    return display(value);
  }

  function renderDiagnostics() {
    renderCapabilities();
    const report = state.diagnostics;
    const body = $("doctor-body");
    clear(body);
    if (!report) {
      $("doctor-badge").textContent = "Not checked";
      $("doctor-badge").className = "state-badge neutral";
      $("doctor-summary").textContent = "Run diagnostics to inspect this environment.";
      emptyRow(body, 4, "No diagnostic report loaded.");
      return;
    }
    const healthy = Boolean(report.healthy);
    const tone = healthy ? (report.has_warnings ? "warning" : "good") : "danger";
    $("doctor-badge").textContent = healthy
      ? report.has_warnings
        ? "Warnings"
        : "Healthy"
      : "Failed";
    $("doctor-badge").className = `state-badge ${tone}`;
    $("doctor-summary").className = `diagnostic-summary ${tone}`;
    $("doctor-summary").textContent =
      `${healthy ? "Required checks passed" : "One or more required checks failed"} · generated ${formatDate(report.generated_at)}`;
    for (const check of report.checks || []) {
      const row = element("tr");
      appendTextCell(row, check.status);
      appendTextCell(row, check.name);
      appendTextCell(row, check.version || check.summary);
      appendTextCell(
        row,
        [check.summary, check.remediation].filter(Boolean).join(" · "),
      );
      body.append(row);
    }
    if (!(report.checks || []).length) {
      emptyRow(body, 4, "The doctor returned no checks.");
    }
    $("metric-health").textContent = healthy
      ? report.has_warnings
        ? "Warnings"
        : "Ready"
      : "Failed";
    $("metric-health-detail").textContent = healthy
      ? "Required dependencies available"
      : "Review Activity › System";
  }

  function renderLogs() {
    const body = $("logs-body");
    clear(body);
    if (!state.logs.length) {
      emptyRow(body, 3, "No retained launcher or portal logs.");
    } else {
      for (const log of state.logs) {
        const row = element("tr", {
          attrs: {
            tabindex: "0",
            "aria-selected": String(state.selectedLog && state.selectedLog.name === log.name),
          },
        });
        appendTextCell(row, log.name);
        appendTextCell(row, formatBytes(log.size_bytes));
        appendTextCell(row, formatNanosecondDate(log.modified_ns));
        const select = () =>
          loadLog(log.name, document.activeElement === row);
        row.addEventListener("click", select);
        row.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            select();
          }
        });
        body.append(row);
      }
    }
    $("log-open-folder-button").disabled = !state.selectedLog;
  }

  async function loadLog(name, restoreFocus = false) {
    try {
      const log = await api(`/api/logs/${encodeURIComponent(name)}`);
      state.selectedLog = log;
      $("log-view-title").textContent =
        `${log.name}${log.truncated ? " · tail view (truncated)" : ""}`;
      $("log-view").textContent = log.text || "This log is empty.";
      renderLogs();
      if (restoreFocus) {
        restoreSelectedRowFocus("logs-body");
      }
    } catch (error) {
      reportError(error, "Could not load session log");
    }
  }

  function recoverTaskTargets(task) {
    if (!task) {
      return [];
    }
    const targetIds = [];
    const addTargetId = (value) => {
      if (typeof value === "string" && value && !targetIds.includes(value)) {
        targetIds.push(value);
      }
    };
    const result = task.result || {};
    if (Array.isArray(result.targets)) {
      result.targets.forEach((item) => addTargetId(item && item.target_id));
    }
    if (result.prepared) {
      addTargetId(result.prepared.target_id);
    }
    (Array.isArray(task.events) ? task.events : []).forEach((event) => {
      const context = event && event.context;
      if (!context) {
        return;
      }
      if (Array.isArray(context.target_ids)) {
        context.target_ids.forEach(addTargetId);
      }
      addTargetId(context.target_id);
    });
    return targetIds.map((trackId) => {
      const track = state.tracks.find((candidate) => candidate.track_id === trackId);
      return track
        ? jobTargetFromTrack(track)
        : {
            track_id: trackId,
            label: shortId(trackId, 22),
            path: "",
            audio_facts: {},
          };
    });
  }

  function renderTaskTargetProgress(task) {
    const host = $("task-target-progress");
    const ownsTask = Boolean(
      task &&
        state.jobTaskId === task.operation_id &&
        state.jobTaskTargets.length,
    );
    clear(host);
    host.hidden = !ownsTask;
    if (!ownsTask) {
      return;
    }
    const results = new Map(
      (
        (task.result && Array.isArray(task.result.targets) && task.result.targets) ||
        []
      ).map((item) => [item.target_id, item]),
    );
    const events = Array.isArray(task.events) ? task.events : [];
    state.jobTaskTargets.forEach((target, index) => {
      const result = results.get(target.track_id);
      const targetEvents = events.filter(
        (event) => event.context && event.context.target_id === target.track_id,
      );
      const codes = new Set(targetEvents.map((event) => event.code));
      let status = "Queued";
      let tone = "neutral";
      if (result && result.state === "failed") {
        status = "Failed";
        tone = "danger";
      } else if (result && result.state === "succeeded") {
        status = "Complete";
        tone = "good";
      } else if (codes.has("MMT-E-PORTAL-BATCH-TARGET-FAILED")) {
        status = "Failed";
        tone = "danger";
      } else if (codes.has("MMT-I-PORTAL-BATCH-TARGET-COMPLETE")) {
        status = "Complete";
        tone = "good";
      } else if (codes.has("MMT-I-PORTAL-BATCH-TARGET-START")) {
        status = "Running";
        tone = "warning";
      } else if (state.jobTaskTargets.length === 1 && task.state === "running") {
        status = "Running";
        tone = "warning";
      } else if (state.jobTaskTargets.length === 1 && task.state === "succeeded") {
        status = "Complete";
        tone = "good";
      } else if (task.state === "failed") {
        status = "Failed";
        tone = "danger";
      }
      host.append(
        element("div", { className: "task-target-row" }, [
          element("span", { className: "task-target-index", text: index + 1 }),
          element("strong", { text: target.label }),
          element("span", {
            className: `state-badge ${tone}`,
            text: status,
          }),
        ]),
      );
    });
  }

  function renderTask() {
    const task = state.currentTask;
    const active = task && ["queued", "running"].includes(task.state);
    const recoveredTaskTargets = recoverTaskTargets(task);
    const isJobTask = Boolean(
      task && ["render", "dry-run"].includes(task.kind),
    );
    if (
      isJobTask &&
      (active || recoveredTaskTargets.length) &&
      state.jobTaskId !== task.operation_id
    ) {
      state.jobTaskId = task.operation_id;
      state.jobTaskTargets = recoveredTaskTargets;
    } else if (
      isJobTask &&
      state.jobTaskId === task.operation_id &&
      !state.jobTaskTargets.length &&
      recoveredTaskTargets.length
    ) {
      state.jobTaskTargets = recoveredTaskTargets;
    }
    const ready = renderJobReadiness();
    const batch = task && task.result && task.result.batch;
    const batchStatus = batch && batch.status;
    const displayState =
      task && task.state === "succeeded" && batchStatus && batchStatus !== "succeeded"
        ? batchStatus === "partial_failure"
          ? "partially completed"
          : "batch failed"
        : task && task.state;
    const terminalTone =
      task && task.state === "failed"
        ? "danger"
        : batchStatus === "failed"
          ? "danger"
          : batchStatus === "partial_failure"
            ? "warning"
            : task && task.state === "succeeded"
              ? "good"
              : "neutral";
    renderTaskTargetProgress(task);
    const reviewable = Boolean(
      task &&
        state.jobTaskId === task.operation_id &&
        task.kind === "render" &&
        task.state === "succeeded" &&
        (!batch || Number(batch.success_count) > 0),
    );
    $("review-masters-button").hidden = !reviewable;
    const commandHost = $("command-task-state");
    clear(commandHost);
    commandHost.append(
      element("span", {
        className: `status-dot ${active ? "run" : terminalTone}`,
      }),
      element("span", {
        text: !task
          ? "Idle"
          : active
            ? `${task.kind}: ${task.stage || task.state}`
            : `${task.kind}: ${displayState}`,
      }),
    );
    $("status-task").textContent =
      `Worker: ${!task ? "idle" : active ? `${task.state} · ${task.stage || "starting"}` : "idle"}`;
    $("dashboard-task").hidden = Boolean(task);
    $("dashboard-task-progress").hidden = !task;
    if (!task) {
      return;
    }
    if (active) {
      $("validation-badge").textContent = task.state === "queued" ? "Queued" : "Running";
      $("validation-badge").className = "state-badge warning";
      $("validation-summary").textContent = `${task.kind} · ${task.stage || task.state}`;
    } else if (
      task &&
      state.jobTaskId === task.operation_id &&
      !state.jobDirty
    ) {
      const badge = $("validation-badge");
      const summary = $("validation-summary");
      if (task.state === "failed" || batchStatus === "failed") {
        badge.textContent = "Failed";
        badge.className = "state-badge danger";
        summary.textContent =
          task.state === "failed"
            ? task.error && task.error.message
              ? task.error.message
              : "The operation failed. Inspect Activity for the complete cause."
            : `All ${batch.failure_count} inputs failed. Inspect Activity for each cause.`;
      } else if (batchStatus === "partial_failure") {
        badge.textContent = "Partial";
        badge.className = "state-badge warning";
        summary.textContent = `${batch.success_count} masters completed; ${batch.failure_count} failed. Successful outputs are available in Library.`;
      } else if (task.state === "succeeded") {
        badge.textContent = "Complete";
        badge.className = "state-badge good";
        const completedCount = Number(batch && batch.success_count);
        summary.textContent =
          task.kind === "dry-run"
            ? `Dry run completed${Number.isFinite(completedCount) ? ` for ${completedCount} ${completedCount === 1 ? "mix" : "mixes"}` : ""}.`
            : `${Number.isFinite(completedCount) ? `${completedCount} ${completedCount === 1 ? "master" : "masters"}` : "Mastering"} completed. Review outputs in Library or inspect evidence in Activity.`;
      }
    }
    $("dashboard-task-kind").textContent = task.kind;
    $("dashboard-task-title").textContent =
      active ? "Mastering operation in progress" : `Operation ${displayState}`;
    $("dashboard-task-status").textContent = displayState;
    $("dashboard-task-status").className =
      `state-badge ${active ? "warning" : terminalTone}`;
    $("dashboard-task-stage").textContent = task.stage || task.state;
    $("dashboard-task-id").textContent = task.operation_id;
    $("dashboard-task-message").textContent =
      task.state === "failed"
        ? `${task.error && task.error.message ? task.error.message : "Operation failed."} Full failure detail is retained in Activity › Event log and the portal log.`
        : batchStatus === "failed"
          ? `All ${batch.failure_count} queued inputs failed. Preparation and failure evidence remain available in Activity.`
          : batchStatus === "partial_failure"
            ? `${batch.success_count} inputs completed and ${batch.failure_count} failed. Successful masters were retained; inspect Activity › Event log for each failed input.`
        : task.state === "succeeded"
          ? "The operation completed and its durable evidence is available in Activity › History."
          : "The browser remains responsive. Keep the portal open until this operation finishes.";
    for (const button of [
      $("job-validate-button"),
      $("dry-run-button"),
      $("render-button"),
      ...all('[data-command="dry-run"], [data-command="render"]'),
    ]) {
      button.disabled = Boolean(active) || !ready;
    }
  }

  async function reviewCompletedMasters() {
    const task = state.currentTask;
    if (!task || state.jobTaskId !== task.operation_id) {
      return;
    }
    const completedIds =
      task.result && Array.isArray(task.result.targets)
        ? task.result.targets
            .filter((item) => item.state === "succeeded")
            .map((item) => item.target_id)
        : state.jobTaskTargets.map((target) => target.track_id);
    if (!completedIds.length) {
      return;
    }
    activateTab("catalog");
    $("catalog-search").value = "";
    $("catalog-role-filter").value = "mastered";
    await refreshCatalog();
    if (
      state.masterLibrary.sources.some(
        (entry) => entry.source && entry.source.track_id === completedIds[0],
      )
    ) {
      selectTrack(completedIds[0]);
    }
  }

  function renderValidation(result) {
    const batchTargets = Array.isArray(result.targets) ? result.targets : [];
    const isBatch = result.kind === "batch-validation" && batchTargets.length > 0;
    let report = result.report || {};
    let prepared = result.prepared || {};
    if (isBatch) {
      const issues = [];
      const audioFacts = new Map();
      let errorCount = 0;
      let warningCount = 0;
      for (const item of batchTargets) {
        const itemReport = item.report || {};
        errorCount += Number(itemReport.error_count || 0);
        warningCount += Number(itemReport.warning_count || 0);
        for (const issue of itemReport.issues || []) {
          issues.push({
            ...issue,
            message: `Input ${Number(item.index) + 1}: ${issue.message}`,
          });
        }
        if (item.error) {
          errorCount += 1;
          issues.push({
            severity: "error",
            code: item.error.code || item.error.exception_type || "batch_target_error",
            message: `Input ${Number(item.index) + 1}: ${item.error.message}`,
            path: item.target_id,
            remediation: "Repair this catalog input, then validate the queue again.",
          });
        }
        for (const facts of itemReport.audio_facts || []) {
          audioFacts.set(facts.path, facts);
        }
      }
      report = {
        ok: Boolean(result.batch && result.batch.ok),
        error_count: errorCount,
        warning_count: warningCount,
        issues,
        audio_facts: [...audioFacts.values()],
      };
      prepared =
        (batchTargets.find((item) => item.prepared) || {}).prepared || {};
    }
    const ok = Boolean(report.ok);
    $("validation-results").hidden = false;
    $("validation-results").open = !ok;
    $("validation-badge").textContent = ok ? "Passed" : "Blocked";
    $("validation-badge").className = `state-badge ${ok ? "good" : "danger"}`;
    $("validation-summary").textContent = isBatch
      ? ok
        ? `${result.batch.valid_count} inputs passed preflight with ${report.warning_count || 0} warning(s).`
        : `${result.batch.invalid_count} of ${result.batch.target_count} inputs are blocked; successful inputs were still checked.`
      : ok
        ? `Preflight passed with ${report.warning_count || 0} warning(s). Engine: ${prepared.engine}.`
        : `Preflight found ${report.error_count || 0} error(s) and ${report.warning_count || 0} warning(s).`;
    const issues = $("validation-issues-body");
    clear(issues);
    for (const issue of report.issues || []) {
      const row = element("tr");
      appendTextCell(row, issue.severity);
      appendTextCell(row, issue.code, { code: true });
      appendTextCell(row, issue.message);
      appendTextCell(row, issue.path, { title: issue.path });
      appendTextCell(row, issue.remediation);
      issues.append(row);
    }
    if (!(report.issues || []).length) {
      emptyRow(issues, 5, "No validation issues.");
    }
    const audioBody = $("validation-audio-body");
    clear(audioBody);
    for (const facts of report.audio_facts || []) {
      const row = element("tr");
      appendTextCell(row, basename(facts.path), { title: facts.path });
      appendTextCell(row, facts.format);
      appendTextCell(row, facts.subtype);
      appendTextCell(row, facts.sample_rate);
      appendTextCell(row, facts.channels);
      appendTextCell(row, formatDuration(facts.duration_seconds));
      appendTextCell(row, formatNumber(facts.peak, 6));
      appendTextCell(row, formatNumber(facts.rms, 6));
      appendTextCell(row, facts.non_finite_samples);
      audioBody.append(row);
    }
    if (!(report.audio_facts || []).length) {
      emptyRow(audioBody, 9, "No decoded audio facts were returned.");
    }
    $("audio-facts-count").textContent = `${(report.audio_facts || []).length} files`;
    const paths = $("prepared-paths");
    clear(paths);
    if (prepared.materialized === false || isBatch) {
      paths.append(
        element("div", {
          className: "notice info",
          text:
            "Validation preview only: no job, selection, run, event, or output files were created.",
        }),
      );
    }
    let pathFacts;
    if (isBatch) {
      pathFacts = batchTargets
        .filter((item) => item.prepared)
        .slice(0, 6)
        .map((item) => {
          const target = state.tracks.find(
            (track) => track.track_id === item.target_id,
          );
          return [
            `Planned input ${Number(item.index) + 1}`,
            `${(target && (target.label || basename(target.preferred_path))) || item.target_id} → ${item.prepared.output_directory}`,
          ];
        });
      if (batchTargets.length > 6) {
        pathFacts.push([
          "Additional inputs",
          `${batchTargets.length - 6} more planned run directories`,
        ]);
      }
    } else {
      const pathLabelPrefix = prepared.materialized === false ? "Planned " : "";
      pathFacts = [
        [`${pathLabelPrefix}job`, prepared.configuration_path],
        [`${pathLabelPrefix}selection`, prepared.selection_path],
        [`${pathLabelPrefix}manifest`, prepared.manifest_path],
        [`${pathLabelPrefix}events`, prepared.event_log_path],
        [`${pathLabelPrefix}outputs`, (prepared.outputs || []).join(", ")],
        [`${pathLabelPrefix}previews`, (prepared.preview_paths || []).join(", ")],
      ].filter((entry) => entry[1]);
    }
    for (const [label, value] of pathFacts) {
      paths.append(element("div", { text: `${label}: ${value}` }));
    }
    paths.hidden = pathFacts.length === 0 && prepared.materialized !== false;
    $("execution-job-id").value = isBatch
      ? `${result.batch.target_count} run IDs generated when execution starts`
      : prepared.job_id || "Generated for each operation";
    $("execution-engine").value = prepared.engine || "Derived from reference count";
    $("execution-manifest").value =
      prepared.materialized === false
        ? "Created only when a dry run or render starts"
        : prepared.manifest_path || "Managed per run";
    $("execution-event-log").value =
      prepared.materialized === false
        ? "Created only when a dry run or render starts"
        : prepared.event_log_path || "Managed per run";
    return ok;
  }

  function openDialog({
    kicker = "",
    title,
    description = "",
    body,
    confirmText = "Continue",
    cancelText = "Cancel",
    hideCancel = false,
    danger = false,
    onConfirm,
  }) {
    const dialog = $("app-dialog");
    const form = $("dialog-form");
    const dialogBody = $("dialog-body");
    const actions = $("dialog-actions");
    $("dialog-kicker").textContent = kicker;
    $("dialog-title").textContent = title;
    $("dialog-description").textContent = description;
    clear(dialogBody);
    clear(actions);
    if (body) {
      dialogBody.append(body);
    }
    return new Promise((resolve) => {
      let settled = false;
      let busy = false;
      let cancelHandler = null;
      let closeHandler = null;
      let submitHandler = null;
      const closeButton = $("dialog-close");
      const removeDialogListeners = () => {
        if (cancelHandler) {
          dialog.removeEventListener("cancel", cancelHandler);
        }
        if (closeHandler) {
          dialog.removeEventListener("close", closeHandler);
        }
        if (submitHandler) {
          form.removeEventListener("submit", submitHandler);
        }
      };
      const finish = (value) => {
        if (settled) {
          return;
        }
        settled = true;
        busy = false;
        dialog.removeAttribute("aria-busy");
        closeButton.disabled = false;
        removeDialogListeners();
        resolve(value);
        if (dialog.open) {
          dialog.close();
        }
      };
      const cancel = element("button", {
        className: "button secondary",
        text: cancelText,
        type: "button",
      });
      cancel.addEventListener("click", () => {
        if (!busy) {
          finish(null);
        }
      });
      const confirm = element("button", {
        className: `button ${danger ? "danger" : "primary"}`,
        text: confirmText,
        type: "submit",
      });
      const confirmAction = async () => {
        if (busy || settled) {
          return;
        }
        busy = true;
        dialog.setAttribute("aria-busy", "true");
        confirm.disabled = true;
        cancel.disabled = true;
        closeButton.disabled = true;
        try {
          const value = onConfirm ? await onConfirm() : true;
          finish(value === undefined ? true : value);
        } catch (error) {
          busy = false;
          dialog.removeAttribute("aria-busy");
          confirm.disabled = false;
          cancel.disabled = false;
          closeButton.disabled = false;
          const previous = dialogBody.querySelector(".dialog-error");
          if (previous) {
            previous.remove();
          }
          dialogBody.prepend(
            element("div", {
              className: "notice danger dialog-error",
              text: error instanceof Error ? error.message : String(error),
              attrs: { role: "alert" },
            }),
          );
        }
      };
      submitHandler = (event) => {
        event.preventDefault();
        void confirmAction();
      };
      form.addEventListener("submit", submitHandler);
      if (hideCancel) {
        actions.append(confirm);
      } else {
        actions.append(cancel, confirm);
      }
      closeButton.disabled = false;
      closeButton.onclick = () => {
        if (!busy) {
          finish(null);
        }
      };
      cancelHandler = (event) => {
        event.preventDefault();
        if (!busy) {
          finish(null);
        }
      };
      closeHandler = () => {
        if (!busy) {
          finish(null);
        }
      };
      dialog.addEventListener("cancel", cancelHandler);
      dialog.addEventListener("close", closeHandler);
      if (typeof dialog.showModal === "function") {
        dialog.showModal();
      } else {
        dialog.setAttribute("open", "");
      }
      const first = dialogBody.querySelector("input, select, textarea, button");
      if (danger) {
        (hideCancel ? closeButton : cancel).focus();
      } else if (first) {
        first.focus();
      } else {
        confirm.focus();
      }
    });
  }

  function textField(label, value = "", options = {}) {
    const input = element(options.multiline ? "textarea" : "input", {
      value,
      attrs: {
        maxlength: options.maxlength || 300,
        placeholder: options.placeholder || "",
        required: options.required ? "" : null,
        rows: options.multiline ? "4" : null,
      },
    });
    if (!options.multiline) {
      input.type = options.type || "text";
    }
    const wrapper = element("label", { className: "field" });
    wrapper.append(element("span", { text: label }), input);
    return { wrapper, input };
  }

  async function askText({
    kicker,
    title,
    description,
    label,
    value = "",
    confirmText = "Save",
    danger = false,
  }) {
    const field = textField(label, value, { required: true });
    return openDialog({
      kicker,
      title,
      description,
      body: field.wrapper,
      confirmText,
      danger,
      onConfirm: () => {
        const selected = field.input.value.trim();
        if (!selected) {
          throw new Error(`${label} is required.`);
        }
        return selected;
      },
    });
  }

  async function confirmAction({
    kicker = "Confirmation",
    title,
    description,
    confirmText = "Continue",
    danger = false,
  }) {
    const body = element("div", {
      className: `notice ${danger ? "danger" : "info"}`,
      text: description,
    });
    return Boolean(
      await openDialog({
        kicker,
        title,
        body,
        confirmText,
        danger,
      }),
    );
  }

  async function nativeDialog(mode, purpose, suggestedName = null, initialDirectory = null) {
    const payload = { mode, purpose };
    if (suggestedName) {
      payload.suggested_name = suggestedName;
    }
    if (initialDirectory) {
      payload.initial_directory = initialDirectory;
    }
    const result = await api("/api/dialog", { method: "POST", body: payload });
    return result.cancelled ? [] : result.paths || [];
  }

  function setJobOutputDirectory(path, options = {}) {
    if (!path) {
      return;
    }
    $("job-output-directory").value = path;
    if (options.markDirty !== false) {
      invalidateValidation();
    }
  }

  async function chooseJobOutputDirectory() {
    try {
      const paths = await nativeDialog(
        "choose-folder", "folder", null, $("job-output-directory").value,
      );
      if (paths.length) {
        setJobOutputDirectory(paths[0]);
        toast(`Output destination set to ${paths[0]}.`, "good");
      }
    } catch (error) {
      reportError(error, "Could not choose the output folder");
    }
  }

  function useDefaultOutputDirectory() {
    const path =
      (state.preferences && state.preferences.default_output_directory) ||
      (state.bootstrap &&
        state.bootstrap.workspace &&
        state.bootstrap.workspace.outputs);
    if (!path) {
      toast("No default output folder is available.", "warning");
      return;
    }
    setJobOutputDirectory(path);
    toast("The workspace default output folder is active.", "good");
  }

  async function saveDefaultOutputDirectory() {
    const path = $("job-output-directory").value.trim();
    if (!path) {
      toast("Choose an output folder before making it the default.", "warning");
      return;
    }
    try {
      state.preferences = await api("/api/preferences", {
        method: "POST",
        body: {
          kind: "music-mastering-tools/portal-preferences",
          schema_version: 1,
          default_output_directory: path,
        },
      });
      toast(`Default output folder saved: ${path}`, "good", 9000);
      setStatus("Default output folder updated", "good");
    } catch (error) {
      reportError(error, "Could not save the default output folder");
    }
  }

  function mergeOriginalLibrarySources(library, tracks, includeArchived) {
    const sources = Array.isArray(library && library.sources)
      ? [...library.sources]
      : [];
    const known = new Set(
      sources
        .filter((entry) => entry && entry.source)
        .map((entry) => entry.source.track_id),
    );
    for (const track of tracks) {
      const roles = Array.isArray(track.roles) ? track.roles : [];
      const isOriginal =
        roles.some((role) => role === "target" || role === "reference") &&
        !roles.includes("generated-output");
      if (
        !isOriginal ||
        known.has(track.track_id) ||
        (!includeArchived && track.archived)
      ) {
        continue;
      }
      sources.push({
        source: track,
        status: track.preferred_path ? "unmastered" : "unavailable",
        active_version_count: 0,
        discarded_version_count: 0,
        requires_version_choice: false,
        automatic_ab: null,
        versions: [],
      });
      known.add(track.track_id);
    }
    sources.sort((left, right) => {
      const leftTrack = left.source || {};
      const rightTrack = right.source || {};
      const leftName = leftTrack.label || basename(leftTrack.preferred_path);
      const rightName = rightTrack.label || basename(rightTrack.preferred_path);
      return leftName.localeCompare(rightName, undefined, {
        numeric: true,
        sensitivity: "base",
      });
    });
    return {
      ...(library || {}),
      sources,
      summary: (library && library.summary) || {},
    };
  }

  function pruneMasterSelections() {
    const sourceById = new Map(
      (state.masterLibrary.sources || []).map((entry) => [
        entry.source.track_id,
        entry,
      ]),
    );
    for (const [key, selected] of state.selectedMasterExports.entries()) {
      const entry = sourceById.get(selected.source_track_id);
      const version =
        entry &&
        (entry.versions || []).find(
          (candidate) =>
            candidate.version_id === selected.version_id &&
            candidate.library_status === "available",
        );
      const deliverable =
        version &&
        (version.deliverables || []).find(
          (candidate) =>
            candidate.ordinal === selected.artifact_ordinal &&
            candidate.playable,
        );
      if (!entry || !version || !deliverable) {
        state.selectedMasterExports.delete(key);
      }
    }
  }

  async function refreshCatalog() {
    const includeArchived = $("catalog-archived-filter").checked;
    const includeDiscarded = $("catalog-discarded-filter").checked;
    const parameters = new URLSearchParams({
      include_archived: String(includeArchived),
      include_discarded: String(includeDiscarded),
    });
    const [library, allTracks] = await Promise.all([
      api(`/api/master-library?${parameters.toString()}`),
      api("/api/tracks?include_archived=true"),
    ]);
    state.tracks = allTracks;
    state.masterLibrary = mergeOriginalLibrarySources(
      library,
      allTracks,
      includeArchived,
    );
    pruneMasterSelections();
    applyCatalogViewFromFullInventory();
    if (
      state.selectedTrackId &&
      !state.catalogRows.some(
        (entry) =>
          entry.source && entry.source.track_id === state.selectedTrackId,
      )
    ) {
      state.selectedTrackId = null;
      state.selectedMasterVersionId = null;
    }
    renderCatalog();
    prepareAutomaticComparison(selectedLibrarySource());
    const unavailableQueuedSource = [
      ...state.jobTargets,
      ...state.jobReferences,
    ].some((queued) => {
      const track = state.tracks.find(
        (candidate) => candidate.track_id === queued.track_id,
      );
      return !track || track.archived || !track.preferred_path;
    });
    if (unavailableQueuedSource) {
      invalidateValidation();
    }
  }

  function applyCatalogViewFromFullInventory() {
    const role = $("catalog-role-filter").value;
    const query = $("catalog-search").value.trim().toLowerCase();
    const includeArchived = $("catalog-archived-filter").checked;
    state.catalogRows = (state.masterLibrary.sources || []).filter((entry) => {
      const track = entry.source;
      const roles = Array.isArray(track.roles) ? track.roles : [];
      if (!includeArchived && track.archived) {
        return false;
      }
      if (role === "mastered" && entry.active_version_count === 0) {
        return false;
      }
      if (
        role === "unmastered" &&
        (entry.active_version_count > 0 || !roles.includes("target"))
      ) {
        return false;
      }
      if (
        (role === "target" || role === "reference") &&
        !roles.includes(role)
      ) {
        return false;
      }
      return !query || safeJson(entry).toLowerCase().includes(query);
    });
  }

  async function refreshReferenceSets() {
    state.referenceSets = await api("/api/reference-sets");
    if (
      state.selectedSetId &&
      !state.referenceSets.some(
        (referenceSet) => referenceSet.set_id === state.selectedSetId,
      )
    ) {
      state.selectedSetId = null;
    }
    renderReferenceSets();
  }

  async function refreshRuns() {
    state.runs = await api("/api/runs");
    state.selectedRun =
      state.runs.find((run) => run.run_id === state.selectedRunId) || null;
    renderRuns();
  }

  async function refreshLogs() {
    state.logs = await api("/api/logs");
    renderLogs();
  }

  async function refreshBootstrap() {
    state.bootstrap = await api("/api/bootstrap");
    configureBrowserSession(state.bootstrap.browser_lifetime);
    renderBootstrap();
  }

  async function refreshAll(options = {}) {
    setStatus("Refreshing private workspace…", "run");
    try {
      const requests = [
        api("/api/bootstrap").then((bootstrap) => {
          configureBrowserSession(bootstrap.browser_lifetime);
          return bootstrap;
        }),
        api("/api/tracks?include_archived=true"),
        api(
          `/api/master-library?include_archived=${
            $("catalog-archived-filter").checked
          }&include_discarded=${$("catalog-discarded-filter").checked}`,
        ),
        api("/api/reference-sets"),
        api("/api/runs"),
        api("/api/capabilities"),
        api("/api/logs"),
        api("/api/tasks/current"),
        api("/api/preferences"),
      ];
      if (!state.defaults || options.reloadDefaults) {
        requests.push(api("/api/job-defaults"));
      }
      const results = await Promise.all(requests);
      [
        state.bootstrap,
        state.tracks,
        state.masterLibrary,
        state.referenceSets,
        state.runs,
        state.capabilities,
        state.logs,
        state.currentTask,
        state.preferences,
      ] = results.slice(0, 9);
      state.masterLibrary = mergeOriginalLibrarySources(
        state.masterLibrary,
        state.tracks,
        $("catalog-archived-filter").checked,
      );
      pruneMasterSelections();
      applyCatalogViewFromFullInventory();
      if (results.length > 9) {
        state.defaults = results[9];
        applyDefaults({
          clearSources: Boolean(options.firstLoad),
          markClean: Boolean(options.firstLoad),
        });
      }
      state.selectedRun =
        state.runs.find((run) => run.run_id === state.selectedRunId) || null;
      renderBootstrap();
      renderCatalog();
      renderReferenceSets();
      renderRuns();
      renderCapabilities();
      renderLogs();
      mergeTaskEvents(state.currentTask);
      renderTask();
      setStatus("Private workspace ready", "good");
      return true;
    } catch (error) {
      reportError(error, "Could not refresh the workspace");
      return false;
    }
  }

  async function runDiagnostics() {
    $("doctor-badge").textContent = "Running";
    $("doctor-badge").className = "state-badge warning";
    try {
      const [diagnostics, capabilities] = await Promise.all([
        api("/api/diagnostics"),
        api("/api/capabilities"),
      ]);
      state.diagnostics = diagnostics;
      state.capabilities = capabilities;
      renderDiagnostics();
      setStatus(
        diagnostics.healthy ? "Environment diagnostics completed" : "Diagnostics found a failure",
        diagnostics.healthy ? "good" : "danger",
      );
    } catch (error) {
      reportError(error, "Environment diagnostics failed");
    }
  }

  async function validateJob() {
    if (jobRequestIsPending()) {
      toast("A setup check or submission is already in progress.", "warning");
      return null;
    }
    activateTab("master-job");
    let request;
    try {
      request = buildJobRequest();
    } catch (error) {
      reportError(error, "Cannot check mastering setup");
      return null;
    }
    const requestRevision = state.jobRevision;
    const returnFocus = document.activeElement;
    state.validating = true;
    setJobRequestBusy(true);
    $("validation-badge").textContent = "Checking";
    $("validation-badge").className = "state-badge warning";
    $("validation-summary").textContent = "Checking decoded audio and capabilities…";
    setStatus("Validating decoded audio and capabilities…", "run");
    try {
      const result = await api("/api/jobs/validate", {
        method: "POST",
        body: request,
      });
      if (requestRevision !== state.jobRevision) {
        state.jobDirty = true;
        $("validation-badge").textContent = "Outdated";
        $("validation-badge").className = "state-badge warning";
        $("validation-summary").textContent =
          "The setup changed during the check. Check the current setup again.";
        setStatus("Setup changed; the completed check is stale", "warning");
        toast(
          "The setup changed while validation was running. Check the current setup again.",
          "warning",
        );
        return null;
      }
      state.jobTaskId = null;
      state.jobTaskTargets = [];
      state.jobDirty = false;
      const ok = renderValidation(result);
      setStatus(
        ok ? "Job validation passed" : "Job validation is blocked",
        ok ? "good" : "danger",
      );
      return result;
    } catch (error) {
      state.jobDirty = true;
      if (requestRevision === state.jobRevision) {
        $("validation-badge").textContent = "Check failed";
        $("validation-badge").className = "state-badge danger";
        $("validation-summary").textContent =
          error instanceof Error ? error.message : String(error);
      } else {
        $("validation-badge").textContent = "Outdated";
        $("validation-badge").className = "state-badge warning";
        $("validation-summary").textContent =
          "The setup changed during the check. Check the current setup again.";
      }
      reportError(error, "Job validation failed");
      return null;
    } finally {
      state.validating = false;
      setJobRequestBusy(false);
      if (
        !$("panel-master-job").hidden &&
        returnFocus instanceof HTMLElement &&
        returnFocus.isConnected &&
        !returnFocus.disabled
      ) {
        returnFocus.focus();
      }
    }
  }

  async function startJob(dryRun) {
    if (jobRequestIsPending()) {
      toast("A setup check or submission is already in progress.", "warning");
      return;
    }
    activateTab("master-job");
    let request;
    try {
      request = buildJobRequest();
    } catch (error) {
      reportError(error, "Cannot start mastering");
      return;
    }
    const submittedRevision = state.jobRevision;
    const submittedTargets = state.jobTargets.map((target) => ({
      track_id: target.track_id,
      label: target.label,
    }));
    const submittedDestination = $("job-output-directory").value;
    const returnFocus = document.activeElement;
    let submitted = false;
    state.submitting = true;
    setJobRequestBusy(true);
    try {
      if (!dryRun) {
        const confirmed = await confirmAction({
          kicker: "Audited render",
          title:
            submittedTargets.length === 1
              ? "Render this master?"
              : `Render ${submittedTargets.length} input tracks?`,
          description:
            `Each input will be decoded, validated, mastered against the shared reference profile, and committed as its own auditable run in ${submittedDestination}. A failure in one track does not erase successful tracks, and source audio is never overwritten.`,
          confirmText:
            submittedTargets.length === 1 ? "Render master" : "Render batch",
        });
        if (!confirmed) {
          return;
        }
      }
      const task = await api(dryRun ? "/api/jobs/dry-run" : "/api/jobs/render", {
        method: "POST",
        body: request,
      });
      submitted = true;
      state.currentTask = task;
      state.jobTaskId = task.operation_id;
      state.jobTaskTargets = submittedTargets;
      state.jobDirty = state.jobRevision !== submittedRevision;
      mergeTaskEvents(task);
      renderTask();
      setStatus(
        dryRun
          ? `Audited dry run started for ${submittedTargets.length} input(s)`
          : `Master render started for ${submittedTargets.length} input(s)`,
        "run",
      );
      toast(
        `${dryRun ? "Dry run" : "Render"} accepted as ${task.operation_id}.`,
        "good",
      );
    } catch (error) {
      reportError(error, dryRun ? "Could not start dry run" : "Could not start render");
    } finally {
      state.submitting = false;
      setJobRequestBusy(false);
      if (!$("panel-master-job").hidden) {
        if (submitted) {
          const statusHeading = $("validation-title");
          statusHeading.setAttribute("tabindex", "-1");
          statusHeading.focus();
        } else if (
          returnFocus instanceof HTMLElement &&
          returnFocus.isConnected &&
          !returnFocus.disabled
        ) {
          returnFocus.focus();
        }
      }
    }
  }

  async function exportSelection() {
    let job;
    try {
      job = buildJobRequest();
      if (job.target_ids.length !== 1) {
        throw new Error(
          "Portable selection JSON currently represents one input track. Keep the batch in this workspace or export one input at a time; the complete catalog export still retains every track.",
        );
      }
    } catch (error) {
      reportError(error, "Cannot export selection");
      return;
    }
    try {
      const paths = await nativeDialog(
        "save-json",
        "selection",
        "mastering-selection.json",
      );
      if (!paths.length) {
        return;
      }
      const result = await api("/api/selections/export", {
        method: "POST",
        // The native SaveFileDialog has already obtained explicit overwrite
        // confirmation before it returns an existing destination.
        body: { path: paths[0], overwrite: true, job },
      });
      toast(`Selection exported: ${result.path}`, "good");
      setStatus("Reimportable selection exported", "good");
    } catch (error) {
      reportError(error, "Could not export selection");
    }
  }

  async function exportCatalog() {
    try {
      const paths = await nativeDialog("save-json", "catalog", "catalog.json");
      if (!paths.length) {
        return;
      }
      const result = await api("/api/catalog/export", {
        method: "POST",
        // The native SaveFileDialog has already obtained explicit overwrite
        // confirmation before it returns an existing destination.
        body: { path: paths[0], overwrite: true },
      });
      toast(`Catalog revision ${result.revision} exported: ${result.path}`, "good");
      setStatus("Catalog export completed", "good");
    } catch (error) {
      reportError(error, "Could not export catalog");
    }
  }

  async function importCatalog() {
    let importCommitted = false;
    try {
      const paths = await nativeDialog("open-json", "catalog");
      if (!paths.length) {
        return;
      }
      const result = await api("/api/catalog/import", {
        method: "POST",
        body: { path: paths[0] },
      });
      importCommitted = true;
      await Promise.all([refreshCatalog(), refreshReferenceSets(), refreshBootstrap()]);
      let selectionLoaded = false;
      if (result.kind === "selection") {
        const hasCurrentSources = Boolean(
          state.jobTargets.length || state.jobReferences.length,
        );
        const replaceCurrentSources =
          !hasCurrentSources ||
          (await confirmAction({
            kicker: "Replace current selection",
            title: "Load the imported selection into Master?",
            description:
              "The selection is already safely registered in the catalog. Loading it replaces the current input queue, references, and weights; processing, destination, and output settings remain unchanged.",
            confirmText: "Replace job sources",
          }));
        if (replaceCurrentSources) {
          loadImportedSelection(result.selection);
          activateTab("master-job");
          activateJobSection("sources");
          selectionLoaded = true;
        }
      }
      toast(
        result.kind === "selection"
          ? selectionLoaded
            ? `Selection imported and loaded into Master at catalog revision ${result.revision}.`
            : `Selection imported at catalog revision ${result.revision}; the current Master setup was kept.`
          : `Catalog imported at revision ${result.revision}.`,
        "good",
      );
      setStatus(
        result.kind === "selection"
          ? selectionLoaded
            ? "Selection imported and loaded"
            : "Selection imported; current job kept"
          : "Catalog import completed",
        "good",
      );
    } catch (error) {
      reportError(
        error,
        importCommitted
          ? "Import committed, but the workspace could not be refreshed"
          : "Could not import catalog or selection",
      );
    }
  }

  function loadImportedSelection(selection) {
    if (!selection || !selection.target || !Array.isArray(selection.references)) {
      throw new Error("The imported selection response is incomplete.");
    }
    if (!selection.references.length || selection.references.length > maxReferences()) {
      throw new Error(
        `An imported selection must contain 1 to ${maxReferences()} references.`,
      );
    }
    const target = state.tracks.find(
      (candidate) => candidate.track_id === selection.target.track_id,
    );
    if (!target || target.archived || !target.preferred_path) {
      throw new Error(
        "The imported target is archived or unavailable. Repair its catalog location first.",
      );
    }
    const references = [...selection.references]
      .sort((left, right) => Number(left.ordinal) - Number(right.ordinal))
      .map((reference) => {
        const track = state.tracks.find(
          (candidate) => candidate.track_id === reference.track_id,
        );
        if (
          !track ||
          track.archived ||
          !track.preferred_path ||
          !track.roles.includes("reference")
        ) {
          throw new Error(
            `Imported reference ${reference.label || reference.track_id} is archived or unavailable.`,
          );
        }
        return jobReferenceFromTrack(
          track,
          reference.level_weight,
          reference.frequency_weight,
        );
      });
    state.jobTargets = [jobTargetFromTrack(target)];
    state.jobReferences = references;
    renderJobTargets();
    renderJobReferences();
    invalidateValidation();
  }

  async function addAudio(defaultRoles) {
    try {
      const paths = await nativeDialog("open-audio-many", "audio");
      if (!paths.length) {
        return;
      }
      const body = element("div");
      const roleCard = element("div", { className: "form-card" });
      roleCard.append(element("strong", { text: "Catalog roles" }));
      const targetChoice = element("input", { type: "checkbox" });
      targetChoice.checked = defaultRoles.includes("target");
      const referenceChoice = element("input", { type: "checkbox" });
      referenceChoice.checked = defaultRoles.includes("reference");
      const targetLabel = element("label", { className: "check-row" }, [
        targetChoice,
        element("span", { text: "Target / input mix" }),
      ]);
      const referenceLabel = element("label", { className: "check-row" }, [
        referenceChoice,
        element("span", { text: "Reference song" }),
      ]);
      roleCard.append(targetLabel, referenceLabel);
      body.append(roleCard);
      const labels = new Map();
      for (const path of paths) {
        const field = textField(`Private label · ${basename(path)}`, "", {
          placeholder: "Optional label",
        });
        field.wrapper.append(element("small", { text: path, title: path }));
        labels.set(path, field.input);
        body.append(field.wrapper);
      }
      let committedRoles = [];
      const result = await openDialog({
        kicker: "Catalog audio",
        title: `Add ${paths.length} ${paths.length === 1 ? "track" : "tracks"}`,
        description:
          "Files stay in place. The catalog records a content hash, audio facts, roles, and this location.",
        body,
        confirmText: "Fingerprint and add",
        onConfirm: async () => {
          const roles = [];
          if (targetChoice.checked) {
            roles.push("target");
          }
          if (referenceChoice.checked) {
            roles.push("reference");
          }
          if (!roles.length) {
            throw new Error("Select at least one catalog role.");
          }
          committedRoles = [...roles];
          const labelValues = {};
          for (const [path, input] of labels.entries()) {
            labelValues[path] = input.value.trim() || null;
          }
          const response = await api("/api/tracks/add", {
            method: "POST",
            body: { paths, roles, labels: labelValues },
          });
          return response;
        },
      });
      if (!result) {
        return;
      }
      await Promise.all([refreshCatalog(), refreshBootstrap()]);
      const queueRole =
        defaultRoles.length === 1 && committedRoles.includes(defaultRoles[0])
          ? defaultRoles[0]
          : null;
      if (queueRole) {
        const queueFailures = [];
        for (const item of result.results || []) {
          if (!item.ok || !item.track || !item.track.track_id) {
            continue;
          }
          try {
            if (queueRole === "target") {
              addJobTarget(item.track.track_id);
            } else {
              addJobReference(item.track.track_id);
            }
          } catch (error) {
            queueFailures.push(error instanceof Error ? error.message : String(error));
          }
        }
        if (queueFailures.length) {
          toast(
            `${queueFailures.length} cataloged track(s) could not be added to the current queue: ${queueFailures[0]}`,
            "warning",
            10000,
          );
        }
      }
      if (result.failure_count) {
        const report = element("div");
        for (const item of result.results) {
          report.append(
            element("div", {
              className: `notice ${item.ok ? "info" : "danger"}`,
              text: item.ok
                ? `${basename(item.path)} was cataloged successfully.`
                : `${basename(item.path)} failed: ${item.error.message}`,
            }),
          );
        }
        const openCatalog = await openDialog({
          kicker: "Batch result",
          title: `${result.success_count} added · ${result.failure_count} failed`,
          description:
            "Successful files were committed independently and are already visible in the catalog. Failed files were not partially registered.",
          body: report,
          cancelText: "Close",
          confirmText: "Open catalog",
          onConfirm: () => true,
        });
        if (openCatalog) {
          activateTab("catalog");
        }
        setStatus("Audio add completed with per-file failures", "warning");
      } else {
        toast(`${result.success_count} track(s) added to the catalog.`, "good");
        setStatus("Catalog audio added", "good");
      }
    } catch (error) {
      reportError(error, "Could not add audio");
    }
  }

  async function verifyTracks(trackId = null) {
    try {
      const result = await api("/api/tracks/verify", {
        method: "POST",
        body: { track_id: trackId },
      });
      await refreshCatalog();
      toast(
        `Verified ${result.count} location(s): ${Object.entries(result.states)
          .map(([name, count]) => `${name} ${count}`)
          .join(", ")}.`,
        "good",
      );
      setStatus("Catalog locations verified", "good");
    } catch (error) {
      reportError(error, "Could not verify catalog locations");
    }
  }

  async function relinkSelectedTrack() {
    const track = selectedTrack();
    if (!track) {
      return;
    }
    try {
      const paths = await nativeDialog("open-audio", "audio", null, track.preferred_path);
      if (!paths.length) {
        return;
      }
      await api("/api/tracks/relink", {
        method: "POST",
        body: { track_id: track.track_id, path: paths[0], archive_location_id: null },
      });
      await refreshCatalog();
      toast("Byte-identical track location linked.", "good");
    } catch (error) {
      reportError(error, "Could not relink track");
    }
  }

  async function renameSelectedTrack() {
    const track = selectedTrack();
    if (!track) {
      return;
    }
    const currentName = track.label || basename(track.preferred_path);
    const label = await askText({
      kicker: "Music library",
      title: "Edit track name",
      description:
        "This changes the name shown throughout the private library and future exports. The original audio filename and every audit record stay unchanged.",
      label: "Track name",
      value: currentName,
      confirmText: "Save track name",
    });
    if (!label || label === track.label) {
      return;
    }
    try {
      console.info("Updating source display name", {
        track_id: track.track_id,
        old_label: track.label,
        new_label: label,
      });
      await api("/api/tracks/label", {
        method: "POST",
        body: { track_id: track.track_id, label },
      });
      await Promise.all([refreshCatalog(), refreshBootstrap()]);
      toast(`Track name updated to ${label}.`, "good");
      setStatus("Music library name updated", "good");
    } catch (error) {
      reportError(error, "Could not update the track name");
    }
  }

  async function exportSelectedMasters() {
    const items = [...state.selectedMasterExports.values()];
    if (!items.length) {
      toast("Select at least one mastered track to export.", "warning");
      return;
    }
    const suffix = $("master-export-suffix").value;
    if (/[\u0000-\u001f<>:"/\\|?*]/u.test(suffix)) {
      toast(
        "The export suffix cannot contain path separators or reserved filename characters.",
        "warning",
      );
      $("master-export-suffix").focus();
      return;
    }
    try {
      const paths = await nativeDialog("choose-folder", "folder");
      if (!paths.length) {
        return;
      }
      const destination = paths[0];
      const confirmed = await confirmAction({
        kicker: "Batch export",
        title: `Export ${items.length} mastered ${
          items.length === 1 ? "track" : "tracks"
        }?`,
        description:
          `Copies will be written to ${destination} with the suffix ` +
          `${suffix || "(none)"}. Existing files are never overwritten; ` +
          "each collision or copy failure is reported separately.",
        confirmText: "Export copies",
      });
      if (!confirmed) {
        return;
      }
      setStatus(`Exporting ${items.length} mastered track(s)…`, "run");
      console.info("Starting selected master export", {
        destination_directory: destination,
        suffix,
        items,
      });
      const result = await api("/api/master-library/export", {
        method: "POST",
        body: {
          destination_directory: destination,
          suffix,
          items,
        },
      });
      for (const item of result.results || []) {
        if (!item.ok) {
          continue;
        }
        const selected = items[item.index];
        if (selected) {
          state.selectedMasterExports.delete(
            masterExportKey(
              selected.source_track_id,
              selected.version_id,
              selected.artifact_ordinal,
            ),
          );
        }
      }
      renderCatalog();

      const report = element("div", { className: "batch-result-list" });
      for (const item of result.results || []) {
        const selected = items[item.index] || {};
        const source = (state.masterLibrary.sources || []).find(
          (entry) =>
            entry.source.track_id ===
            (item.source_track_id || selected.source_track_id),
        );
        const sourceName = source
          ? source.source.label || basename(source.source.preferred_path)
          : item.source_track_id || selected.source_track_id || `Item ${item.index + 1}`;
        report.append(
          element("div", {
            className: `notice ${item.ok ? "info" : "danger"}`,
            text: item.ok
              ? `${sourceName}: ${item.destination_path}`
              : `${sourceName}: ${
                  (item.error && item.error.message) || "Export failed"
                }`,
          }),
        );
      }
      await openDialog({
        kicker: "Batch export result",
        title: `${result.success_count} exported · ${result.failure_count} failed`,
        description:
          result.failure_count > 0
            ? "Successful copies are complete. Failed items remain selected so you can change the suffix or destination and retry."
            : "Every selected master was copied and fingerprint-verified.",
        body: report,
        confirmText: "Done",
        hideCancel: true,
      });
      setStatus(
        result.failure_count > 0
          ? "Master export completed with item failures"
          : "Master export completed",
        result.failure_count > 0 ? "warning" : "good",
      );
    } catch (error) {
      reportError(error, "Could not export selected masters");
    }
  }

  function clearDiscardedVersionFromAudition(version) {
    const trackIds = new Set(
      (version.deliverables || [])
        .map((deliverable) => deliverable.track_id)
        .filter(Boolean),
    );
    if (
      ["a", "b", "preview"].some(
        (slot) =>
          state.audition[slot] &&
          trackIds.has(state.audition[slot].track_id),
      )
    ) {
      clearAudition();
    }
  }

  async function discardMasterVersion(entry, version) {
    if (!entry || !version) {
      return;
    }
    const confirmed = await confirmAction({
      kicker: "Recoverable master cleanup",
      title: `Discard ${version.display_label}?`,
      description:
        "All audio deliverables for this mastered version will move into the private recovery area. The original track, manifest, logs, reference evidence, and catalog history remain intact, and the version can be restored later.",
      confirmText: "Discard mastered version",
      danger: true,
    });
    if (!confirmed) {
      return;
    }
    try {
      setStatus(`Discarding ${version.display_label}…`, "run");
      console.info("Discarding mastered version", {
        source_track_id: entry.source.track_id,
        version_id: version.version_id,
      });
      const result = await api("/api/master-library/discard", {
        method: "POST",
        body: {
          source_track_id: entry.source.track_id,
          version_id: version.version_id,
        },
      });
      for (const [key, selected] of state.selectedMasterExports.entries()) {
        if (
          selected.source_track_id === entry.source.track_id &&
          selected.version_id === version.version_id
        ) {
          state.selectedMasterExports.delete(key);
        }
      }
      if (
        state.preferredMasterBySource.get(entry.source.track_id) ===
        version.version_id
      ) {
        state.preferredMasterBySource.delete(entry.source.track_id);
      }
      clearDiscardedVersionFromAudition(version);
      state.automaticComparisonSourceId = null;
      await Promise.all([refreshCatalog(), refreshRuns(), refreshBootstrap()]);
      prepareAutomaticComparison(selectedLibrarySource());
      toast(
        `Master discarded recoverably. Recovery record: ${result.tombstone_path}`,
        "good",
        10000,
      );
      setStatus("Mastered version moved to private recovery", "good");
    } catch (error) {
      reportError(error, "Could not discard the mastered version");
    }
  }

  async function restoreMasterVersion(entry, version) {
    if (!entry || !version) {
      return;
    }
    try {
      setStatus(`Restoring ${version.display_label}…`, "run");
      console.info("Restoring mastered version", {
        source_track_id: entry.source.track_id,
        version_id: version.version_id,
      });
      await api("/api/master-library/restore", {
        method: "POST",
        body: {
          source_track_id: entry.source.track_id,
          version_id: version.version_id,
        },
      });
      state.automaticComparisonSourceId = null;
      await Promise.all([refreshCatalog(), refreshRuns(), refreshBootstrap()]);
      prepareAutomaticComparison(selectedLibrarySource());
      toast("Mastered version restored to its original output paths.", "good");
      setStatus("Mastered version restored", "good");
    } catch (error) {
      reportError(error, "Could not restore the mastered version");
    }
  }

  async function setSelectedTrackArchived(archived) {
    const track = selectedTrack();
    if (!track) {
      return;
    }
    if (archived) {
      const confirmed = await confirmAction({
        kicker: "Catalog maintenance",
        title: `Archive ${track.label || basename(track.preferred_path)}?`,
        description:
          "The track will be hidden from normal selection. Its audio, locations, sets, runs, and artifacts will not be deleted.",
        confirmText: "Archive track",
        danger: true,
      });
      if (!confirmed) {
        return;
      }
    }
    try {
      await api(archived ? "/api/tracks/archive" : "/api/tracks/restore", {
        method: "POST",
        body: { track_id: track.track_id },
      });
      if (archived && !$("catalog-archived-filter").checked) {
        state.selectedTrackId = null;
      }
      await Promise.all([refreshCatalog(), refreshReferenceSets(), refreshBootstrap()]);
      toast(archived ? "Track archived." : "Track restored.", "good");
    } catch (error) {
      reportError(error, archived ? "Could not archive track" : "Could not restore track");
    }
  }

  function useSelectedTrackAsTarget() {
    const track = selectedTrack();
    if (!track || !(track.roles || []).includes("target")) {
      return;
    }
    activateTab("master-job");
    activateJobSection("sources");
    try {
      addJobTarget(track.track_id);
    } catch (error) {
      reportError(error, "Could not add input track");
    }
  }

  function useSelectedTrackAsReference() {
    const track = selectedTrack();
    if (!track || !(track.roles || []).includes("reference")) {
      return;
    }
    try {
      addJobReference(track.track_id);
      activateTab("master-job");
      activateJobSection("sources");
    } catch (error) {
      reportError(error, "Could not add reference");
    }
  }

  async function createReferenceSet() {
    const name = await askText({
      kicker: "Reusable profile",
      title: "Create a reference set",
      description:
        "A named set stores ordered catalog identities and independent level/frequency weights.",
      label: "Set name",
      confirmText: "Create set",
    });
    if (!name) {
      return;
    }
    try {
      const created = await api("/api/reference-sets/create", {
        method: "POST",
        body: { name },
      });
      await Promise.all([refreshReferenceSets(), refreshBootstrap()]);
      state.selectedSetId = created.set_id;
      renderReferenceSets();
      toast(`Reference set created: ${created.name}`, "good");
      await editReferenceSetMembers();
    } catch (error) {
      reportError(error, "Could not create reference set");
    }
  }

  function memberEditor(referenceSet) {
    const members = (referenceSet.members || []).map((member) => ({
      track_id: member.track_id,
      label: member.label || basename(member.path),
      level_weight: Number(member.level_weight),
      frequency_weight: Number(member.frequency_weight),
    }));
    const root = element("div");
    const toolbar = element("div", { className: "inline-toolbar" });
    const picker = element("select", {
      attrs: { id: "reference-member-picker" },
    });
    picker.append(option("", "Choose a cataloged reference"));
    for (const track of activeTracksForRole("reference")) {
      picker.append(option(track.track_id, track.label || basename(track.preferred_path)));
    }
    const pickerField = element("label", {
      className: "field grow",
      attrs: { for: "reference-member-picker" },
    });
    pickerField.append(element("span", { text: "Reference to add" }), picker);
    const addButton = element("button", {
      className: "button secondary",
      text: "Add reference",
      type: "button",
    });
    toolbar.append(pickerField, addButton);
    const list = element("div", { className: "dialog-member-list" });
    root.append(toolbar, list);

    const restoreMemberControlFocus = (
      trackId,
      controlName,
      fallbackIndex,
    ) => {
      const cards = all(".dialog-member", list);
      const card =
        cards.find((candidate) => candidate.dataset.memberTrackId === trackId) ||
        cards[Math.min(fallbackIndex, Math.max(0, cards.length - 1))];
      if (!card) {
        picker.focus();
        return;
      }
      let control = card.querySelector(
        `[data-member-control="${controlName}"]`,
      );
      if (!control || control.disabled) {
        const alternate = controlName === "up" ? "down" : "up";
        control = card.querySelector(
          `[data-member-control="${alternate}"]:not(:disabled)`,
        );
      }
      if (!control || control.disabled) {
        control = card.querySelector('[data-member-control="remove"]');
      }
      (control || picker).focus();
    };

    const render = () => {
      clear(list);
      addButton.disabled = members.length >= maxReferences();
      addButton.title = addButton.disabled
        ? `A reference set can contain at most ${maxReferences()} members.`
        : "";
      if (!members.length) {
        list.append(
          element("div", {
            className: "empty-state",
            text: "This set is empty. Add one or more cataloged references.",
          }),
        );
        return;
      }
      members.forEach((member, index) => {
        const card = element("div", {
          className: "form-card dialog-member",
          attrs: { "data-member-track-id": member.track_id },
        });
        const header = element("div", { className: "inline-toolbar" });
        header.append(
          element("strong", { text: `${index + 1}. ${member.label}` }),
          element("span", { className: "toolbar-spacer" }),
        );
        const up = element("button", {
          className: "icon-button",
          text: "↑",
          type: "button",
          title: "Move earlier",
          attrs: {
            "aria-label": `Move ${member.label} earlier`,
            "data-member-control": "up",
          },
        });
        up.disabled = index === 0;
        up.addEventListener("click", () => {
          const [item] = members.splice(index, 1);
          const destination = index - 1;
          members.splice(destination, 0, item);
          render();
          restoreMemberControlFocus(item.track_id, "up", destination);
        });
        const down = element("button", {
          className: "icon-button",
          text: "↓",
          type: "button",
          title: "Move later",
          attrs: {
            "aria-label": `Move ${member.label} later`,
            "data-member-control": "down",
          },
        });
        down.disabled = index === members.length - 1;
        down.addEventListener("click", () => {
          const [item] = members.splice(index, 1);
          const destination = index + 1;
          members.splice(destination, 0, item);
          render();
          restoreMemberControlFocus(item.track_id, "down", destination);
        });
        const remove = element("button", {
          className: "icon-button danger-text",
          text: "×",
          type: "button",
          title: "Remove reference",
          attrs: {
            "aria-label": `Remove ${member.label}`,
            "data-member-control": "remove",
          },
        });
        remove.addEventListener("click", () => {
          members.splice(index, 1);
          render();
          restoreMemberControlFocus(null, "remove", index);
        });
        header.append(up, down, remove);
        const fields = element("div", { className: "field-grid" });
        const level = textField("Level weight", member.level_weight, { type: "number" });
        level.input.min = "0";
        level.input.step = "0.1";
        level.input.addEventListener("change", () => {
          member.level_weight = Number(level.input.value);
        });
        const frequency = textField("Frequency weight", member.frequency_weight, {
          type: "number",
        });
        frequency.input.min = "0";
        frequency.input.step = "0.1";
        frequency.input.addEventListener("change", () => {
          member.frequency_weight = Number(frequency.input.value);
        });
        fields.append(level.wrapper, frequency.wrapper);
        card.append(header, fields);
        list.append(card);
      });
    };
    addButton.addEventListener("click", () => {
      if (members.length >= maxReferences()) {
        toast(
          `A reference set can contain at most ${maxReferences()} members.`,
          "warning",
        );
        return;
      }
      const track = state.tracks.find((candidate) => candidate.track_id === picker.value);
      if (!track) {
        toast("Choose a reference before adding it.", "warning");
        return;
      }
      if (members.some((member) => member.track_id === track.track_id)) {
        toast("A named reference set cannot contain duplicate content.", "warning");
        return;
      }
      members.push({
        track_id: track.track_id,
        label: track.label || basename(track.preferred_path),
        level_weight: 1,
        frequency_weight: 1,
      });
      picker.value = "";
      render();
    });
    render();
    return {
      root,
      value() {
        if (members.length > maxReferences()) {
          throw new Error(
            `A reference set cannot contain more than ${maxReferences()} members.`,
          );
        }
        for (const member of members) {
          if (
            !Number.isFinite(member.level_weight) ||
            member.level_weight < 0 ||
            !Number.isFinite(member.frequency_weight) ||
            member.frequency_weight < 0
          ) {
            throw new Error("Every weight must be a finite non-negative number.");
          }
        }
        if (
          members.length &&
          members.reduce((sum, member) => sum + member.level_weight, 0) <= 0
        ) {
          throw new Error("The set needs a positive total level weight.");
        }
        if (
          members.length &&
          members.reduce((sum, member) => sum + member.frequency_weight, 0) <= 0
        ) {
          throw new Error("The set needs a positive total frequency weight.");
        }
        return members.map((member) => ({
          track_id: member.track_id,
          level_weight: member.level_weight,
          frequency_weight: member.frequency_weight,
        }));
      },
    };
  }

  async function editReferenceSetMembers() {
    const referenceSet = selectedSet();
    if (!referenceSet) {
      return;
    }
    const editor = memberEditor(referenceSet);
    const updated = await openDialog({
      kicker: "Atomic replacement",
      title: `Edit ${referenceSet.name}`,
      description:
        "Reorder, add, remove, or independently weight references. Save replaces the complete membership in one catalog transaction.",
      body: editor.root,
      confirmText: "Save members",
      onConfirm: async () =>
        api("/api/reference-sets/replace-members", {
          method: "POST",
          body: { set_id: referenceSet.set_id, members: editor.value() },
        }),
    });
    if (!updated) {
      return;
    }
    await Promise.all([refreshReferenceSets(), refreshBootstrap()]);
    state.selectedSetId = updated.set_id;
    renderReferenceSets();
    toast(`Reference set updated: ${updated.name}`, "good");
  }

  async function renameReferenceSet() {
    const referenceSet = selectedSet();
    if (!referenceSet) {
      return;
    }
    const name = await askText({
      kicker: "Catalog label",
      title: "Rename reference set",
      description: "Run manifests and track identities are unchanged.",
      label: "Set name",
      value: referenceSet.name,
      confirmText: "Rename",
    });
    if (!name || name === referenceSet.name) {
      return;
    }
    try {
      await api("/api/reference-sets/rename", {
        method: "POST",
        body: { set_id: referenceSet.set_id, name },
      });
      await refreshReferenceSets();
      toast(`Reference set renamed to ${name}.`, "good");
    } catch (error) {
      reportError(error, "Could not rename reference set");
    }
  }

  async function deleteReferenceSet() {
    const referenceSet = selectedSet();
    if (!referenceSet) {
      return;
    }
    const confirmed = await confirmAction({
      kicker: "Catalog maintenance",
      title: `Delete ${referenceSet.name}?`,
      description:
        "This deletes only the reusable set definition. Tracks, source audio, selections, runs, and manifests remain intact.",
      confirmText: "Delete set",
      danger: true,
    });
    if (!confirmed) {
      return;
    }
    try {
      await api("/api/reference-sets/delete", {
        method: "POST",
        body: { set_id: referenceSet.set_id },
      });
      state.selectedSetId = null;
      await Promise.all([refreshReferenceSets(), refreshBootstrap()]);
      toast("Reference set deleted. No audio was removed.", "good");
    } catch (error) {
      reportError(error, "Could not delete reference set");
    }
  }

  async function saveJobReferencesAsSet() {
    if (!state.jobReferences.length) {
      toast("Add at least one reference before saving a set.", "warning");
      return;
    }
    if (
      new Set(state.jobReferences.map((reference) => reference.track_id)).size !==
      state.jobReferences.length
    ) {
      toast("Named sets cannot contain duplicate content. Remove duplicates first.", "warning");
      return;
    }
    const name = await askText({
      kicker: "Reusable profile",
      title: "Save current references",
      description:
        "The current order and both weight dimensions will become a named catalog set.",
      label: "Set name",
      confirmText: "Save set",
    });
    if (!name) {
      return;
    }
    let created = null;
    try {
      created = await api("/api/reference-sets/create", {
        method: "POST",
        body: { name },
      });
      const saved = await api("/api/reference-sets/replace-members", {
        method: "POST",
        body: {
          set_id: created.set_id,
          members: state.jobReferences.map((reference) => ({
            track_id: reference.track_id,
            level_weight: reference.level_weight,
            frequency_weight: reference.frequency_weight,
          })),
        },
      });
      await Promise.all([refreshReferenceSets(), refreshBootstrap()]);
      state.selectedSetId = saved.set_id;
      renderReferenceSets();
      toast(`Saved reference set: ${saved.name}`, "good");
    } catch (error) {
      if (created) {
        try {
          await api("/api/reference-sets/delete", {
            method: "POST",
            body: { set_id: created.set_id },
          });
        } catch (cleanupError) {
          console.error("Could not remove incomplete reference set", cleanupError);
        }
      }
      reportError(error, "Could not save reference set");
    }
  }

  async function loadReferenceSet(referenceSet = null) {
    const selected =
      referenceSet ||
      state.referenceSets.find(
        (candidate) => candidate.set_id === $("reference-set-picker").value,
      ) ||
      selectedSet();
    if (!selected) {
      toast("Choose a named reference set.", "warning");
      return;
    }
    if (!selected.members.length) {
      toast("This reference set is empty. Edit its members first.", "warning");
      return;
    }
    if (selected.members.length > maxReferences()) {
      toast(`The set exceeds the ${maxReferences()} reference limit.`, "danger");
      return;
    }
    const unavailable = selected.members.filter((member) => member.archived || !member.path);
    if (unavailable.length) {
      toast(
        "One or more set members are archived or have no known path. Repair the catalog first.",
        "danger",
      );
      return;
    }
    if (state.jobReferences.length) {
      const confirmed = await confirmAction({
        kicker: "Replace current selection",
        title: `Load ${selected.name}?`,
        description:
          "This replaces the references currently in Master. Mixes and processing settings remain unchanged.",
        confirmText: "Replace references",
      });
      if (!confirmed) {
        return;
      }
    }
    state.jobReferences = selected.members.map((member) => ({
      track_id: member.track_id,
      label: member.label || basename(member.path),
      path: member.path,
      level_weight: Number(member.level_weight),
      frequency_weight: Number(member.frequency_weight),
    }));
    renderJobReferences();
    invalidateValidation();
    activateTab("master-job");
    activateJobSection("sources");
    toast(`Loaded reference set: ${selected.name}`, "good");
  }

  function recoveryPathRow(label, initialValue, purpose) {
    const field = textField(label, initialValue || "", {
      placeholder: purpose === "run-manifest" ? "Required manifest JSON" : "Optional",
    });
    field.input.readOnly = true;
    const button = element("button", {
      className: "button secondary",
      text: "Browse…",
      type: "button",
    });
    const row = element("div", { className: "inline-toolbar" }, [
      field.wrapper,
      button,
    ]);
    button.addEventListener("click", async () => {
      try {
        const paths = await nativeDialog("open-json", purpose);
        if (paths.length) {
          field.input.value = paths[0];
        }
      } catch (error) {
        reportError(error, "Could not open recovery file picker");
      }
    });
    return { row, input: field.input };
  }

  async function recoverManifest(initialManifestPath = "") {
    const manifest = recoveryPathRow(
      "Run manifest",
      initialManifestPath,
      "run-manifest",
    );
    const selection = recoveryPathRow("Selection override", "", "selection");
    const configuration = recoveryPathRow(
      "Job configuration override",
      "",
      "job-configuration",
    );
    const body = element("div");
    body.append(
      element("div", {
        className: "notice info",
        text:
          "Recovery never rerenders audio. It verifies the preserved manifest and reimports its selection, then idempotently rebuilds catalog run/artifact indexing.",
      }),
      manifest.row,
      selection.row,
      configuration.row,
    );
    const result = await openDialog({
      kicker: "Evidence recovery",
      title: "Recover an audited run",
      description:
        "Selection and job paths are optional when the manifest already owns or embeds them.",
      body,
      confirmText: "Recover indexing",
      onConfirm: async () => {
        if (!manifest.input.value.trim()) {
          throw new Error("Choose a run manifest.");
        }
        return api("/api/recovery/run", {
          method: "POST",
          body: {
            manifest_path: manifest.input.value.trim(),
            selection_path: selection.input.value.trim() || null,
            configuration_path: configuration.input.value.trim() || null,
          },
        });
      },
    });
    if (!result) {
      return;
    }
    await Promise.all([
      refreshCatalog(),
      refreshReferenceSets(),
      refreshRuns(),
      refreshBootstrap(),
    ]);
    state.selectedRunId = result.run_id;
    await selectRun(result.run_id);
    toast(
      `Recovered ${result.run_id} with ${result.artifact_count} indexed artifacts.`,
      "good",
    );
    setStatus("Run indexing recovered without rerendering", "good");
  }

  async function recoverFromPicker() {
    try {
      const paths = await nativeDialog("open-json", "run-manifest");
      if (paths.length) {
        await recoverManifest(paths[0]);
      }
    } catch (error) {
      reportError(error, "Could not start run recovery");
    }
  }

  async function openFolder(path) {
    if (!path) {
      return;
    }
    try {
      await api("/api/open-folder", {
        method: "POST",
        body: { path },
      });
    } catch (error) {
      reportError(error, "Could not show path in Explorer");
    }
  }

  async function shutdownPortal() {
    const confirmed = await confirmAction({
      kicker: "Local application",
      title: "Shut down the private portal?",
      description:
        state.bootstrap?.browser_lifetime?.enabled
          ? "Shutdown is refused while mastering is active. Closing the last workbench tab also exits after active work finishes."
          : "Shutdown is refused while mastering is active. This session stays running when its browser tabs close.",
      confirmText: "Shut down",
      danger: true,
    });
    if (!confirmed) {
      return;
    }
    try {
      await api("/api/shutdown", { method: "POST", body: {} });
      state.stopped = true;
      closeBrowserSession();
      setStatus("Private portal stopped; this page is now read-only", "warning");
      toast("The local portal has shut down. You may close this browser tab.", "good", 12000);
      all("button, input, select, textarea").forEach((control) => {
        control.disabled = true;
      });
    } catch (error) {
      reportError(error, "Portal shutdown was refused");
    }
  }

  async function pollTask() {
    if (state.stopped || state.polling) {
      return;
    }
    state.polling = true;
    try {
      const task = await api("/api/tasks/current");
      state.currentTask = task;
      mergeTaskEvents(task);
      renderTask();
      if (
        task &&
        ["succeeded", "failed"].includes(task.state) &&
        state.lastTerminalTaskId !== task.operation_id
      ) {
        state.lastTerminalTaskId = task.operation_id;
        await Promise.all([refreshBootstrap(), refreshRuns(), refreshCatalog()]);
        const batch = task.result && task.result.batch;
        if (task.state === "succeeded" && batch && batch.status === "partial_failure") {
          toast(
            `${batch.success_count} ${task.kind} target(s) completed and ${batch.failure_count} failed. Successful masters were retained; inspect Activity › Event log for the failed inputs.`,
            "warning",
            14000,
          );
          setStatus(`${task.kind} partially completed`, "warning");
        } else if (task.state === "succeeded" && batch && batch.status === "failed") {
          toast(
            `${task.kind} finished processing, but all ${batch.failure_count} inputs failed. Inspect Activity › Event log and System logs for each cause.`,
            "danger",
            14000,
          );
          setStatus(`${task.kind} batch failed`, "danger");
        } else if (task.state === "succeeded") {
          toast(
            `${task.kind} completed. Durable evidence is available in Activity › History.`,
            "good",
            10000,
          );
          setStatus(`${task.kind} completed`, "good");
        } else {
          toast(
            `${task.kind} failed. Inspect Activity › Event log and System logs for the complete cause chain.`,
            "danger",
            12000,
          );
          setStatus(`${task.kind} failed`, "danger");
        }
      }
    } catch (error) {
      if (!state.stopped) {
        console.error("Task polling failed", error);
      }
    } finally {
      state.polling = false;
      if (!state.stopped) {
        window.setTimeout(pollTask, POLL_INTERVAL_MS);
      }
    }
  }

  function bindControls() {
    all("[data-tab]").forEach((tab) => {
      tab.addEventListener("click", () => activateTab(tab.dataset.tab));
    });
    bindLinearTabKeys("[data-tab]", activateTab);

    all("[data-go-tab]").forEach((button) => {
      button.addEventListener("click", () => activateTab(button.dataset.goTab));
    });

    all("[data-job-section]").forEach((step) => {
      step.addEventListener("click", () => activateJobSection(step.dataset.jobSection));
    });

    all("[data-run-subtab]").forEach((tab) => {
      tab.addEventListener("click", () => activateRunSubtab(tab.dataset.runSubtab));
    });
    bindLinearTabKeys("[data-run-subtab]", activateRunSubtab);

    const commands = {
      "new-job": async () => {
        const hasWork = Boolean(
          state.jobDirty ||
            state.jobTargets.length ||
            state.jobReferences.length ||
            $("job-notes").value.trim(),
        );
        if (
          hasWork &&
          !(await confirmAction({
            kicker: "Master",
            title: "Start a new job?",
            description:
              "This clears the current input queue, references, weights, notes, destination override, and processing changes. Catalog tracks, defaults, and named sets are not changed.",
            confirmText: "Start new job",
          }))
        ) {
          return;
        }
        applyDefaults({ clearSources: true, markClean: true });
        activateTab("master-job");
        activateJobSection("sources");
      },
      "import-catalog": importCatalog,
      "export-catalog": exportCatalog,
      "export-selection": exportSelection,
      "open-workspace": () =>
        openFolder(state.bootstrap && state.bootstrap.workspace.root),
      shutdown: shutdownPortal,
      "validate-job": validateJob,
      "dry-run": () => startJob(true),
      render: () => startJob(false),
      "refresh-all": () => refreshAll(),
      "add-target-track": () => addAudio(["target"]),
      "add-reference-track": () => addAudio(["reference"]),
      "add-both-track": () => addAudio(["target", "reference"]),
    };
    all("[data-command]").forEach((button) => {
      button.addEventListener("click", async () => {
        closeMenus();
        const handler = commands[button.dataset.command];
        if (handler) {
          await handler();
        }
      });
    });

    all(".command-menu").forEach((menu) => {
      menu.addEventListener("toggle", () => {
        if (menu.open) {
          all(".command-menu[open]").forEach((other) => {
            if (other !== menu) {
              other.removeAttribute("open");
            }
          });
        }
      });
    });
    document.addEventListener("click", (event) => {
      if (!event.target.closest(".command-menu")) {
        closeMenus();
      }
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        closeMenus();
      }
    });

    $("job-form").addEventListener("submit", (event) => event.preventDefault());
    $("job-form").addEventListener("input", () => invalidateValidation());
    $("target-add-button").addEventListener("click", () =>
      chooseCatalogTracks("target").catch((error) =>
        reportError(error, "Could not add mixes"),
      ),
    );
    $("preview-enabled").addEventListener("change", () => {
      togglePreview();
      invalidateValidation();
    });
    $("reference-add-button").addEventListener("click", () =>
      chooseCatalogTracks("reference").catch((error) =>
        reportError(error, "Could not add references"),
      ),
    );
    $("reference-set-load-button").addEventListener("click", () => loadReferenceSet());
    $("references-equalize-button").addEventListener("click", () => {
      state.jobReferences.forEach((reference) => {
        reference.level_weight = 1;
        reference.frequency_weight = 1;
      });
      renderJobReferences();
      invalidateValidation();
    });
    $("references-save-set-button").addEventListener("click", saveJobReferencesAsSet);
    $("output-folder-choose-button").addEventListener(
      "click",
      chooseJobOutputDirectory,
    );
    $("output-folder-default-button").addEventListener(
      "click",
      useDefaultOutputDirectory,
    );
    $("output-folder-save-default-button").addEventListener(
      "click",
      saveDefaultOutputDirectory,
    );
    $("job-reset-button").addEventListener("click", () => {
      const notes = $("job-notes").value;
      applyDefaults({ clearSources: false });
      $("job-notes").value = notes;
      toast(
        "Processing, output, preview, and safety defaults restored; notes were preserved.",
        "good",
      );
    });
    $("job-export-selection-button").addEventListener("click", exportSelection);
    $("job-validate-button").addEventListener("click", validateJob);
    $("dry-run-button").addEventListener("click", () => startJob(true));
    $("render-button").addEventListener("click", () => startJob(false));
    $("review-masters-button").addEventListener("click", () => {
      reviewCompletedMasters().catch((error) =>
        reportError(error, "Could not open completed masters"),
      );
    });

    let catalogSearchTimer = null;
    $("catalog-search").addEventListener("input", () => {
      window.clearTimeout(catalogSearchTimer);
      catalogSearchTimer = window.setTimeout(() => {
        refreshCatalog().catch((error) =>
          reportError(error, "Could not filter the catalog"),
        );
      }, 220);
    });
    $("catalog-role-filter").addEventListener("change", () => {
      refreshCatalog().catch((error) => reportError(error, "Could not filter the catalog"));
    });
    $("catalog-archived-filter").addEventListener("change", () => {
      refreshCatalog().catch((error) => reportError(error, "Could not filter the catalog"));
    });
    $("catalog-discarded-filter").addEventListener("change", () => {
      refreshCatalog().catch((error) =>
        reportError(error, "Could not update discarded master visibility"),
      );
    });
    $("catalog-refresh-button").addEventListener("click", () => {
      refreshCatalog().catch((error) => reportError(error, "Could not refresh catalog"));
    });
    $("master-library-select-all").addEventListener("change", () => {
      const selected = $("master-library-select-all").checked;
      for (const entry of state.catalogRows) {
        if (activeMasterVersions(entry).length) {
          setSourceExportSelection(entry, selected);
        }
      }
      renderCatalog();
    });
    $("master-export-clear-button").addEventListener("click", () => {
      state.selectedMasterExports.clear();
      renderCatalog();
    });
    $("master-export-button").addEventListener("click", exportSelectedMasters);
    $("master-version-select-all").addEventListener("change", () => {
      const entry = selectedLibrarySource();
      const version = selectedMasterVersion(entry);
      if (!entry || !version) {
        return;
      }
      setVersionExportSelection(
        entry,
        version,
        $("master-version-select-all").checked,
      );
      renderCatalog();
    });
    $("catalog-verify-all-button").addEventListener("click", () => verifyTracks());
    $("track-verify-button").addEventListener("click", () => {
      const track = selectedTrack();
      if (track) {
        verifyTracks(track.track_id);
      }
    });
    $("track-relink-button").addEventListener("click", relinkSelectedTrack);
    $("track-play-button").addEventListener("click", () => {
      const track = selectedTrack();
      if (track) {
        playTrack(track, {
          label: `Original · ${track.label || basename(track.preferred_path)}`,
        });
      }
    });
    $("track-compare-button").addEventListener("click", () => {
      const entry = selectedLibrarySource();
      const version = selectedMasterVersion(entry);
      if (entry && version) {
        try {
          compareOriginalWithMaster(entry, version, {
            autoplay: true,
            preserveTime: false,
          });
        } catch (error) {
          reportError(error, "Could not start A/B comparison");
        }
      }
    });
    $("track-rename-button").addEventListener("click", renameSelectedTrack);
    $("track-role-target-button").addEventListener("click", useSelectedTrackAsTarget);
    $("track-role-reference-button").addEventListener(
      "click",
      useSelectedTrackAsReference,
    );
    $("track-archive-button").addEventListener("click", () =>
      setSelectedTrackArchived(true),
    );
    $("track-restore-button").addEventListener("click", () =>
      setSelectedTrackArchived(false),
    );

    $("set-create-button").addEventListener("click", createReferenceSet);
    $("set-use-button").addEventListener("click", () => loadReferenceSet(selectedSet()));
    $("set-edit-button").addEventListener("click", editReferenceSetMembers);
    $("set-rename-button").addEventListener("click", renameReferenceSet);
    $("set-delete-button").addEventListener("click", deleteReferenceSet);

    $("runs-refresh-button").addEventListener("click", () => {
      refreshRuns().catch((error) => reportError(error, "Could not refresh runs"));
    });
    $("run-recover-file-button").addEventListener("click", recoverFromPicker);
    $("run-recover-button").addEventListener("click", () => {
      if (state.selectedRun && state.selectedRun.manifest_path) {
        recoverManifest(state.selectedRun.manifest_path).catch((error) =>
          reportError(error, "Could not recover run indexing"),
        );
      }
    });
    $("run-open-folder-button").addEventListener("click", () => {
      if (state.selectedRun) {
        openFolder(state.selectedRun.manifest_path);
      }
    });

    for (const id of ["events-search", "events-level-filter", "events-stage-filter"]) {
      $(id).addEventListener(id === "events-search" ? "input" : "change", renderEvents);
    }
    $("events-clear-view-button").addEventListener("click", () => {
      state.events.forEach((event) => state.hiddenEventKeys.add(event._key));
      state.events = [];
      state.selectedEventKey = null;
      renderEvents();
      toast("The in-browser event view was cleared. Durable JSONL evidence was not changed.");
    });

    $("diagnostics-refresh-button").addEventListener("click", runDiagnostics);
    $("logs-refresh-button").addEventListener("click", () => {
      refreshLogs().catch((error) => reportError(error, "Could not refresh logs"));
    });
    $("log-open-folder-button").addEventListener("click", () => {
      if (state.selectedLog) {
        openFolder(state.selectedLog.path);
      }
    });

    $("audition-slot-a").addEventListener("click", () =>
      activateAuditionSlot("a"),
    );
    $("audition-slot-b").addEventListener("click", () =>
      activateAuditionSlot("b"),
    );
    $("audition-picker-a").addEventListener("change", () =>
      chooseAuditionCandidate("a", $("audition-picker-a").value),
    );
    $("audition-picker-b").addEventListener("change", () =>
      chooseAuditionCandidate("b", $("audition-picker-b").value),
    );
    $("audition-swap-button").addEventListener("click", swapAuditionSlots);
    $("audition-clear-button").addEventListener("click", () => {
      clearAudition();
    });
    $("audition-player").addEventListener("error", () => {
      if ($("audition-player").getAttribute("src")) {
        toast(
          "This file could not be played by the browser. Verify its catalog location and codec support.",
          "danger",
          10000,
        );
      }
    });
    document.addEventListener("keydown", (event) => {
      const target = event.target;
      const isEditing =
        target instanceof HTMLInputElement ||
        target instanceof HTMLSelectElement ||
        target instanceof HTMLTextAreaElement ||
        target instanceof HTMLButtonElement ||
        (target instanceof HTMLElement && target.isContentEditable) ||
        $("app-dialog").open;
      if (isEditing || event.altKey || event.ctrlKey || event.metaKey) {
        return;
      }
      if (event.key === "1" && state.audition.a) {
        event.preventDefault();
        activateAuditionSlot("a");
      } else if (event.key === "2" && state.audition.b) {
        event.preventDefault();
        activateAuditionSlot("b");
      }
    });
  }

  async function initialize() {
    bindControls();
    const requestedTab = window.location.hash.replace(/^#/, "");
    const validTab = all("[data-panel]").some(
      (panel) => panel.dataset.panel === requestedTab,
    );
    activateTab(validTab ? requestedTab : "master-job");
    activateRunSubtab("artifacts");
    renderJobTargets();
    renderJobReferences();
    renderAuditionDock();
    setStatus("Connecting to the private workbench…", "run");
    const workspaceReady = await refreshAll({ firstLoad: true, reloadDefaults: true });
    if (!workspaceReady) {
      document.body.dataset.appState = "error";
      return;
    }
    await runDiagnostics();
    document.body.dataset.appState = "ready";
    pollTask();
  }

  initialize().catch((error) => {
    document.body.dataset.appState = "error";
    reportError(error, "Portal initialization failed");
  });
})();
