// Verifies the packaged extension the way users get it: installs
// synapse-<version>.vsix into an isolated VS Code (fresh extensions dir and user
// data) with VS Code's own CLI, then runs the production smoke suite against
// that installed copy with a brand-new Synapse home, so the engine is
// provisioned from the wheel inside the .vsix. Run `npm run package` first.
import * as cp from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { downloadAndUnzipVSCode, resolveCliArgsFromVSCodeExecutablePath, runTests } from "@vscode/test-electron";

async function main() {
  const extensionRoot = path.resolve(__dirname, "../../");
  const version = JSON.parse(fs.readFileSync(path.join(extensionRoot, "package.json"), "utf8")).version;
  const vsix = path.join(extensionRoot, `synapse-${version}.vsix`);
  if (!fs.existsSync(vsix)) throw new Error(`${vsix} not found; run \`npm run package\` first`);

  const sandbox = fs.mkdtempSync(path.join(os.tmpdir(), "synapse-vsix-"));
  const extensionsDir = path.join(sandbox, "extensions");
  const userDataDir = path.join(sandbox, "user-data");
  // Short path on purpose (Windows MAX_PATH during pip installs).
  const home = process.env.SYNAPSE_VSIX_TEST_HOME || path.join(os.homedir(), ".synapse-vsix-test");
  fs.rmSync(home, { recursive: true, force: true });

  try {
    const vscodeExecutablePath = await downloadAndUnzipVSCode("stable");
    const [cli, ...cliArgs] = resolveCliArgsFromVSCodeExecutablePath(vscodeExecutablePath);
    const env = { ...process.env };
    delete env.ELECTRON_RUN_AS_NODE;
    const args = [...cliArgs, "--extensions-dir", extensionsDir, "--user-data-dir", userDataDir, "--install-extension", vsix];
    // On Windows the CLI is a .cmd script, which needs a shell; quote every argument.
    const install =
      process.platform === "win32"
        ? cp.spawnSync([cli, ...args].map((a) => `"${a}"`).join(" "), { encoding: "utf8", shell: true, env })
        : cp.spawnSync(cli, args, { encoding: "utf8", env });
    process.stdout.write(install.stdout);
    if (!fs.existsSync(extensionsDir) || !fs.readdirSync(extensionsDir).some((d) => d.startsWith("synapse-labs.synapse-"))) {
      throw new Error(`installing the .vsix failed:\n${install.stderr}`);
    }

    const workspace = path.join(sandbox, "workspace");
    fs.cpSync(path.join(extensionRoot, "test", "fixtures", "sample-workspace"), workspace, { recursive: true });
    fs.mkdirSync(path.join(workspace, ".vscode"));
    fs.writeFileSync(
      path.join(workspace, ".vscode", "settings.json"),
      JSON.stringify({ "synapse.autoBuildOnOpen": false, "synapse.autoUpdateOnSave": false })
    );

    await runTests({
      vscodeExecutablePath,
      // Only the fake language model loads from source; Synapse is the installed .vsix.
      extensionDevelopmentPath: path.join(extensionRoot, "test", "fixtures", "fake-lm-extension"),
      extensionTestsPath: path.resolve(__dirname, "./smoke/index"),
      launchArgs: [workspace, "--extensions-dir", extensionsDir, "--user-data-dir", userDataDir, "--disable-workspace-trust"],
      extensionTestsEnv: { ELECTRON_RUN_AS_NODE: undefined, SYNAPSE_HOME: home },
    });
  } finally {
    fs.rmSync(sandbox, { recursive: true, force: true });
  }
}

main().catch((err) => {
  console.error("Packaged extension test failed:", err);
  process.exitCode = 1;
});
