# Multi-root VS Code workspaces

Synapse keeps every folder in `vscode.workspace.workspaceFolders`. The folder that owns the active file is selected by the longest matching path. A single-folder workspace behaves as before. When several folders are open and no file is active, commands ask which folder to use instead of silently using the first one.

Each folder has its own `graphify-out/graph.json`. The graph cache reloads when that path changes. File watchers and save-triggered updates are registered per folder.
