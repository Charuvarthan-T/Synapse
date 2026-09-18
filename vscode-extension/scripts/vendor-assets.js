// Copies third-party browser assets the webviews use into media/vendor/, so
// every view works offline and loads nothing from the network:
//
//   vis-network.min.js   the exact version the engine's graph map (graph.html)
//                        pins, verified against the engine's own SRI hash
//   codicon.css/.ttf     VS Code's icon font, for native-looking icons
//
// Runs on `npm run build:assets`, before compiling and before packaging.
"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const extRoot = path.resolve(__dirname, "..");
const repoRoot = path.resolve(extRoot, "..");
const vendorOut = path.join(extRoot, "media", "vendor");
const modules = path.join(extRoot, "node_modules");

function fail(message) {
  console.error(`vendor-assets: ${message}`);
  process.exit(1);
}

function copy(from, name) {
  if (!fs.existsSync(from)) fail(`missing ${from}; run npm install`);
  fs.copyFileSync(from, path.join(vendorOut, name));
}

function vendorVisNetwork() {
  const exporter = fs.readFileSync(path.join(repoRoot, "graphify", "exporters", "html.py"), "utf8");
  const version = exporter.match(/vis-network@([\d.]+)\/standalone\/umd\/vis-network\.min\.js/);
  const sri = exporter.match(/integrity="sha384-([A-Za-z0-9+/=]+)"/);
  if (!version || !sri) fail("could not find the vis-network pin in graphify/exporters/html.py");

  const pkgDir = path.join(modules, "vis-network");
  const installed = JSON.parse(fs.readFileSync(path.join(pkgDir, "package.json"), "utf8")).version;
  if (installed !== version[1]) {
    fail(`the engine pins vis-network ${version[1]} but ${installed} is installed; update package.json`);
  }
  const src = path.join(pkgDir, "standalone", "umd", "vis-network.min.js");
  const digest = crypto.createHash("sha384").update(fs.readFileSync(src)).digest("base64");
  if (digest !== sri[1]) fail("vis-network.min.js does not match the engine's SRI hash");
  copy(src, "vis-network.min.js");
}

fs.mkdirSync(vendorOut, { recursive: true });
vendorVisNetwork();
copy(path.join(modules, "@vscode", "codicons", "dist", "codicon.css"), "codicon.css");
copy(path.join(modules, "@vscode", "codicons", "dist", "codicon.ttf"), "codicon.ttf");
console.log("vendor-assets: vis-network and codicons copied to media/vendor");
