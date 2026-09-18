// @ts-check
// Synapse results panel: answers, search results, symbol explanations and
// verification reports. All text uses textContent; the only HTML inserted is
// the answer body, which the extension renders with an escaping Markdown
// renderer (src/ui/markdown.ts).
(function () {
  const vscode = acquireVsCodeApi();

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
  const where = (ref) => (ref && ref.file ? `${ref.file}${ref.line ? `:${ref.line}` : ""}` : "");

  const KIND = {
    ask: { icon: "sparkle", label: "Answer" },
    search: { icon: "search", label: "Graph search" },
    explain: { icon: "symbol-method", label: "Symbol" },
    verify: { icon: "shield", label: "Code verification" },
  };
  const VERDICT_ICON = { ok: "pass-filled", warn: "warning", bad: "error", none: "info" };
  const CLAIM = {
    SUPPORTED: { icon: "pass-filled", pill: "ok", text: "Supported" },
    CONTRADICTED: { icon: "error", pill: "bad", text: "Contradicted" },
    UNKNOWN: { icon: "question", pill: "", text: "Unverified" },
  };

  function locButton(ref) {
    if (!ref || !ref.file) return null;
    return h(
      "button",
      {
        class: "loc",
        title: "Open in editor",
        onclick: (e) => {
          e.stopPropagation();
          send({ type: "open", file: ref.file, line: ref.line });
        },
      },
      where(ref)
    );
  }

  function header(kind, title, meta, opts = {}) {
    const k = KIND[kind];
    return h(
      "header",
      { class: "top" },
      h(
        "div",
        { class: "kicker" },
        icon(k.icon),
        k.label,
        opts.copy
          ? h("div", { class: "actions" }, h("button", { class: "icon-btn", title: "Copy answer", "aria-label": "Copy answer", onclick: () => send({ type: "copy", text: opts.copy }) }, icon("copy")))
          : null
      ),
      h("h1", { class: opts.code ? "code" : "" }, title),
      meta && meta.length ? h("div", { class: "meta" }, meta) : null
    );
  }

  function verdictBanner(v) {
    return h("div", { class: `verdict ${v.level}`, role: "status" }, icon(VERDICT_ICON[v.level] || "info"), h("div", {}, h("div", { class: "headline" }, v.headline), v.detail ? h("div", { class: "detail" }, v.detail) : null));
  }

  function claimItem(c) {
    const style = CLAIM[c.verdict] || CLAIM.UNKNOWN;
    return h(
      "li",
      { class: "item" },
      icon(style.icon),
      h(
        "div",
        { class: "main" },
        h("div", { class: "title code", title: c.raw || c.text }, c.text),
        c.reason ? h("div", { class: "sub" }, c.reason) : null,
        c.refs && c.refs.length ? h("div", { class: "refs" }, c.refs.map((r) => h("span", {}, r.label, " ", locButton(r)))) : null
      ),
      h("span", { class: `pill ${style.pill}` }, style.text)
    );
  }

  function group(title, count, items, open) {
    return h(
      "details",
      { class: "group", open: open || undefined },
      h("summary", {}, icon("chevron-right", "chev"), h("h3", { class: "eyebrow" }, `${title} · ${count}`)),
      h("ul", { class: "items" }, items)
    );
  }

  function emptyBox(iconName, text) {
    return h("div", { class: "empty" }, icon(iconName), text);
  }

  // Views -----------------------------------------------------------------------

  function renderLoading(v) {
    return [
      header(v.mode, v.title, v.subtitle ? [h("span", {}, v.subtitle)] : [], { code: v.mode === "explain" }),
      h(
        "main",
        {},
        h(
          "ul",
          { class: "steps" },
          (v.steps || []).map((s, i) => {
            const state = i < v.current ? "done" : i === v.current ? "active" : "";
            return h("li", { class: state }, icon(state === "done" ? "check" : state === "active" ? "loading" : "circle-large-outline", state === "active" ? "spin" : ""), s);
          })
        ),
        h("div", { class: "skeleton" }, h("div"), h("div"), h("div"))
      ),
    ];
  }

  function renderError(v) {
    return [
      header(v.mode, v.title, []),
      h(
        "main",
        {},
        h(
          "div",
          { class: "error-box", role: "alert" },
          icon("error"),
          h(
            "div",
            {},
            h("div", {}, v.message),
            h(
              "div",
              { class: "actions" },
              (v.actions || []).map((a, i) => h("button", { class: i === 0 ? "btn" : "btn ghost", onclick: () => send({ type: "run", command: a.command }) }, a.label))
            )
          )
        )
      ),
    ];
  }

  function renderAnswer(v) {
    const body = h("div", { class: "prose" });
    body.innerHTML = v.answerHtml; // produced by the extension's escaping renderer
    body.addEventListener("click", (e) => {
      const target = /** @type {HTMLElement} */ (e.target);
      if (target && target.matches("code.symbol")) send({ type: "openSymbol", label: target.textContent });
    });
    const claims = v.claims || [];
    return [
      header("ask", v.question, [h("span", {}, icon("sparkle"), v.providerLabel), h("span", {}, icon("type-hierarchy"), "Grounded in your code graph")], { copy: v.answerText }),
      h(
        "main",
        {},
        h("section", {}, verdictBanner(v.verdict)),
        h("section", {}, v.revised ? h("div", { class: "note" }, icon("history"), "Revised after the graph flagged claims in the first draft") : null, body),
        claims.length
          ? h("section", {}, group("Claims checked against the graph", claims.length, claims.map(claimItem), claims.some((c) => c.verdict !== "SUPPORTED")))
          : null,
        h("footer", { class: "foot" }, `Context: ${v.contextChars.toLocaleString()} characters retrieved with relationship-weighted, community-aware graph traversal, then each claim was validated against the graph (Synapse bidirectional reasoning).`)
      ),
    ];
  }

  function renderSearch(v) {
    const nodeItems = v.nodes.map((n) =>
      h(
        "li",
        { class: "item clickable", onclick: () => send({ type: "open", file: n.file, line: n.line }) },
        icon(/\)$/.test(n.label) ? "symbol-method" : /\.\w+$/.test(n.label) ? "file-code" : "symbol-class"),
        h("div", { class: "main" }, h("div", { class: "title code" }, n.label), n.community ? h("div", { class: "sub" }, n.community) : null),
        h("span", { class: "side" }, where(n))
      )
    );
    const edgeItems = v.edges.map((e) =>
      h(
        "li",
        { class: "item" },
        h("div", { class: "main" }, h("div", { class: "rel" }, e.from, h("span", { class: "arrow" }, "→"), h("span", { class: "kind" }, e.relation), h("span", { class: "arrow" }, "→"), e.to)),
        h("span", { class: "side" }, locButton(e))
      )
    );
    return [
      header("search", v.question, [
        h("span", {}, icon("lock"), "Local · no AI"),
        v.strategy ? h("span", { title: v.strategy }, icon("git-merge"), v.strategyShort) : null,
      ]),
      h(
        "main",
        {},
        v.nodes.length
          ? [
              h("section", {}, group("Relevant symbols", v.nodes.length, nodeItems, true)),
              edgeItems.length ? h("section", {}, group("Relationships", v.edges.length, edgeItems, true)) : null,
            ]
          : h("section", {}, emptyBox("search", "No matching symbols. Try different words, or Ask to let your AI reason over the graph."))
      ),
    ];
  }

  function renderExplain(v) {
    if (!v.node) {
      return [header("explain", v.symbol, [], { code: true }), h("main", {}, emptyBox("symbol-misc", `“${v.symbol}” isn't in the graph. It may be external, or the graph may need a rebuild.`))];
    }
    const n = v.node;
    const fact = (label, value) => h("div", { class: "fact" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value));
    const sections = v.groups.map((g) =>
      h(
        "section",
        {},
        h("h3", { class: "eyebrow" }, `${g.title} · ${g.items.length}`),
        h(
          "ul",
          { class: "items" },
          g.items.map((it) =>
            h(
              "li",
              { class: "item clickable", onclick: () => send({ type: "openSymbol", label: it.label }) },
              h("div", { class: "main" }, h("div", { class: "title code" }, it.label)),
              h("span", { class: "side" }, locButton(it))
            )
          )
        )
      )
    );
    return [
      header("explain", n.label, [locButton(n)], { code: true }),
      h(
        "main",
        {},
        h("section", {}, h("div", { class: "facts" }, fact("Kind", n.type || "code"), fact("Community", n.community || "—"), fact("Connections", String(n.degree ?? "—")))),
        sections.length ? h("div", { class: "grid2" }, sections) : h("section", {}, emptyBox("circle-slash", "No relationships recorded for this symbol."))
      ),
    ];
  }

  function renderVerify(v) {
    const list = (items) => items.map(claimItem);
    return [
      header("verify", v.source, [h("span", {}, icon("lock"), "Local · no AI"), h("span", {}, `${v.lines} line${v.lines === 1 ? "" : "s"} checked`)], { code: false }),
      h(
        "main",
        {},
        h("section", {}, verdictBanner(v.verdict)),
        v.contradicted.length ? h("section", {}, group("Contradicted by the graph", v.contradicted.length, list(v.contradicted), true)) : null,
        v.unknown.length ? h("section", {}, group("Not in the graph (external, built-in or new)", v.unknown.length, list(v.unknown), !v.contradicted.length && !v.supported.length)) : null,
        v.supported.length ? h("section", {}, group("Confirmed", v.supported.length, list(v.supported), false)) : null,
        v.notes && v.notes.length ? h("footer", { class: "foot" }, v.notes.join(" ")) : null
      ),
    ];
  }

  const RENDER = { loading: renderLoading, error: renderError, answer: renderAnswer, search: renderSearch, explain: renderExplain, verify: renderVerify };

  function show(view) {
    const app = document.getElementById("app");
    if (!app) return;
    const render = RENDER[view.kind];
    app.replaceChildren(h("div", { class: "page" }, render ? render(view) : []));
    if (view.kind !== "loading") window.scrollTo(0, 0);
  }

  window.addEventListener("message", (event) => {
    const msg = event.data;
    if (msg && msg.type === "show") {
      vscode.setState(msg.view);
      show(msg.view);
    }
  });

  const restored = vscode.getState();
  if (restored && restored.kind !== "loading") show(restored);
  send({ type: "ready" });
})();
