import * as vscode from "vscode";
import * as path from "path";
import { graphJsonPath, hasGraph } from "./cliService";
import { GraphNode, GraphData, loadGraph, topGodNodes, groupByCommunity } from "./graphModel";

type Section = { kind: "godNodesSection" } | { kind: "communitiesSection" };
type CommunityItem = { kind: "community"; id: number; nodes: GraphNode[] };
type NodeItem = { kind: "node"; node: GraphNode };
type TreeElement = Section | CommunityItem | NodeItem;

export class GraphTreeProvider implements vscode.TreeDataProvider<TreeElement> {
  private _onDidChangeTreeData = new vscode.EventEmitter<void>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;

  private graph: GraphData | null = null;

  constructor(private readonly workspaceRoot: string | undefined) {
    this.refresh();
  }

  refresh(): void {
    if (this.workspaceRoot && hasGraph(this.workspaceRoot)) {
      this.graph = loadGraph(graphJsonPath(this.workspaceRoot));
    } else {
      this.graph = null;
    }
    vscode.commands.executeCommand("setContext", "graphify.hasGraph", !!this.graph);
    this._onDidChangeTreeData.fire();
  }

  getTreeItem(element: TreeElement): vscode.TreeItem {
    if (element.kind === "godNodesSection") {
      const item = new vscode.TreeItem(
        "God Nodes (most connected)",
        vscode.TreeItemCollapsibleState.Expanded
      );
      item.iconPath = new vscode.ThemeIcon("star-full");
      return item;
    }
    if (element.kind === "communitiesSection") {
      const item = new vscode.TreeItem(
        "Communities",
        vscode.TreeItemCollapsibleState.Collapsed
      );
      item.iconPath = new vscode.ThemeIcon("symbol-namespace");
      return item;
    }
    if (element.kind === "community") {
      const item = new vscode.TreeItem(
        `Community ${element.id} (${element.nodes.length})`,
        vscode.TreeItemCollapsibleState.Collapsed
      );
      item.iconPath = new vscode.ThemeIcon("folder");
      return item;
    }
    // node
    const n = element.node;
    const item = new vscode.TreeItem(n.label, vscode.TreeItemCollapsibleState.None);
    item.description = n.source_file ? path.basename(n.source_file) : undefined;
    item.tooltip = n.source_file ? `${n.source_file} ${n.source_location ?? ""}` : n.label;
    item.iconPath = new vscode.ThemeIcon(
      n.label.endsWith(")") ? "symbol-method" : n.label.endsWith(".py") ? "file-code" : "symbol-class"
    );
    item.contextValue = "graphNode";
    if (n.source_file) {
      item.command = {
        command: "graphify.revealNode",
        title: "Go to Definition",
        arguments: [n],
      };
    }
    return item;
  }

  getChildren(element?: TreeElement): TreeElement[] {
    if (!this.graph) return [];

    if (!element) {
      return [{ kind: "godNodesSection" }, { kind: "communitiesSection" }];
    }
    if (element.kind === "godNodesSection") {
      return topGodNodes(this.graph, 10).map((node) => ({ kind: "node", node }));
    }
    if (element.kind === "communitiesSection") {
      const groups = groupByCommunity(this.graph);
      return [...groups.entries()]
        .sort((a, b) => b[1].length - a[1].length)
        .map(([id, nodes]) => ({ kind: "community", id, nodes }));
    }
    if (element.kind === "community") {
      return element.nodes
        .slice(0, 200) // cap rendering for very large communities
        .map((node) => ({ kind: "node", node }));
    }
    return [];
  }
}
