import * as assert from "assert";
import { spawnSync } from "child_process";
import * as fs from "fs";
import * as path from "path";
import { adaptGraphHtml } from "../../src/ui/graphHtml";
import { CODE_EXTENSIONS, isCodeFile } from "../../src/languages";
import { parsePythonVersion, pythonCandidates } from "../../src/engine/provisioner";

const repoRoot = path.resolve(__dirname, "../../../..");

describe("graph map adaptation", () => {
  const html = fs.readFileSync(path.resolve(__dirname, "../../../test/fixtures/engine-output/graph.html"), "utf8");
  const out = adaptGraphHtml(html, { visUri: "https://file+.vscode-resource.test/media/vendor/vis-network.min.js", cspSource: "https://file+.vscode-resource.test" });

  it("replaces the unpkg.com script with the bundled copy", () => {
    assert.ok(html.includes("unpkg.com/vis-network"), "fixture should reference unpkg");
    assert.ok(!out.includes("unpkg.com"));
    assert.ok(out.includes('<script src="https://file+.vscode-resource.test/media/vendor/vis-network.min.js"></script>'));
  });

  it("adds a CSP that allows no network access", () => {
    const csp = out.match(/Content-Security-Policy" content="([^"]+)"/)![1];
    assert.match(csp, /default-src 'none'/);
    assert.ok(!/connect-src/.test(csp));
    assert.ok(!/https?:\/\/(?!file\+)/.test(csp));
  });

  it("wires double-click to open source via postMessage", () => {
    assert.ok(out.includes('network.on("doubleClick"'));
    assert.ok(out.lastIndexOf("acquireVsCodeApi") < out.lastIndexOf("</body>"));
  });
});

describe("languages", () => {
  it("matches the engine's CODE_EXTENSIONS exactly", function () {
    const py = process.platform === "win32" ? ["py", "-3"] : ["python3"];
    const r = spawnSync(py[0], [...py.slice(1), "-c", "import json,sys; sys.path.insert(0, sys.argv[1]); from graphify.detect import CODE_EXTENSIONS as C; print(json.dumps(sorted(C)))", repoRoot], { encoding: "utf8" });
    if (r.status !== 0) this.skip(); // engine deps not importable with system Python
    assert.deepStrictEqual([...CODE_EXTENSIONS].sort(), JSON.parse(r.stdout));
  });

  it("classifies files by extension", () => {
    assert.ok(isCodeFile("/x/app/main.py"));
    assert.ok(isCodeFile("C:\\x\\web\\client.tsx"));
    assert.ok(!isCodeFile("/x/README.md"));
    assert.ok(!isCodeFile("/x/Makefile"));
  });
});

describe("provisioner helpers", () => {
  it("parses Python versions", () => {
    assert.deepStrictEqual(parsePythonVersion("3.12\n"), [3, 12]);
    assert.strictEqual(parsePythonVersion("Python was not found"), null);
  });

  it("uses only the configured interpreter when one is set", () => {
    assert.deepStrictEqual(pythonCandidates("/opt/py/bin/python3"), [["/opt/py/bin/python3", []]]);
    assert.ok(pythonCandidates().length >= 2);
  });
});
