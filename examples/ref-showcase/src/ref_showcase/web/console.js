(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => Array.from(document.querySelectorAll(selector));
  const state = {
    services: { db: false, storage: false, inference: false, analytics: false },
    documents: [],
    offset: 0,
    limit: 10,
    total: 0,
    chat: [],
    controllers: new Map(),
    pollTimer: null,
    unloading: false,
  };

  class ApiError extends Error {
    constructor(status, detail) {
      super(`HTTP ${status}: ${detail}`);
      this.status = status;
      this.detail = detail;
    }
  }

  function makeId(prefix) {
    if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
      return `${prefix}-${globalThis.crypto.randomUUID()}`;
    }
    return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function storedId(key, prefix) {
    let value = localStorage.getItem(key);
    if (!value) {
      value = makeId(prefix);
      localStorage.setItem(key, value);
    }
    return value;
  }

  function identity() {
    return {
      distinct: storedId("ref-showcase-distinct-id", "browser"),
      session: storedId("ref-showcase-session-id", "session"),
    };
  }

  function identityHeaders() {
    const ids = identity();
    return { "X-Distinct-ID": ids.distinct, "X-Session-ID": ids.session };
  }

  function showIdentity() {
    const ids = identity();
    $("#distinct-id").textContent = ids.distinct;
    $("#session-id").textContent = ids.session;
    $("#identify-id").value = ids.distinct;
    $("#alias-previous").value = ids.distinct;
  }

  function captureCorrelation(response) {
    const value = response.headers.get("X-Correlation-ID");
    if (value) $("#correlation-id").textContent = value;
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    const init = { ...options, headers };
    if (options.json !== undefined) {
      headers.set("Content-Type", "application/json");
      init.body = JSON.stringify(options.json);
      delete init.json;
    }
    const response = await fetch(path, init);
    captureCorrelation(response);
    if (!response.ok) {
      let detail = response.statusText || "request failed";
      try {
        const body = await response.json();
        detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || body);
      } catch {
        const text = await response.text();
        if (text) detail = text;
      }
      throw new ApiError(response.status, detail);
    }
    if (response.status === 204) return null;
    const type = response.headers.get("content-type") || "";
    return type.includes("json") ? response.json() : response.text();
  }

  function errorText(error) {
    if (error instanceof ApiError) return error.message;
    if (error && error.name === "AbortError") return "Request superseded.";
    return error instanceof Error ? error.message : String(error);
  }

  function announce(message, error = false) {
    const notice = $("#notice");
    notice.textContent = message;
    notice.classList.toggle("error", error);
  }

  function report(error, context) {
    if (error && error.name === "AbortError") return;
    announce(`${context}: ${errorText(error)}`, true);
  }

  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  }

  function jsonOutput(target, value) {
    $(target).textContent = JSON.stringify(value, null, 2);
  }

  function setBusy(form, busy) {
    const submit = form.querySelector('button[type="submit"]');
    if (submit) submit.disabled = busy;
    form.setAttribute("aria-busy", String(busy));
  }

  async function submitOnce(form, task) {
    if (form.dataset.submitting === "true") return;
    form.dataset.submitting = "true";
    setBusy(form, true);
    try {
      await task();
    } finally {
      form.dataset.submitting = "false";
      setBusy(form, false);
    }
  }

  function supersede(key) {
    const previous = state.controllers.get(key);
    if (previous) previous.abort();
    const controller = new AbortController();
    state.controllers.set(key, controller);
    return controller;
  }

  function setDot(name, available, availableText, missingText) {
    const dot = $(`#${name}-dot`);
    const label = $(`#${name}-state`);
    dot.className = `dot ${available ? "good" : "warn"}`;
    label.textContent = available ? availableText : missingText;
  }

  function applyAvailability() {
    const docsEnabled = state.services.db;
    const createEnabled = state.services.db && state.services.storage;
    $("#document-form").classList.toggle("unavailable", !createEnabled);
    $("#document-form").querySelector('button[type="submit"]').disabled = !createEnabled;
    $("#seed-form").classList.toggle("unavailable", !createEnabled);
    $("#seed-form").querySelector('button[type="submit"]').disabled = !createEnabled;
    $("#upload-form").classList.toggle("unavailable", !state.services.storage);
    $("#upload-form").querySelector('button[type="submit"]').disabled = !state.services.storage;
    $("#document-filter").querySelector("button").disabled = !docsEnabled;
    $("#search-form").querySelector('button[type="submit"]').disabled = !docsEnabled;
    $$(".inference-required").forEach((panel) => {
      panel.classList.toggle("unavailable", !state.services.inference);
      panel.querySelectorAll("button, input, textarea, select").forEach((control) => {
        control.disabled = !state.services.inference;
      });
    });
    $("#load-models").disabled = !state.services.inference;
    ["capture-form", "identify-form", "alias-form"].forEach((id) => {
      const form = $(`#${id}`);
      form.classList.toggle("unavailable", !state.services.analytics);
      form.querySelectorAll("button, input, textarea").forEach((control) => {
        control.disabled = !state.services.analytics;
      });
    });
    $("#analytics-refresh").disabled = !state.services.analytics;
  }

  function renderQueue(data) {
    const container = $("#queue-stats");
    container.replaceChildren();
    const list = el("dl");
    Object.entries(data.depth || {}).forEach(([name, depth]) => {
      list.append(el("dt", name), el("dd", depth));
    });
    list.append(el("dt", "dead letter"), el("dd", data.dead_letter));
    container.append(list);
  }

  async function refreshOverview({ featureProbe = false } = {}) {
    const checks = await Promise.allSettled([
      api("/healthz"),
      api("/readyz"),
      api("/queue/stats"),
    ]);
    const health = checks[0];
    const ready = checks[1];
    const queues = checks[2];
    const alive = health.status === "fulfilled";
    $("#health-dot").className = `dot ${alive ? "good" : "bad"}`;
    $("#health-state").textContent = alive ? "Process alive" : "Process unreachable";

    if (ready.status === "fulfilled") {
      const serviceChecks = ready.value.checks || {};
      state.services.db = serviceChecks.db === true;
      state.services.storage = serviceChecks.storage === true;
      if (Object.hasOwn(serviceChecks, "inference")) {
        state.services.inference = serviceChecks.inference === true;
      }
      if (Object.hasOwn(serviceChecks, "analytics")) {
        state.services.analytics = serviceChecks.analytics === true;
      }
    } else if (ready.reason instanceof ApiError && ready.reason.status === 503) {
      // api() rejects a useful readiness body, so feature probes below establish exact states.
      state.services.db = false;
      state.services.storage = false;
    }
    if (queues.status === "fulfilled") {
      state.services.db = true;
      renderQueue(queues.value);
    } else {
      $("#queue-stats").textContent = errorText(queues.reason);
    }

    if (featureProbe) {
      const features = await Promise.allSettled([
        api("/storage/objects?limit=1"),
        api("/inference/models"),
        api("/analytics/events?limit=1"),
      ]);
      state.services.storage = features[0].status === "fulfilled";
      state.services.inference = features[1].status === "fulfilled";
      state.services.analytics = features[2].status === "fulfilled";
    }

    setDot("db", state.services.db, "Relational store ready", "Set DATABASE_URL");
    setDot("storage", state.services.storage, "Bucket reachable", "Set STORAGE_*");
    setDot("inference", state.services.inference, "Gateway configured", "Optional; fallback search active");
    setDot("analytics", state.services.analytics, "Event store ready", "Optional; set MINI_ANALYTICS_DSN");
    $("#analytics-guidance").hidden = state.services.analytics;
    applyAvailability();
  }

  function schedulePolling() {
    clearTimeout(state.pollTimer);
    if (state.unloading || document.hidden) return;
    state.pollTimer = setTimeout(async () => {
      await refreshOverview();
      schedulePolling();
    }, 15000);
  }

  function renderDocumentOptions() {
    ["#chat-document", "#summary-document"].forEach((selector) => {
      const select = $(selector);
      const selected = select.value;
      select.replaceChildren();
      state.documents.forEach((document) => {
        const option = el("option", `${document.id} — ${document.title}`);
        option.value = String(document.id);
        select.append(option);
      });
      if (selected) select.value = selected;
    });
  }

  function actionButton(label, handler, className) {
    const button = el("button", label, className);
    button.type = "button";
    button.addEventListener("click", handler);
    return button;
  }

  function renderDocuments(page) {
    state.documents = page.items || [];
    state.total = page.total;
    state.offset = page.offset;
    const body = $("#documents-body");
    body.replaceChildren();
    if (!state.documents.length) {
      const row = el("tr");
      const cell = el("td", "No documents match these filters.");
      cell.colSpan = 5;
      row.append(cell);
      body.append(row);
    }
    state.documents.forEach((document) => {
      const row = el("tr");
      row.append(
        el("td", document.title),
        el("td", document.status),
        el("td", (document.tags || []).join(", ") || "—"),
        el("td", document.chunk_count),
      );
      const actions = el("td");
      actions.append(actionButton("Inspect", () => loadDocumentDetail(document.id)));
      row.append(actions);
      body.append(row);
    });
    const from = page.total ? page.offset + 1 : 0;
    const to = Math.min(page.offset + page.limit, page.total);
    $("#documents-page").textContent = `${from}–${to} of ${page.total}`;
    $("#documents-prev").disabled = page.offset === 0;
    $("#documents-next").disabled = page.offset + page.limit >= page.total;
    renderDocumentOptions();
  }

  async function loadDocuments(offset = state.offset) {
    const controller = supersede("documents");
    const params = new URLSearchParams({ limit: state.limit, offset });
    const tag = $("#filter-tag").value.trim();
    const status = $("#filter-status").value.trim();
    if (tag) params.set("tag", tag);
    if (status) params.set("status", status);
    try {
      renderDocuments(await api(`/documents?${params}`, { signal: controller.signal }));
    } catch (error) {
      report(error, "Could not list documents");
    }
  }

  async function loadDocumentDetail(id) {
    try {
      const detail = await api(`/documents/${id}`);
      $("#detail-title").textContent = detail.document.title;
      const container = $("#document-detail");
      container.replaceChildren();
      const metadata = el("dl");
      [
        ["Status", detail.document.status],
        ["Source", detail.document.source],
        ["Tags", detail.document.tags.join(", ") || "—"],
        ["Embedding", detail.chunks.every((chunk) => chunk.embedded) ? "all chunks embedded" : "pending"],
        ["Summary key", detail.document.summary_key || "pending"],
      ].forEach(([term, value]) => metadata.append(el("dt", term), el("dd", value)));
      container.append(metadata, el("h4", "Chunks"));
      detail.chunks.forEach((chunk) => {
        container.append(el("div", `${chunk.ordinal}. ${chunk.content}`, "chunk"));
      });
      $("#document-dialog").showModal();
    } catch (error) {
      report(error, "Could not load document detail");
    }
  }

  async function loadObjects() {
    const controller = supersede("objects");
    const params = new URLSearchParams({
      prefix: $("#storage-prefix").value,
      limit: $("#storage-limit").value,
    });
    try {
      const data = await api(`/storage/objects?${params}`, { signal: controller.signal });
      const body = $("#objects-body");
      body.replaceChildren();
      if (!data.items.length) {
        const row = el("tr");
        const cell = el("td", "No objects under this prefix.");
        cell.colSpan = 4;
        row.append(cell);
        body.append(row);
      }
      data.items.forEach((object) => {
        const row = el("tr");
        row.append(el("td", object.key), el("td", object.size), el("td", object.last_modified));
        const actions = el("td");
        const download = el("a", "Download", "action");
        download.href = `/storage/object/content?${new URLSearchParams({ key: object.key })}`;
        actions.append(
          download,
          actionButton("Presign", () => {
            $("#presign-key").value = object.key;
            $("#presign-key").focus();
          }),
          actionButton("Delete", () => deleteObject(object.key)),
        );
        row.append(actions);
        body.append(row);
      });
    } catch (error) {
      report(error, "Could not list storage objects");
    }
  }

  async function deleteObject(key) {
    if (!globalThis.confirm(`Delete object "${key}"? This cannot be undone from the console.`)) return;
    try {
      await api(`/storage/object?${new URLSearchParams({ key })}`, { method: "DELETE" });
      announce(`Deleted storage object ${key}.`);
      await loadObjects();
    } catch (error) {
      report(error, "Could not delete object");
    }
  }

  function renderSearch(data) {
    const container = $("#search-results");
    container.replaceChildren();
    if (!data.hits.length) {
      container.append(el("p", "No embedded chunks matched. Seed or process documents first."));
      return;
    }
    data.hits.forEach((hit) => {
      const item = el("article", undefined, "search-hit");
      item.append(el("strong", hit.title), el("p", `Score ${hit.score} · document ${hit.document_id} · chunk ${hit.chunk_id}`));
      item.append(actionButton("Inspect document", () => loadDocumentDetail(hit.document_id), "secondary"));
      container.append(item);
    });
  }

  function renderChat() {
    const transcript = $("#chat-transcript");
    transcript.replaceChildren();
    state.chat.forEach((turn) => {
      const item = el("div", undefined, "chat-turn");
      item.append(el("strong", turn.role), el("span", turn.content));
      transcript.append(item);
    });
  }

  async function streamSummary(documentId, form) {
    const controller = supersede("summary-stream");
    const output = $("#summary-output");
    output.textContent = "";
    setBusy(form, true);
    try {
      const response = await fetch(`/documents/${documentId}/summary/stream`, {
        headers: identityHeaders(),
        signal: controller.signal,
      });
      captureCorrelation(response);
      if (!response.ok) {
        let detail = response.statusText;
        try {
          const body = await response.json();
          detail = body.detail || detail;
        } catch {
          detail = (await response.text()) || detail;
        }
        throw new ApiError(response.status, detail);
      }
      if (!response.body) throw new Error("Streaming is unsupported by this browser.");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
        const frames = buffer.split("\n\n");
        buffer = frames.pop() || "";
        frames.forEach((frame) => {
          frame.split("\n").forEach((line) => {
            if (!line.startsWith("data:")) return;
            const token = line.slice(5).trimStart();
            if (token !== "[DONE]") output.textContent += token;
          });
        });
        if (done) break;
      }
      announce("Summary stream completed.");
    } catch (error) {
      report(error, "Could not stream summary");
    } finally {
      setBusy(form, false);
    }
  }

  function renderFunnel(data) {
    const container = $("#funnel-output");
    container.replaceChildren(el("p", `${data.converted} of ${data.entered} converted (${Math.round(data.overall_conversion * 100)}%)`));
    const list = el("ol");
    data.steps.forEach((step) => list.append(el("li", `${step.event}: ${step.count}`)));
    container.append(list);
  }

  function renderRetention(data) {
    const container = $("#retention-output");
    container.replaceChildren();
    const grid = el("div", undefined, "retention-grid");
    data.cells.forEach((cell) => {
      grid.append(el("div", `${cell.cohort_week} · P${cell.period}: ${cell.active}`, "retention-cell"));
    });
    container.append(grid);
  }

  function renderEvents(data) {
    const container = $("#events-output");
    container.replaceChildren();
    const list = el("ol");
    data.events.forEach((event) => {
      list.append(el("li", `${event.event} · ${event.distinct_id} · ${event.timestamp}`));
    });
    container.append(list);
  }

  async function refreshAnalytics() {
    const results = await Promise.allSettled([
      api("/analytics/funnel"),
      api("/analytics/retention"),
      api("/analytics/events?limit=20"),
      api("/analytics/sql"),
    ]);
    if (results[0].status === "rejected" && results[0].reason instanceof ApiError && results[0].reason.status === 503) {
      state.services.analytics = false;
      applyAvailability();
      $("#analytics-guidance").hidden = false;
      report(results[0].reason, "Analytics unavailable");
      return;
    }
    if (results[0].status === "fulfilled") renderFunnel(results[0].value);
    if (results[1].status === "fulfilled") renderRetention(results[1].value);
    if (results[2].status === "fulfilled") renderEvents(results[2].value);
    if (results[3].status === "fulfilled") jsonOutput("#sql-output", results[3].value);
    const failed = results.find((result) => result.status === "rejected");
    if (failed) report(failed.reason, "One analytics report failed");
  }

  function parseProperties(selector) {
    const text = $(selector).value.trim();
    if (!text) return {};
    const parsed = JSON.parse(text);
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
      throw new Error("Properties must be a JSON object.");
    }
    return parsed;
  }

  function wireForms() {
    $("#document-form").addEventListener("submit", (event) => {
      event.preventDefault();
      submitOnce(event.currentTarget, async () => {
        try {
          const data = await api("/documents", {
            method: "POST",
            headers: identityHeaders(),
            json: {
              title: $("#document-title").value.trim(),
              text: $("#document-text").value,
              tags: $("#document-tags").value.split(",").map((tag) => tag.trim()).filter(Boolean),
            },
          });
          jsonOutput("#document-result", data);
          announce(`Document ${data.document_id} accepted. Run make worker if processing remains pending.`);
          await Promise.all([loadDocuments(0), refreshOverview()]);
        } catch (error) {
          report(error, "Could not create document");
        }
      });
    });

    $("#upload-form").addEventListener("submit", (event) => {
      event.preventDefault();
      submitOnce(event.currentTarget, async () => {
        const body = new FormData();
        const file = $("#upload-file").files[0];
        body.append("file", file);
        body.append("prefix", $("#upload-prefix").value);
        try {
          const data = await api("/storage/uploads", { method: "POST", body });
          jsonOutput("#upload-result", data);
          announce(`Uploaded ${data.key}. It has not entered the document pipeline.`);
        } catch (error) {
          report(error, "Could not upload object");
        }
      });
    });

    $("#seed-form").addEventListener("submit", (event) => {
      event.preventDefault();
      submitOnce(event.currentTarget, async () => {
        try {
          const data = await api(`/showcase/seed?${new URLSearchParams({ count: $("#seed-count").value })}`, { method: "POST" });
          jsonOutput("#seed-result", data);
          announce(`Seed complete: ${data.created} created, ${data.skipped} skipped, ${data.jobs_processed} jobs processed.`);
          await Promise.all([loadDocuments(0), refreshOverview(), state.services.analytics ? refreshAnalytics() : Promise.resolve()]);
        } catch (error) {
          report(error, "Could not seed samples");
        }
      });
    });

    $("#document-filter").addEventListener("submit", (event) => {
      event.preventDefault();
      loadDocuments(0);
    });
    $("#storage-filter").addEventListener("submit", (event) => {
      event.preventDefault();
      loadObjects();
    });
    $("#presign-form").addEventListener("submit", (event) => {
      event.preventDefault();
      submitOnce(event.currentTarget, async () => {
        try {
          const data = await api("/storage/presign", {
            method: "POST",
            json: {
              key: $("#presign-key").value,
              method: $("#presign-method").value,
              expires_in: Number($("#presign-expiry").value),
            },
          });
          const output = $("#presign-result");
          output.replaceChildren();
          const link = el("a", `${data.method.toUpperCase()} ${data.key}`);
          link.href = data.url;
          link.rel = "noreferrer";
          output.append(link, document.createTextNode(` · expires in ${data.expires_in}s`));
        } catch (error) {
          report(error, "Could not generate presigned URL");
        }
      });
    });

    $("#search-form").addEventListener("submit", (event) => {
      event.preventDefault();
      submitOnce(event.currentTarget, async () => {
        const controller = supersede("search");
        try {
          const data = await api("/search", {
            method: "POST",
            signal: controller.signal,
            headers: identityHeaders(),
            json: { query: $("#search-query").value, limit: Number($("#search-limit").value) },
          });
          renderSearch(data);
        } catch (error) {
          report(error, "Search failed");
        }
      });
    });

    $("#chat-form").addEventListener("submit", (event) => {
      event.preventDefault();
      submitOnce(event.currentTarget, async () => {
        const message = $("#chat-message").value.trim();
        state.chat.push({ role: "user", content: message });
        renderChat();
        try {
          const data = await api(`/documents/${$("#chat-document").value}/chat`, {
            method: "POST",
            headers: identityHeaders(),
            json: { messages: state.chat, max_tokens: 256 },
          });
          state.chat.push({ role: "assistant", content: data.reply });
          $("#chat-message").value = "";
          renderChat();
        } catch (error) {
          state.chat.pop();
          renderChat();
          report(error, "Chat failed");
        }
      });
    });
    $("#summary-form").addEventListener("submit", (event) => {
      event.preventDefault();
      streamSummary($("#summary-document").value, event.currentTarget);
    });

    $("#capture-form").addEventListener("submit", (event) => {
      event.preventDefault();
      submitOnce(event.currentTarget, async () => {
        try {
          const ids = identity();
          const data = await api("/analytics/capture", {
            method: "POST",
            json: { distinct_id: ids.distinct, session_id: ids.session, event: $("#capture-event").value, properties: parseProperties("#capture-properties") },
          });
          jsonOutput("#capture-result", data);
        } catch (error) {
          report(error, "Capture failed");
        }
      });
    });
    $("#identify-form").addEventListener("submit", (event) => {
      event.preventDefault();
      submitOnce(event.currentTarget, async () => {
        try {
          const data = await api("/analytics/identify", {
            method: "POST",
            json: { distinct_id: $("#identify-id").value, properties: parseProperties("#identify-properties") },
          });
          jsonOutput("#identify-result", data);
        } catch (error) {
          report(error, "Identify failed");
        }
      });
    });
    $("#alias-form").addEventListener("submit", (event) => {
      event.preventDefault();
      submitOnce(event.currentTarget, async () => {
        try {
          const data = await api("/analytics/alias", {
            method: "POST",
            json: { previous_id: $("#alias-previous").value, distinct_id: $("#alias-current").value },
          });
          jsonOutput("#alias-result", data);
        } catch (error) {
          report(error, "Alias failed");
        }
      });
    });
  }

  function wireControls() {
    $("#theme").addEventListener("change", (event) => {
      document.documentElement.dataset.theme = event.target.value;
      localStorage.setItem("ref-showcase-theme", event.target.value);
    });
    $("#refresh-overview").addEventListener("click", () => refreshOverview({ featureProbe: true }));
    $("#documents-prev").addEventListener("click", () => loadDocuments(Math.max(0, state.offset - state.limit)));
    $("#documents-next").addEventListener("click", () => loadDocuments(state.offset + state.limit));
    $("#detail-close").addEventListener("click", () => $("#document-dialog").close());
    $$("[data-debug]").forEach((button) => {
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          jsonOutput(`#${button.dataset.target}`, await api(button.dataset.debug));
        } catch (error) {
          report(error, `Could not load ${button.dataset.debug}`);
        } finally {
          button.disabled = false;
        }
      });
    });
    $("#load-models").addEventListener("click", async () => {
      try {
        jsonOutput("#models-output", await api("/inference/models"));
      } catch (error) {
        report(error, "Could not list models");
      }
    });
    $("#chat-reset").addEventListener("click", () => {
      state.chat = [];
      renderChat();
    });
    $("#analytics-refresh").addEventListener("click", refreshAnalytics);
    $("#identity-reset").addEventListener("click", () => {
      localStorage.removeItem("ref-showcase-distinct-id");
      localStorage.removeItem("ref-showcase-session-id");
      showIdentity();
      announce("Browser analytics identity reset.");
    });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) clearTimeout(state.pollTimer);
      else {
        refreshOverview();
        schedulePolling();
      }
    });
    globalThis.addEventListener("beforeunload", () => {
      state.unloading = true;
      clearTimeout(state.pollTimer);
      state.controllers.forEach((controller) => controller.abort());
    });
  }

  async function init() {
    const theme = localStorage.getItem("ref-showcase-theme") || "system";
    document.documentElement.dataset.theme = theme;
    $("#theme").value = theme;
    $("#grafana-link").href = `${location.protocol}//${location.hostname}:3000/`;
    showIdentity();
    wireForms();
    wireControls();
    announce("Console ready. Checking services…");
    await refreshOverview({ featureProbe: true });
    if (state.services.db) await loadDocuments(0);
    if (state.services.storage) await loadObjects();
    if (state.services.analytics) await refreshAnalytics();
    announce("Service state refreshed. HTTP 503 responses are shown as feature setup guidance.");
    schedulePolling();
  }

  init().catch((error) => report(error, "Console initialization failed"));
})();
