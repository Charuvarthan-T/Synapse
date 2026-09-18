import * as crypto from "crypto";
import * as vscode from "vscode";

export function nonce(): string {
  return crypto.randomBytes(16).toString("base64");
}

/** Local assets a Synapse webview is allowed to load. */
export function webviewOptions(extensionUri: vscode.Uri): vscode.WebviewOptions {
  return {
    enableScripts: true,
    localResourceRoots: [vscode.Uri.joinPath(extensionUri, "media")],
  };
}

/**
 * HTML shell for Synapse's own webviews: a strict CSP (nonce-only scripts,
 * local styles/fonts/images, no network), VS Code's codicons, the shared base
 * stylesheet plus page-specific CSS/JS from media/.
 */
export function webviewShell(
  webview: vscode.Webview,
  extensionUri: vscode.Uri,
  page: { title: string; css: string; js: string; body?: string }
): string {
  const n = nonce();
  const asset = (...parts: string[]) => webview.asWebviewUri(vscode.Uri.joinPath(extensionUri, "media", ...parts));
  const csp = [
    "default-src 'none'",
    `img-src ${webview.cspSource} data:`,
    `font-src ${webview.cspSource}`,
    `style-src ${webview.cspSource}`,
    `script-src 'nonce-${n}'`,
  ].join("; ");
  return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="Content-Security-Policy" content="${csp}">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>${page.title}</title>
<link rel="stylesheet" href="${asset("vendor", "codicon.css")}">
<link rel="stylesheet" href="${asset("base.css")}">
<link rel="stylesheet" href="${asset(page.css)}">
</head>
<body>
${page.body ?? '<div id="app"></div>'}
<script nonce="${n}" src="${asset(page.js)}"></script>
</body>
</html>`;
}
