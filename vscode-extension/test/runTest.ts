import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { runTests } from "@vscode/test-electron";

function copyDir(src: string, dest: string): void {
  fs.mkdirSync(dest, { recursive: true });
  for (const e of fs.readdirSync(src, { withFileTypes: true })) {
    const from = path.join(src, e.name);
    const to = path.join(dest, e.name);
    if (e.isDirectory()) copyDir(from, to);
    else fs.copyFileSync(from, to);
  }
}

async function main() {
  // __dirname is out/test. Fixtures are static files, so they live in the
  // source tree, and the workspace is copied so graphify-out/ never lands there.
  const extensionRoot = path.resolve(__dirname, "../../");
  const fixtures = path.join(extensionRoot, "test", "fixtures");
  const workspace = fs.mkdtempSync(path.join(os.tmpdir(), "synapse-it-"));
  copyDir(path.join(fixtures, "sample-workspace"), workspace);
  // Tests drive builds and updates explicitly, so turn the automatic ones off.
  fs.mkdirSync(path.join(workspace, ".vscode"));
  fs.writeFileSync(
    path.join(workspace, ".vscode", "settings.json"),
    JSON.stringify({ "synapse.autoBuildOnOpen": false, "synapse.autoUpdateOnSave": false }, null, 2)
  );

  try {
    await runTests({
      // Synapse plus a test-only extension that provides a fake language model.
      extensionDevelopmentPath: [extensionRoot, path.join(fixtures, "fake-lm-extension")],
      extensionTestsPath: path.resolve(__dirname, "./suite/index"),
      launchArgs: [workspace, "--disable-extensions", "--disable-workspace-trust"],
      extensionTestsEnv: {
        // Launched from an Electron-based tool (VS Code terminal, Claude Code),
        // ELECTRON_RUN_AS_NODE may be inherited and break the test instance.
        ELECTRON_RUN_AS_NODE: undefined,
        // Reuse the engine venv the engine e2e suite provisions (short path).
        SYNAPSE_HOME: process.env.SYNAPSE_TEST_HOME || path.join(os.homedir(), ".synapse-e2e-test"),
      },
    });
  } catch (err) {
    console.error("Integration tests failed:", err);
    process.exitCode = 1;
  } finally {
    fs.rmSync(workspace, { recursive: true, force: true });
  }
}

void main();
