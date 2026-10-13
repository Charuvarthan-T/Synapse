/** Workspace-root policy for Synapse. Pure functions, no VS Code import. */

export interface WorkspaceFolderLike {
  uri: { fsPath: string };
}

function normalize(filePath: string): string {
  return filePath.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

/** Every workspace folder, in the order VS Code reports them. None are dropped. */
export function listFolderPaths(folders: readonly WorkspaceFolderLike[] | undefined): string[] {
  if (!folders) return [];
  return folders.map((folder) => folder.uri.fsPath).filter((folder) => folder.length > 0);
}

/**
 * Folder that owns `filePath`. The longest matching prefix wins so a nested
 * folder is preferred over its parent. A file outside every folder matches nothing.
 */
export function rootForFile(folders: readonly string[], filePath: string | undefined): string | undefined {
  if (!filePath) return undefined;
  const target = normalize(filePath);
  let best: string | undefined;
  let bestLength = -1;
  for (const folder of folders) {
    const root = normalize(folder);
    if (target === root || target.startsWith(root + "/")) {
      if (root.length > bestLength) {
        best = folder;
        bestLength = root.length;
      }
    }
  }
  return best;
}

/**
 * Synchronous root used for status and cache lookup.
 * Active file wins. A single folder is that folder. Several folders and no
 * active file return undefined so the caller must ask, instead of silently
 * using the first folder.
 */
export function implicitRoot(folders: readonly string[], activeFile: string | undefined): string | undefined {
  const owned = rootForFile(folders, activeFile);
  if (owned) return owned;
  if (folders.length === 1) return folders[0];
  return undefined;
}
