// @ts-check
// Synapse sidebar. Renders the state the extension posts; sends user intents
// back as messages. All text goes through textContent (never innerHTML).
(function () {
  const vscode = acquireVsCodeApi();
  const saved = vscode.getState() || {};
  /** @type {any} */
  let state = null;
  let mode = saved.mode === "search" ? "search" : "ask";

  /** Tiny DOM builder: h("div", {class: "x", onclick}, child, "text"). */
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === undefined || v === null || v === false) continue;
      if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "class") el.className = v;
      else el.setAttribute(k, v === true ? "" : String(v));
    }
    for (const c of children.flat()) {
      if (c === null || c === undefined || c === false) continue;
      el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
  }
  const icon = (name, extra = "") => h("span", { class: `codicon codicon-${name} ${extra}`.trim(), "aria-hidden": "true" });
  const send = (msg) => vscode.postMessage(msg);
  const run = (command) => send({ type: "run", command });
  const fmt = (n) => (n >= 10000 ? `${(n / 1000).toFixed(n >= 100000 ? 0 : 1)}k` : n.toLocaleString());
  const basename = (p) => (p ? p.split(/[\\/]/).pop() : "");

  function ago(ms) {
    if (!ms) return "";
    const s = Math.max(0, Math.round((Date.now() - ms) / 1000));
    if (s < 45) return "just now";
    const m = Math.round(s / 60);
    if (m < 60) return `${m} min ago`;
    const hr = Math.round(m / 60);
    if (hr < 24) return `${hr} h ago`;
    return `${Math.round(hr / 24)} d ago`;
  }

  // Composer (built once so typing and focus survive state updates) --------

  const input = /** @type {HTMLTextAreaElement} */ (
    h("textarea", { rows: "2", "aria-label": "Question", spellcheck: "false" })
  );
  input.value = saved.draft || "";
  const sendBtn = /** @type {HTMLButtonElement} */ (
    h("button", { class: "send", title: "Send (Enter)", "aria-label": "Send", onclick: submit }, icon("arrow-up"))
  );
  const askBtn = h("button", { type: "button", onclick: () => setMode("ask") }, icon("sparkle"), "Ask");
  const searchBtn = h("button", { type: "button", onclick: () => setMode("search") }, icon("search"), "Search");
  const hint = h("div", { class: "composer-hint" });
  const composer = h(
    "div",
    {},
    h("div", { class: "composer" }, input, h("div", { class: "composer-bar" }, h("div", { class: "segmented", role: "group", "aria-label": "Mode" }, askBtn, searchBtn), h("div", { class: "spacer" }), sendBtn)),
    hint
  );

  function persist() {
    vscode.setState({ mode, draft: input.value });
  }

  function setMode(next) {
    mode = next;
    persist();
    renderComposer();
    input.focus();
  }

  function autosize() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
  }

  function submit() {
    const text = input.value.trim();
    if (!text) return;
    send({ type: mode, text });
  }

  input.addEventListener("input", () => {
    persist();
    autosize();
    sendBtn.disabled = !input.value.trim();
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      submit();
    }
  });

  function renderComposer() {
    askBtn.setAttribute("aria-pressed", String(mode === "ask"));
    searchBtn.setAttribute("aria-pressed", String(mode === "search"));
    askBtn.title = "Answer with your AI, grounded in the graph and checked against it";
    searchBtn.title = "Find relevant symbols and relationships (local, no AI)";
    input.placeholder = mode === "ask" ? "Ask anything about this codebase…" : "Search symbols, features, relationships…";
    sendBtn.disabled = !input.value.trim();
    hint.replaceChildren();
    if (mode === "ask" && state) {
      const ai = state.ai || { label: "", available: false };
      hint.append(
        h(
          "button",
          { class: `ai-chip${ai.available ? "" : " missing"}`, title: "Choose the AI Synapse uses", onclick: () => run("synapse.chooseModel") },
          icon(ai.available ? "sparkle" : "warning"),
          h("span", { class: "label" }, ai.label || "Choose AI"),
          icon("chevron-down")
        )
      );
    } else {
      hint.append(icon("lock"), h("span", {}, "Local search · no AI, nothing leaves your machine"));
    }
  }

  // Sections ------------------------------------------------------------------

  function statusSection() {
    const s = state;
    let dot = "idle";
    let text = [h("span", {}, "Not built yet")];
    if (s.task) {
      dot = "busy";
      text = [h("b", {}, s.task.title)];
    } else if (s.phase === "error") {
      dot = "error";
      text = [h("b", {}, "Something went wrong")];
    } else if (s.phase === "ready") {
      dot = "ready";
      text = [h("b", {}, "Graph ready"), s.builtAt ? ` · updated ${ago(s.builtAt)}` : ""];
    } else if (s.phase === "untrusted") {
      text = [h("span", {}, "Workspace not trusted")];
    } else if (s.phase === "no-workspace") {
      text = [h("span", {}, "No folder open")];
    }
    const section = h("section", {}, h("div", { class: "status", role: "status" }, h("span", { class: `dot ${dot}` }), h("span", { class: "text" }, ...text)));
    if (s.task) {
      section.append(
        h("div", { class: "task-bar" }, h("div", { class: "progress" })),
        h(
          "div",
          { class: "task-detail" },
          h("span", {}, s.task.detail || "Working…"),
          s.task.cancellable ? h("button", { class: "link", onclick: () => send({ type: "cancel" }) }, "Cancel") : null
        )
      );
    }
    return section;
  }

  function hero(title, body, action) {
    return h(
      "section",
      { class: "hero" },
      h("span", { class: "logo", role: "img", "aria-label": "Synapse" }),
      h("h2", {}, title),
      h("p", {}, body),
      action,
      h(
        "div",
        { class: "fineprint" },
        h("span", {}, icon("lock"), "Runs locally"),
        h("span", {}, icon("key"), "No API key"),
        h("span", {}, icon("globe"), "20+ languages")
      )
    );
  }

  function errorSection() {
    return h(
      "section",
      {},
      h(
        "div",
        { class: "notice", role: "alert" },
        icon("error"),
        h(
          "div",
          { class: "body" },
          h("div", {}, state.error || "Unknown error"),
          h(
            "div",
            { class: "actions" },
            h("button", { class: "btn", onclick: () => run("synapse.buildGraph") }, "Try again"),
            h("button", { class: "btn ghost", onclick: () => run("synapse.showLog") }, "Show log")
          )
        )
      )
    );
  }

  function statsSection() {
    const st = state.stats;
    const stat = (value, label, title) => h("div", { class: "stat", title }, h("div", { class: "value" }, fmt(value)), h("div", { class: "label" }, label));
    const section = h(
      "section",
      {},
      h(
        "div",
        { class: "stats" },
        stat(st.nodes, "symbols", `${st.nodes.toLocaleString()} nodes in ${st.files.toLocaleString()} files`),
        stat(st.edges, "relations", `${st.edges.toLocaleString()} edges: calls, imports, inheritance, …`),
        stat(st.communities, "communities", "Clusters of closely related code")
      )
    );
    if (st.semanticRelations > 0) {
      section.append(h("div", { class: "enrich-row" }, icon("sparkle"), `${st.semanticRelations.toLocaleString()} AI-inferred intent relations`));
    }
    return section;
  }

  function symbolsSection() {
    const items = state.stats.keySymbols || [];
    if (!items.length) return null;
    return h(
      "section",
      {},
      h("h3", { class: "eyebrow" }, "Key symbols"),
      h(
        "ul",
        { class: "list" },
        items.map((k) =>
          h(
            "li",
            {},
            h(
              "button",
              { class: "row", title: `${k.label} — ${k.file || ""}${k.line ? `:${k.line}` : ""}\n${k.degree} connections`, onclick: () => send({ type: "openSymbol", id: k.id }) },
              icon(/\)$/.test(k.label) ? "symbol-method" : "symbol-class"),
              h("span", { class: "name" }, k.label),
              h("span", { class: "where" }, basename(k.file)),
              h("span", { class: "count" }, k.degree)
            )
          )
        )
      )
    );
  }

  function toolsSection() {
    const tool = (iconName, title, sub, command) =>
      h("li", {}, h("button", { class: "row tool", onclick: () => run(command) }, icon(iconName), h("span", { class: "text" }, h("span", {}, title), h("span", { class: "sub" }, sub))));
    const enriched = state.stats && state.stats.semanticRelations > 0;
    return h(
      "section",
      {},
      h("h3", { class: "eyebrow" }, "Tools"),
      h(
        "ul",
        { class: "list" },
        tool("shield", "Verify code", "Catch APIs that don't exist in this repo", "synapse.verifyCode"),
        tool("type-hierarchy", "Graph map", "Explore the whole codebase visually", "synapse.openGraphMap"),
        tool("sparkle", enriched ? "Re-run AI enrichment" : "Enrich with AI", "Add intent relations like handles, validates", "synapse.enrichGraph")
      )
    );
  }

  function render() {
    const app = document.getElementById("app");
    if (!app || !state) return;
    const parts = [statusSection()];
    const hasGraph = !!state.stats;

    if (state.phase === "no-workspace") {
      parts.push(hero("Open a folder to begin", "Synapse maps the code in your workspace into a knowledge graph.", h("button", { class: "btn block", onclick: () => run("workbench.action.files.openFolder") }, "Open Folder")));
    } else if (state.phase === "untrusted") {
      parts.push(hero("Trust this workspace", "Synapse only analyzes code in trusted workspaces.", h("button", { class: "btn block", onclick: () => run("workbench.trust.manage") }, "Manage Workspace Trust")));
    } else {
      if (state.phase === "error") parts.push(errorSection());
      if (hasGraph) {
        renderComposer();
        parts.push(h("section", {}, composer), statsSection(), symbolsSection(), toolsSection());
      } else if (state.task) {
        parts.push(h("section", {}, h("p", { class: "muted" }, "Everything runs on your machine. You can keep working while Synapse finishes.")));
      } else if (state.phase !== "error") {
        parts.push(
          hero(
            "Map your codebase",
            "Build a knowledge graph of your code, then ask questions grounded in it and check AI-written code for APIs that don't exist.",
            h("button", { class: "btn block", onclick: () => run("synapse.buildGraph") }, icon("circuit-board"), "Build Graph")
          )
        );
      }
    }
    const focused = document.activeElement === input;
    app.replaceChildren(...parts.filter(Boolean));
    autosize();
    if (focused) input.focus();
  }

  window.addEventListener("message", (event) => {
    const msg = event.data;
    if (msg && msg.type === "state") {
      state = msg.state;
      render();
    } else if (msg && msg.type === "focusInput") {
      if (msg.mode) mode = msg.mode === "search" ? "search" : "ask";
      renderComposer();
      input.focus();
    } else if (msg && msg.type === "clearInput") {
      input.value = "";
      persist();
      renderComposer();
      autosize();
    }
  });

  setInterval(() => state && state.phase === "ready" && !state.task && render(), 30000);
  send({ type: "ready" });
})();
