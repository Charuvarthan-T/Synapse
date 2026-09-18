// Packages this repository's own engine (the Synapse fork of graphify, in
// ../graphify) into the extension, so the .vsix always ships our code and
// never the upstream "graphifyy" package from PyPI.
//
//   engine/<name>.whl         pure-Python wheel built from ../graphify
//   engine/manifest.json      wheel name + sha256 + source commit
//
// Runs on `npm run build:engine` and automatically before packaging.
"use strict";

const { spawnSync } = require("child_process");
const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");

const extRoot = path.resolve(__dirname, "..");
const repoRoot = path.resolve(extRoot, "..");
const engineOut = path.join(extRoot, "engine");

// Modules that only exist in the Synapse fork. A wheel missing any of them is
// the upstream engine (or a broken build) and must never be shipped.
const SYNAPSE_MODULES = [
  "graphify/bidirectional_reasoner.py",
  "graphify/preexec_validate.py",
  "graphify/community_retrieval.py",
  "graphify/weighted_retrieval.py",
  "graphify/semantic_graph.py",
  "graphify/claims.py",
  "graphify/graph_checks.py",
];
const ROOT_FILES = ["pyproject.toml", "README.md", "LICENSE", "LICENSE-MIT", "NOTICE"];
const SKIP_DIRS = new Set(["__pycache__", "graphify-out", ".pytest_cache"]);

function fail(message) {
  console.error(`build-engine: ${message}`);
  process.exit(1);
}

function run(cmd, args, opts = {}) {
  const res = spawnSync(cmd, args, { encoding: "utf8", ...opts });
  return { ok: res.status === 0, out: `${res.stdout || ""}${res.stderr || ""}`, error: res.error };
}

function findPython() {
  const candidates = process.env.PYTHON
    ? [[process.env.PYTHON, []]]
    : process.platform === "win32"
      ? [["py", ["-3"]], ["python", []], ["python3", []]]
      : [["python3", []], ["python", []]];
  for (const [cmd, pre] of candidates) {
    const r = run(cmd, [...pre, "-c", "import sys; assert sys.version_info >= (3, 10)"]);
    if (r.ok) return [cmd, pre];
  }
  fail("Python 3.10+ is required to build the engine wheel (set PYTHON to override).");
}

function copyTree(src, dest) {
  fs.mkdirSync(dest, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    if (entry.isDirectory() && SKIP_DIRS.has(entry.name)) continue;
    const from = path.join(src, entry.name);
    const to = path.join(dest, entry.name);
    if (entry.isDirectory()) copyTree(from, to);
    else if (entry.isFile() && !entry.name.endsWith(".pyc")) fs.copyFileSync(from, to);
  }
}

function sha(algorithm, file, encoding) {
  return crypto.createHash(algorithm).update(fs.readFileSync(file)).digest(encoding);
}

function gitRevision() {
  const head = run("git", ["rev-parse", "--short", "HEAD"], { cwd: repoRoot });
  if (!head.ok) return "unknown";
  const dirty = run("git", ["status", "--porcelain", "--", "graphify", "pyproject.toml"], {
    cwd: repoRoot,
  });
  return head.out.trim() + (dirty.ok && dirty.out.trim() ? "-dirty" : "");
}

function buildWheel() {
  // Build from a clean staging copy: setuptools reuses a stale build/lib in the
  // source tree, which can leak deleted modules into the wheel.
  const staging = fs.mkdtempSync(path.join(os.tmpdir(), "synapse-engine-"));
  try {
    for (const file of ROOT_FILES) {
      const src = path.join(repoRoot, file);
      if (!fs.existsSync(src)) fail(`missing ${file} at repository root`);
      fs.copyFileSync(src, path.join(staging, file));
    }
    copyTree(path.join(repoRoot, "graphify"), path.join(staging, "graphify"));

    const dist = path.join(staging, "dist");
    const uv = run("uv", ["build", "--wheel", "--out-dir", dist, staging]);
    if (!uv.ok) {
      const [py, pre] = findPython();
      const pip = run(py, [...pre, "-m", "pip", "wheel", "--no-deps", "-w", dist, staging]);
      if (!pip.ok) fail(`wheel build failed:\n${uv.out}\n${pip.out}`);
    }
    const wheels = fs.readdirSync(dist).filter((f) => f.endsWith(".whl"));
    if (wheels.length !== 1) fail(`expected one wheel, found: ${wheels.join(", ") || "none"}`);

    fs.rmSync(engineOut, { recursive: true, force: true });
    fs.mkdirSync(engineOut, { recursive: true });
    const wheelPath = path.join(engineOut, wheels[0]);
    fs.copyFileSync(path.join(dist, wheels[0]), wheelPath);
    return wheelPath;
  } finally {
    fs.rmSync(staging, { recursive: true, force: true });
  }
}

function verifyWheel(wheelPath) {
  const [py, pre] = findPython();
  const probe = [
    "import sys, zipfile",
    "names = set(zipfile.ZipFile(sys.argv[1]).namelist())",
    "missing = [m for m in sys.argv[2:] if m not in names]",
    "llm = zipfile.ZipFile(sys.argv[1]).read('graphify/llm.py').decode('utf-8')",
    "missing += [] if '\"editor-bridge\"' in llm else ['editor-bridge backend in graphify/llm.py']",
    "print('\\n'.join(missing)); sys.exit(1 if missing else 0)",
  ].join("\n");
  const r = run(py, [...pre, "-c", probe, wheelPath, ...SYNAPSE_MODULES]);
  if (!r.ok) fail(`wheel is not the Synapse engine; missing:\n${r.out}`);
}

function main() {
  const wheelPath = buildWheel();
  verifyWheel(wheelPath);
  const manifest = {
    wheel: path.basename(wheelPath),
    sha256: sha("sha256", wheelPath, "hex"),
    source: gitRevision(),
  };
  fs.writeFileSync(path.join(engineOut, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n");
  console.log(`build-engine: ${manifest.wheel} (${manifest.sha256.slice(0, 12)}, source ${manifest.source})`);
}

main();
