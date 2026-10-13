import * as assert from "assert";
import { implicitRoot, listFolderPaths, rootForFile } from "../../src/workspaceRoots";

describe("workspace roots", () => {
  const folders = ["C:/work/alpha", "C:/work/beta"];

  it("keeps every workspace folder", () => {
    assert.deepStrictEqual(
      listFolderPaths([{ uri: { fsPath: "C:/work/alpha" } }, { uri: { fsPath: "C:/work/beta" } }]),
      folders
    );
  });

  it("selects the folder that contains the active file", () => {
    assert.strictEqual(rootForFile(folders, "C:/work/beta/src/app.ts"), "C:/work/beta");
    assert.strictEqual(implicitRoot(folders, "C:/work/beta/src/app.ts"), "C:/work/beta");
  });

  it("prefers the longer matching root", () => {
    assert.strictEqual(
      rootForFile(["C:/work", "C:/work/beta"], "C:/work/beta/main.py"),
      "C:/work/beta"
    );
  });

  it("does not silently fall back to the first folder when several are open", () => {
    assert.strictEqual(implicitRoot(folders, undefined), undefined);
  });

  it("keeps single-folder behaviour", () => {
    assert.strictEqual(implicitRoot(["C:/only"], undefined), "C:/only");
  });

  it("ignores a file outside every root", () => {
    assert.strictEqual(rootForFile(folders, "D:/other/file.py"), undefined);
  });
});
