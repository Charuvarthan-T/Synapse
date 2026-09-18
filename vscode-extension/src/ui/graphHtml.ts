// Pure (no `vscode` import) so it can be unit-tested.

/**
 * Adapt the engine's standalone graph.html for a webview:
 *  - load vis-network from the copy bundled with the extension (works offline,
 *    no third-party request), instead of unpkg.com;
 *  - add a CSP with no network access at all;
 *  - add double-click-to-open-source, wired back to the extension.
 */
export function adaptGraphHtml(html: string, opts: { visUri: string; cspSource: string }): string {
  const csp = [
    "default-src 'none'",
    // graph.html uses inline scripts and inline event handlers (onchange=...),
    // so inline script is required; its data is sanitized by the engine.
    `script-src 'unsafe-inline' ${opts.cspSource}`,
    `style-src 'unsafe-inline' ${opts.cspSource}`,
    `img-src data: ${opts.cspSource}`,
    `font-src ${opts.cspSource}`,
  ].join("; ");

  let out = html.replace(
    /<script\s+src="https:\/\/unpkg\.com\/vis-network@[^"]+"[^>]*><\/script>/,
    `<script src="${opts.visUri}"></script>`
  );
  const head = `<meta http-equiv="Content-Security-Policy" content="${csp}">
<style>
  #synapse-hint { position: fixed; left: 50%; bottom: 14px; transform: translateX(-50%); z-index: 1000;
    padding: 5px 12px; border-radius: 999px; font: 12px -apple-system, "Segoe UI", sans-serif;
    color: #cfd3dc; background: rgba(20, 22, 30, 0.82); border: 1px solid rgba(255, 255, 255, 0.08);
    pointer-events: none; transition: opacity 0.4s ease; }
</style>`;
  out = out.includes("<head>") ? out.replace("<head>", `<head>\n${head}`) : `${head}\n${out}`;

  const bridge = `<div id="synapse-hint">Double-click a node to open its source</div>
<script>
(function () {
  const vscode = acquireVsCodeApi();
  setTimeout(function () { const h = document.getElementById("synapse-hint"); if (h) h.style.opacity = "0"; }, 5000);
  if (typeof network !== "undefined" && network && network.on) {
    network.on("doubleClick", function (params) {
      if (params && params.nodes && params.nodes.length) vscode.postMessage({ type: "openNode", id: String(params.nodes[0]) });
    });
  }
})();
</script>`;
  return out.includes("</body>") ? out.replace(/<\/body>(?![\s\S]*<\/body>)/, `${bridge}\n</body>`) : `${out}\n${bridge}`;
}

