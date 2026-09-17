import * as path from "path";
import { runTests } from "@vscode/test-electron";

async function main() {
  try {
    // __dirname here is out/test (compiled output) — extensionDevelopmentPath
    // needs the extension root, extensionTestsPath needs the compiled suite,
    // and workspacePath needs the *source* fixtures dir (fixtures are static
    // assets, not .ts files, so tsc never copies them into out/).
    const extensionDevelopmentPath = path.resolve(__dirname, "../../");
    const extensionTestsPath = path.resolve(__dirname, "./suite/index");
    const workspacePath = path.resolve(__dirname, "../../test/fixtures/sample-python-repo");

    await runTests({
      extensionDevelopmentPath,
      extensionTestsPath,
      launchArgs: [workspacePath, "--disable-extensions", "--disable-workspace-trust"],
      // If this is invoked from inside a VS Code integrated terminal (e.g. by
      // Claude Code itself, or any Electron-based dev tool), ELECTRON_RUN_AS_NODE
      // is often already set in the environment. Inherited by the spawned test
      // instance, it makes Electron run as plain Node instead of launching the
      // app, which then fails trying to `require()` the workspace path as a
      // script. Explicitly unset it for this child process only.
      extensionTestsEnv: { ELECTRON_RUN_AS_NODE: undefined },
    });
  } catch (err) {
    console.error("Failed to run integration tests:", err);
    process.exit(1);
  }
}

void main();
