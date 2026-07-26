from __future__ import annotations
import hashlib
import html as _html
import json
import math
import os
import re
import shutil
import sys
from collections import Counter
from datetime import date
from pathlib import Path
import networkx as nx
from networkx.readwrite import json_graph
from graphify.security import sanitize_label
from graphify.analyze import _node_community_map
from graphify.build import edge_data

from graphify.exporters.graphdb import push_to_falkordb, push_to_neo4j  # noqa: E402,F401


_BACKUP_ARTIFACTS = [
    "graph.json",
    "GRAPH_REPORT.md",
    ".graphify_labels.json",
    ".graphify_analysis.json",
    "manifest.json",
    ".graphify_semantic_marker",
    "cost.json",
]


def backup_if_protected(out_dir: Path) -> "Path | None":
    """Snapshot graph artifacts to a dated subfolder before an overwrite.

    Triggers when graph.json exists AND either:
    - .graphify_semantic_marker is present (graph cost real LLM tokens), or
    - .graphify_labels.json contains at least one non-default community label
      (graph has been curated by a human or skill).

    Returns the backup folder path, or None if no backup was taken.
    Never raises — backup failure prints a warning but never blocks the write.
    Set GRAPHIFY_NO_BACKUP=1 to disable.
    """
    if os.environ.get("GRAPHIFY_NO_BACKUP"):
        return None
    out = Path(out_dir)
    if not (out / "graph.json").exists():
        return None

    is_semantic = (out / ".graphify_semantic_marker").exists()
    is_curated = False
    labels_file = out / ".graphify_labels.json"
    if labels_file.exists():
        try:
            labels = json.loads(labels_file.read_text(encoding="utf-8"))
            is_curated = any(v != f"Community {k}" for k, v in labels.items())
        except Exception:
            pass

    if not is_semantic and not is_curated:
        return None

    reason = "+".join(
        filter(None, ["semantic" if is_semantic else "", "curated" if is_curated else ""])
    )
    today = date.today().isoformat()
    backup_dir = out / today
    graph_src = out / "graph.json"

    if backup_dir.exists() and (backup_dir / "graph.json").exists():
        src_hash = hashlib.sha256(graph_src.read_bytes()).hexdigest()
        bak_hash = hashlib.sha256((backup_dir / "graph.json").read_bytes()).hexdigest()
        if src_hash == bak_hash:
            return backup_dir

    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        copied = 0
        for name in _BACKUP_ARTIFACTS:
            src = out / name
            if src.exists():
                try:
                    shutil.copy2(src, backup_dir / name)
                    copied += 1
                except Exception:
                    pass
        if copied:
            print(f"[graphify] backed up {reason} graph ({copied} files) -> {backup_dir.name}/")
        return backup_dir
    except Exception as exc:
        import sys

        print(
            f"[graphify] warning: backup failed ({exc}) - continuing with overwrite",
            file=sys.stderr,
        )
        return None


def _obsidian_tag(name: str) -> str:
    """Sanitize a community name for use as an Obsidian tag.

    Obsidian tags only allow alphanumerics, hyphens, underscores, and slashes.
    Spaces become underscores; everything else is stripped.
    """
    return re.sub(r"[^a-zA-Z0-9_\-/]", "", name.replace(" ", "_"))


def _strip_diacritics(text: str | None) -> str:
    import unicodedata

    if not isinstance(text, str):
        text = "" if text is None else str(text)
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _yaml_str(s: str) -> str:
    """Escape a value for safe embedding in a YAML double-quoted scalar (F-009).

    See `graphify.ingest._yaml_str` for the full rationale; duplicated here to
    avoid pulling the URL-fetching `ingest` module into export's dependency
    graph. Handles backslash, double-quote, all line breaks (\\n, \\r,
    U+2028, U+2029), tab, NUL, and other C0/DEL control characters that
    would otherwise let a hostile `source_file` / `community` / etc. break
    out of the YAML scalar and inject sibling keys.
    """
    if s is None:
        return ""
    out: list[str] = []
    for ch in str(s):
        cp = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\0":
            out.append("\\0")
        elif cp == 0x2028:
            out.append("\\L")
        elif cp == 0x2029:
            out.append("\\P")
        elif cp < 0x20 or cp == 0x7F:
            out.append(f"\\x{cp:02x}")
        else:
            out.append(ch)
    return "".join(out)


from graphify.exporters.base import COMMUNITY_COLORS  # noqa: E402,F401

from graphify.exporters.html import to_html  # noqa: E402,F401


_CONFIDENCE_SCORE_DEFAULTS = {"EXTRACTED": 1.0, "INFERRED": 0.5, "AMBIGUOUS": 0.2}


def attach_hyperedges(G: nx.Graph, hyperedges: list) -> None:
    """Store hyperedges in the graph's metadata dict."""
    existing = G.graph.get("hyperedges", [])
    seen_ids = {h["id"] for h in existing}
    for h in hyperedges:
        if h.get("id") and h["id"] not in seen_ids:
            existing.append(h)
            seen_ids.add(h["id"])
    G.graph["hyperedges"] = existing


def _git_head() -> str | None:
    """Return the current git HEAD commit hash, or None if not in a git repo."""
    import subprocess as _sp

    try:
        r = _sp.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=3)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


MALFORMED_GRAPH = object()


def existing_graph_node_count(path: "str | Path"):
    """Node count of an existing graph.json.

    Returns:
      - an ``int`` node count when the file parses;
      - ``None`` when there is verifiably nothing to protect — absent, empty, or
        over the size cap (matching how :func:`to_json` lets the new graph
        replace an empty/oversized file);
      - :data:`MALFORMED_GRAPH` when the file is present and non-empty but
        unparseable — the caller must treat this as fail-closed (refuse to
        overwrite), mirroring to_json's #479 handling of a corrupt/mid-write file.

    The raw ``--no-cluster`` write path uses this to apply the same #479 shrink
    guard that :func:`to_json` applies inline for the clustered path.
    """
    p = Path(path)
    if not p.exists():
        return None
    from graphify.security import check_graph_file_size_cap

    try:
        check_graph_file_size_cap(p)
    except Exception:
        return None
    try:
        raw = p.read_text(encoding="utf-8")
    except Exception:
        try:
            return MALFORMED_GRAPH if p.stat().st_size > 0 else None
        except Exception:
            return None
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return MALFORMED_GRAPH
    nodes = data.get("nodes") if isinstance(data, dict) else None
    return len(nodes) if isinstance(nodes, list) else MALFORMED_GRAPH


def to_json(
    G: nx.Graph,
    communities: dict[int, list[str]],
    output_path: str,
    *,
    force: bool = False,
    built_at_commit: str | None = None,
    community_labels: dict[int, str] | None = None,
) -> bool:
    existing_path = Path(output_path)
    if not force and existing_path.exists():
        from graphify.security import check_graph_file_size_cap

        try:
            check_graph_file_size_cap(existing_path)
        except Exception:
            oversized = True
        else:
            oversized = False
        if not oversized:
            try:
                raw = existing_path.read_text(encoding="utf-8")
            except Exception:
                raw = ""
            if not raw.strip():
                existing_n = 0
            else:
                try:
                    existing_data = json.loads(raw)
                    existing_n = len(existing_data.get("nodes", []))
                except Exception as exc:
                    import sys as _sys

                    print(
                        f"[graphify] WARNING: existing {existing_path} could not be "
                        f"read to verify the new graph is not smaller ({exc}). "
                        f"Refusing to overwrite; pass force=True to override.",
                        file=_sys.stderr,
                    )
                    return False
            new_n = G.number_of_nodes()
            if new_n < existing_n:
                import sys as _sys

                print(
                    f"[graphify] WARNING: new graph has {new_n} nodes but existing "
                    f"graph.json has {existing_n} (net -{existing_n - new_n}). "
                    f"Refusing to overwrite. Possible causes: missing chunk files from "
                    f"a previous session, or fuzzy dedup collapsed same-named symbols "
                    f"across files during an --update on an already-current graph. "
                    f"Run a full rebuild (/graphify .) to be safe, or pass force=True "
                    f"only if you have verified the reduction is legitimate.",
                    file=_sys.stderr,
                )
                return False

    node_community = _node_community_map(communities)
    _labels: dict[int, str] = {int(k): v for k, v in (community_labels or {}).items()}
    try:
        data = json_graph.node_link_data(G, edges="links")
    except TypeError:
        data = json_graph.node_link_data(G)
    for node in data["nodes"]:
        cid = node_community.get(node["id"])
        node["community"] = cid
        if cid is not None and _labels:
            node["community_name"] = _labels.get(cid, f"Community {cid}")
        node["norm_label"] = _strip_diacritics(node.get("label", "")).lower()
    for link in data["links"]:
        if "confidence_score" not in link:
            conf = link.get("confidence", "EXTRACTED")
            link["confidence_score"] = _CONFIDENCE_SCORE_DEFAULTS.get(conf, 1.0)
        true_src = link.pop("_src", None)
        true_tgt = link.pop("_tgt", None)
        if true_src is not None and true_tgt is not None:
            link["source"] = true_src
            link["target"] = true_tgt
    data["hyperedges"] = getattr(G, "graph", {}).get("hyperedges", [])
    commit = built_at_commit if built_at_commit is not None else _git_head()
    if commit:
        data["built_at_commit"] = commit
    from graphify.paths import write_json_atomic

    write_json_atomic(output_path, data, indent=2)
    return True


def prune_dangling_edges(graph_data: dict) -> tuple[dict, int]:
    """Remove edges whose source or target node is not in the node set.

    Returns the cleaned graph_data dict and the number of pruned edges.
    """
    node_ids = {n["id"] for n in graph_data["nodes"]}
    links_key = "links" if "links" in graph_data else "edges"
    before = len(graph_data[links_key])
    graph_data[links_key] = [
        e for e in graph_data[links_key] if e["source"] in node_ids and e["target"] in node_ids
    ]
    return graph_data, before - len(graph_data[links_key])


def _cypher_escape(s: str) -> str:
    """Escape a string for safe embedding in a Cypher single-quoted literal.

    Handles all characters that could prematurely terminate the literal or
    inject control sequences:
      - `\\` and `'` (literal terminators)
      - newlines/CRs (would break the per-line statement framing)
      - NUL/control bytes (defensive — Neo4j errors on raw NULs)

    Also strips any leading/trailing whitespace that would let an attacker
    break the `;`-terminated statement boundary used by `cypher-shell`.
    Closing `}` and `)` are NOT special inside a single-quoted Cypher string,
    so escaping the quote and backslash correctly is sufficient (a `}` inside
    a properly-closed `'...'` literal is just a character) — but we previously
    missed `\\n` / `\\r` which DO let a payload break out of the statement
    line and inject a fresh MATCH/DELETE on the following line. See F-008.
    """
    s = "".join(ch for ch in s if ch >= " " or ch == "\t")
    return s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n").replace("\r", "\\r")


_CYPHER_IDENT_RE = re.compile(r"[^A-Za-z0-9_]")


def _cypher_label(raw: str, fallback: str) -> str:
    """Sanitise a value used in identifier position (node label / rel type).

    Cypher does not provide a way to escape `:Foo` label syntax, so we must
    strip everything except `[A-Za-z0-9_]` and require the result to start
    with a letter; otherwise we fall back to a safe constant.
    """
    cleaned = _CYPHER_IDENT_RE.sub("", raw or "")
    if not cleaned or not cleaned[0].isalpha():
        return fallback
    return cleaned


def to_cypher(G: nx.Graph, output_path: str) -> None:
    lines = ["// Neo4j Cypher import - generated by /graphify", ""]
    for node_id, data in G.nodes(data=True):
        label = _cypher_escape(data.get("label", node_id))
        node_id_esc = _cypher_escape(node_id)
        ftype = _cypher_label(
            (data.get("file_type", "unknown") or "unknown").capitalize(),
            "Entity",
        )
        lines.append(f"MERGE (n:{ftype} {{id: '{node_id_esc}', label: '{label}'}});")
    lines.append("")
    for u, v, data in G.edges(data=True):
        rel = _cypher_label(
            (data.get("relation", "RELATES_TO") or "RELATES_TO").upper(),
            "RELATES_TO",
        )
        conf = _cypher_escape(data.get("confidence", "EXTRACTED"))
        u_esc = _cypher_escape(u)
        v_esc = _cypher_escape(v)
        lines.append(
            f"MATCH (a {{id: '{u_esc}'}}), (b {{id: '{v_esc}'}}) "
            f"MERGE (a)-[:{rel} {{confidence: '{conf}'}}]->(b);"
        )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


generate_html = to_html


def _cap_filename(s: str, limit: int = 200) -> str:
    """Cap a filename stem to ``limit`` UTF-8 bytes so it stays under the 255-byte
    filesystem limit even after the ``.md`` extension and dedup suffix are added
    (#1094). The cap is on BYTES, not chars, because a label of multibyte
    characters (CJK, accented) can exceed 255 bytes well under 255 chars. When
    truncation happens, an 8-char hash of the full label is appended so two
    distinct labels sharing a long prefix produce distinct, deterministic
    filenames instead of colliding."""
    b = s.encode("utf-8")
    if len(b) <= limit:
        return s
    digest = hashlib.sha1(s.encode("utf-8")).hexdigest()[:8]
    keep = limit - 9
    truncated = b[:keep].decode("utf-8", "ignore")
    return f"{truncated}_{digest}"


def _dedup_node_filenames(G: nx.Graph, safe_name) -> dict[str, str]:
    """Map each node_id to a unique note filename, appending a numeric suffix on
    collision. The collision set is keyed on the lowercased name so two labels
    differing only by case (e.g. "References" vs "references") still get distinct
    filenames - on case-insensitive filesystems (macOS/APFS, Windows/NTFS) they
    would otherwise resolve to one path and silently overwrite each other on disk.
    The suffixed candidate is itself re-checked, so a generated "base_1" never
    silently overwrites a node whose literal label is already "base_1"."""
    node_filenames: dict[str, str] = {}
    used: set[str] = set()
    for node_id, data in G.nodes(data=True):
        base = safe_name(data.get("label", node_id))
        candidate = base
        n = 1
        while candidate.lower() in used:
            candidate = f"{base}_{n}"
            n += 1
        used.add(candidate.lower())
        node_filenames[node_id] = candidate
    return node_filenames


def to_obsidian(
    G: nx.Graph,
    communities: dict[int, list[str]],
    output_dir: str,
    community_labels: dict[int, str] | None = None,
    cohesion: dict[int, float] | None = None,
) -> int:
    """Export graph as an Obsidian vault - one .md file per node with [[wikilinks]],
    plus one _COMMUNITY_name.md overview note per community (sorted to top by underscore prefix).

    Open the output directory as a vault in Obsidian to get an interactive
    graph view with community colors and full-text search over node metadata.

    Returns the number of node notes + community notes written.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    _manifest_path = out / ".graphify_obsidian_manifest.json"
    try:
        _owned: set[str] = set(
            json.loads(_manifest_path.read_text(encoding="utf-8")).get("files", [])
        )
    except (OSError, ValueError):
        _owned = set()
    _written: list[str] = []
    _skipped: list[str] = []

    def _owned_write(rel_name: str, content: str) -> bool:
        """Write a graphify-owned file, refusing to overwrite a pre-existing file
        graphify didn't create. Returns True if written."""
        target = out / rel_name
        if target.exists() and rel_name not in _owned:
            _skipped.append(rel_name)
            return False
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        _written.append(rel_name)
        return True

    node_community = _node_community_map(communities)

    def safe_name(label: str) -> str:
        cleaned = re.sub(
            r'[\\/*?:"<>|#^[\]]',
            "",
            label.replace("\r\n", " ").replace("\r", " ").replace("\n", " "),
        ).strip()
        cleaned = re.sub(r"\.(md|mdx|qmd|markdown)$", "", cleaned, flags=re.IGNORECASE)
        if not re.search(r"\w", cleaned, flags=re.UNICODE):
            return "unnamed"
        return _cap_filename(cleaned)

    node_filename = _dedup_node_filenames(G, safe_name)

    def _dominant_confidence(node_id: str) -> str:
        confs = []
        for u, v, edata in G.edges(node_id, data=True):
            confs.append(edata.get("confidence", "EXTRACTED"))
        if not confs:
            return "EXTRACTED"
        return Counter(confs).most_common(1)[0][0]

    _FTYPE_TAG = {
        "code": "graphify/code",
        "document": "graphify/document",
        "paper": "graphify/paper",
        "image": "graphify/image",
    }

    node_notes_written = 0
    for node_id, data in G.nodes(data=True):
        label = data.get("label", node_id)
        cid = node_community.get(node_id)
        community_name = (
            community_labels.get(cid, f"Community {cid}")
            if community_labels and cid is not None
            else f"Community {cid}"
        )

        ftype = data.get("file_type", "")
        ftype_tag = _FTYPE_TAG.get(ftype, f"graphify/{ftype}" if ftype else "graphify/document")
        dom_conf = _dominant_confidence(node_id)
        conf_tag = f"graphify/{dom_conf}"
        comm_tag = f"community/{_obsidian_tag(community_name)}"
        node_tags = [ftype_tag, conf_tag, comm_tag]

        lines: list[str] = []

        lines += [
            "---",
            f'source_file: "{_yaml_str(data.get("source_file", ""))}"',
            f'type: "{_yaml_str(ftype)}"',
            f'community: "{_yaml_str(community_name)}"',
        ]
        if data.get("source_location"):
            lines.append(f'location: "{_yaml_str(str(data["source_location"]))}"')
        lines.append("tags:")
        for tag in node_tags:
            lines.append(f"  - {tag}")
        lines += ["---", "", f"# {label}", ""]

        neighbors = list(G.neighbors(node_id))
        if neighbors:
            lines.append("## Connections")
            for neighbor in sorted(neighbors, key=lambda n: G.nodes[n].get("label", n)):
                edata = edge_data(G, node_id, neighbor)
                neighbor_label = node_filename[neighbor]
                relation = edata.get("relation", "")
                confidence = edata.get("confidence", "EXTRACTED")
                lines.append(f"- [[{neighbor_label}]] - `{relation}` [{confidence}]")
            lines.append("")

        inline_tags = " ".join(f"#{t}" for t in node_tags)
        lines.append(inline_tags)

        fname = node_filename[node_id] + ".md"
        if _owned_write(fname, "\n".join(lines)):
            node_notes_written += 1

    inter_community_edges: dict[int, dict[int, int]] = {}
    for cid in communities:
        inter_community_edges[cid] = {}
    for u, v in G.edges():
        cu = node_community.get(u)
        cv = node_community.get(v)
        if cu is not None and cv is not None and cu != cv:
            inter_community_edges.setdefault(cu, {})
            inter_community_edges.setdefault(cv, {})
            inter_community_edges[cu][cv] = inter_community_edges[cu].get(cv, 0) + 1
            inter_community_edges[cv][cu] = inter_community_edges[cv].get(cu, 0) + 1

    def _community_reach(node_id: str) -> int:
        neighbor_cids = {
            node_community[nb]
            for nb in G.neighbors(node_id)
            if nb in node_community and node_community[nb] != node_community.get(node_id)
        }
        return len(neighbor_cids)

    def _community_name(cid) -> str:
        return (
            community_labels.get(cid, f"Community {cid}")
            if community_labels and cid is not None
            else f"Community {cid}"
        )

    community_filename: dict = {}
    used_community: set[str] = set()
    for cid in communities:
        base = f"_COMMUNITY_{safe_name(_community_name(cid))}"
        candidate = base
        n = 1
        while candidate.lower() in used_community:
            candidate = f"{base}_{n}"
            n += 1
        used_community.add(candidate.lower())
        community_filename[cid] = candidate

    community_notes_written = 0
    for cid, all_members in communities.items():
        community_name = _community_name(cid)
        members = [m for m in all_members if m in G and m in node_filename]
        n_members = len(members)
        coh_value = cohesion.get(cid) if cohesion else None

        lines: list[str] = []

        lines.append("---")
        lines.append("type: community")
        if coh_value is not None:
            lines.append(f"cohesion: {coh_value:.2f}")
        lines.append(f"members: {n_members}")
        lines.append("---")
        lines.append("")
        lines.append(f"# {community_name}")
        lines.append("")

        if coh_value is not None:
            cohesion_desc = (
                "tightly connected"
                if coh_value >= 0.7
                else "moderately connected"
                if coh_value >= 0.4
                else "loosely connected"
            )
            lines.append(f"**Cohesion:** {coh_value:.2f} - {cohesion_desc}")
        lines.append(f"**Members:** {n_members} nodes")
        lines.append("")

        lines.append("## Members")
        for node_id in sorted(members, key=lambda n: G.nodes[n].get("label", n)):
            data = G.nodes[node_id]
            node_label = node_filename[node_id]
            ftype = data.get("file_type", "")
            source = data.get("source_file", "")
            entry = f"- [[{node_label}]]"
            if ftype:
                entry += f" - {ftype}"
            if source:
                entry += f" - {source}"
            lines.append(entry)
        lines.append("")

        comm_tag_name = _obsidian_tag(community_name)
        lines.append("## Live Query (requires Dataview plugin)")
        lines.append("")
        lines.append("```dataview")
        lines.append(f"TABLE source_file, type FROM #community/{comm_tag_name}")
        lines.append("SORT file.name ASC")
        lines.append("```")
        lines.append("")

        cross = inter_community_edges.get(cid, {})
        if cross:
            lines.append("## Connections to other communities")
            for other_cid, edge_count in sorted(cross.items(), key=lambda x: -x[1]):
                other_fname = (
                    community_filename.get(other_cid)
                    or f"_COMMUNITY_{safe_name(_community_name(other_cid))}"
                )
                lines.append(
                    f"- {edge_count} edge{'s' if edge_count != 1 else ''} to [[{other_fname}]]"
                )
            lines.append("")

        bridge_nodes = [
            (node_id, G.degree(node_id), _community_reach(node_id))
            for node_id in members
            if _community_reach(node_id) > 0
        ]
        bridge_nodes.sort(key=lambda x: (-x[2], -x[1]))
        top_bridges = bridge_nodes[:5]
        if top_bridges:
            lines.append("## Top bridge nodes")
            for node_id, degree, reach in top_bridges:
                node_label = node_filename[node_id]
                lines.append(
                    f"- [[{node_label}]] - degree {degree}, connects to {reach} "
                    f"{'community' if reach == 1 else 'communities'}"
                )

        fname = community_filename[cid] + ".md"
        if _owned_write(fname, "\n".join(lines)):
            community_notes_written += 1

    graph_config = {
        "colorGroups": [
            {
                "query": f"tag:#community/{label.replace(' ', '_')}",
                "color": {
                    "a": 1,
                    "rgb": int(COMMUNITY_COLORS[cid % len(COMMUNITY_COLORS)].lstrip("#"), 16),
                },
            }
            for cid, label in sorted((community_labels or {}).items())
        ]
    }
    _owned_write(".obsidian/graph.json", json.dumps(graph_config, indent=2))

    stale = _owned - set(_written) - set(_skipped)
    pruned = 0
    for rel_name in sorted(stale):
        target = (out / rel_name).resolve()
        if out.resolve() not in target.parents:
            continue
        try:
            target.unlink(missing_ok=True)
            pruned += 1
        except OSError:
            pass
    if pruned:
        print(
            f"[graphify] pruned {pruned} note(s) for nodes no longer in the graph",
            file=sys.stderr,
        )

    try:
        _manifest_path.write_text(
            json.dumps({"files": sorted(set(_written))}, indent=2), encoding="utf-8"
        )
    except OSError:
        pass
    if _skipped:
        shown = ", ".join(_skipped[:5]) + (
            f" (+{len(_skipped) - 5} more)" if len(_skipped) > 5 else ""
        )
        print(
            f"[graphify] WARNING: skipped {len(_skipped)} pre-existing file(s) graphify "
            f"did not create, to avoid overwriting your notes: {shown}. "
            f"Export into an empty directory (or the default graphify-out/obsidian) "
            f"to get the full vault.",
            file=sys.stderr,
        )

    return node_notes_written + community_notes_written


def to_canvas(
    G: nx.Graph,
    communities: dict[int, list[str]],
    output_path: str,
    community_labels: dict[int, str] | None = None,
    node_filenames: dict[str, str] | None = None,
) -> None:
    """Export graph as an Obsidian Canvas file - communities as groups, nodes as cards.

    Generates a structured layout: communities arranged in a grid, nodes within
    each community arranged in rows. Edges shown between connected nodes.
    Opens in Obsidian as an infinite canvas with community groupings visible.
    """
    CANVAS_COLORS = ["1", "2", "3", "4", "5", "6"]

    def safe_name(label: str) -> str:
        cleaned = re.sub(
            r'[\\/*?:"<>|#^[\]]',
            "",
            label.replace("\r\n", " ").replace("\r", " ").replace("\n", " "),
        ).strip()
        cleaned = re.sub(r"\.(md|mdx|qmd|markdown)$", "", cleaned, flags=re.IGNORECASE)
        if not re.search(r"\w", cleaned, flags=re.UNICODE):
            return "unnamed"
        return _cap_filename(cleaned)

    if node_filenames is None:
        node_filenames = _dedup_node_filenames(G, safe_name)

    if not communities and G.number_of_nodes() > 0:
        communities = {0: [str(n) for n in G.nodes()]}

    num_communities = len(communities)
    cols = math.ceil(math.sqrt(num_communities)) if num_communities > 0 else 1
    rows = math.ceil(num_communities / cols) if num_communities > 0 else 1

    canvas_nodes: list[dict] = []
    canvas_edges: list[dict] = []

    gap = 80
    group_x_offsets: list[int] = []
    group_y_offsets: list[int] = []

    sorted_cids = sorted(communities.keys())
    group_sizes: dict[int, tuple[int, int]] = {}
    group_cols: dict[int, int] = {}
    for cid in sorted_cids:
        members = [m for m in communities[cid] if m in G and m in node_filenames]
        n = len(members)
        inner_cols = max(1, math.ceil(math.sqrt(n)))
        w = max(600, 220 * inner_cols)
        h = max(400, 100 * math.ceil(n / inner_cols) + 120)
        group_sizes[cid] = (w, h)
        group_cols[cid] = inner_cols

    col_widths: list[int] = []
    row_heights: list[int] = []
    for col_idx in range(cols):
        max_w = 0
        for row_idx in range(rows):
            linear = row_idx * cols + col_idx
            if linear < len(sorted_cids):
                cid = sorted_cids[linear]
                w, _ = group_sizes[cid]
                max_w = max(max_w, w)
        col_widths.append(max_w)

    for row_idx in range(rows):
        max_h = 0
        for col_idx in range(cols):
            linear = row_idx * cols + col_idx
            if linear < len(sorted_cids):
                cid = sorted_cids[linear]
                _, h = group_sizes[cid]
                max_h = max(max_h, h)
        row_heights.append(max_h)

    group_layout: dict[int, tuple[int, int, int, int]] = {}
    for idx, cid in enumerate(sorted_cids):
        col_idx = idx % cols
        row_idx = idx // cols
        gx = sum(col_widths[:col_idx]) + col_idx * gap
        gy = sum(row_heights[:row_idx]) + row_idx * gap
        gw, gh = group_sizes[cid]
        group_layout[cid] = (gx, gy, gw, gh)

    all_canvas_nodes: set[str] = set()
    for members in communities.values():
        all_canvas_nodes.update(members)

    for idx, cid in enumerate(sorted_cids):
        members = communities[cid]
        community_name = (
            community_labels.get(cid, f"Community {cid}")
            if community_labels and cid is not None
            else f"Community {cid}"
        )
        gx, gy, gw, gh = group_layout[cid]
        canvas_color = CANVAS_COLORS[idx % len(CANVAS_COLORS)]

        canvas_nodes.append(
            {
                "id": f"g{cid}",
                "type": "group",
                "label": community_name,
                "x": gx,
                "y": gy,
                "width": gw,
                "height": gh,
                "color": canvas_color,
            }
        )

        inner_cols = group_cols[cid]
        members = [m for m in members if m in G and m in node_filenames]
        sorted_members = sorted(members, key=lambda n: G.nodes[n].get("label", n))
        for m_idx, node_id in enumerate(sorted_members):
            col = m_idx % inner_cols
            row = m_idx // inner_cols
            nx_x = gx + 20 + col * (180 + 20)
            nx_y = gy + 80 + row * (60 + 20)
            fname = node_filenames.get(node_id, safe_name(G.nodes[node_id].get("label", node_id)))
            canvas_nodes.append(
                {
                    "id": f"n_{node_id}",
                    "type": "file",
                    "file": f"{fname}.md",
                    "x": nx_x,
                    "y": nx_y,
                    "width": 180,
                    "height": 60,
                }
            )

    all_edges_weighted: list[tuple[float, str, str, str]] = []
    for u, v, edata in G.edges(data=True):
        if u in all_canvas_nodes and v in all_canvas_nodes:
            weight = edata.get("weight", 1.0)
            relation = edata.get("relation", "")
            conf = edata.get("confidence", "EXTRACTED")
            label = f"{relation} [{conf}]" if relation else f"[{conf}]"
            all_edges_weighted.append((weight, u, v, label))

    all_edges_weighted.sort(key=lambda x: -x[0])
    for weight, u, v, label in all_edges_weighted[:200]:
        canvas_edges.append(
            {
                "id": f"e_{u}_{v}",
                "fromNode": f"n_{u}",
                "toNode": f"n_{v}",
                "label": label,
            }
        )

    canvas_data = {"nodes": canvas_nodes, "edges": canvas_edges}
    Path(output_path).write_text(json.dumps(canvas_data, indent=2), encoding="utf-8")


def to_graphml(
    G: nx.Graph,
    communities: dict[int, list[str]],
    output_path: str,
) -> None:
    """Export graph as GraphML - opens in Gephi, yEd, and any GraphML-compatible tool.

    Community IDs are written as a node attribute so Gephi can colour by community.
    Edge confidence (EXTRACTED/INFERRED/AMBIGUOUS) is preserved as an edge attribute.
    """
    H = G.copy()
    node_community = _node_community_map(communities)
    for node_id in H.nodes():
        H.nodes[node_id]["community"] = node_community.get(node_id, -1)
    for _, attrs in H.nodes(data=True):
        for k in [k for k in attrs if k.startswith("_")]:
            del attrs[k]
    for _, _, attrs in H.edges(data=True):
        for k in [k for k in attrs if k.startswith("_")]:
            del attrs[k]

    def _graphml_safe(val):
        if val is None:
            return ""
        if isinstance(val, bool) or isinstance(val, (int, float, str)):
            return val
        try:
            return json.dumps(val, default=str, sort_keys=True)
        except (TypeError, ValueError):
            return str(val)

    for key, val in list(H.graph.items()):
        H.graph[key] = _graphml_safe(val)
    for node_id in H.nodes():
        for key, val in list(H.nodes[node_id].items()):
            H.nodes[node_id][key] = _graphml_safe(val)
    for u, v in H.edges():
        for key, val in list(H.edges[u, v].items()):
            H.edges[u, v][key] = _graphml_safe(val)

    out = Path(output_path)
    tmp = out.with_name(out.name + ".tmp")
    try:
        nx.write_graphml(H, str(tmp))
        os.replace(str(tmp), str(out))
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def to_svg(
    G: nx.Graph,
    communities: dict[int, list[str]],
    output_path: str,
    community_labels: dict[int, str] | None = None,
    figsize: tuple[int, int] = (20, 14),
) -> None:
    """Export graph as an SVG file using matplotlib + spring layout.

    Lightweight and embeddable - works in Obsidian notes, Notion, GitHub READMEs,
    and any markdown renderer. No JavaScript required.

    Node size scales with degree. Community colors match the HTML output.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
    except ImportError as e:
        raise ImportError("matplotlib not installed. Run: pip install matplotlib") from e

    node_community = _node_community_map(communities)

    fig, ax = plt.subplots(figsize=figsize, facecolor="#1a1a2e")
    ax.set_facecolor("#1a1a2e")
    ax.axis("off")

    pos = nx.spring_layout(G, seed=42, k=2.0 / (G.number_of_nodes() ** 0.5 + 1))

    degree = dict(G.degree())
    max_deg = max(degree.values(), default=1) or 1

    node_colors = [
        COMMUNITY_COLORS[node_community.get(n, 0) % len(COMMUNITY_COLORS)] for n in G.nodes()
    ]
    node_sizes = [300 + 1200 * (degree.get(n, 1) / max_deg) for n in G.nodes()]

    for u, v, data in G.edges(data=True):
        conf = data.get("confidence", "EXTRACTED")
        style = "solid" if conf == "EXTRACTED" else "dashed"
        alpha = 0.6 if conf == "EXTRACTED" else 0.3
        x0, y0 = pos[u]
        x1, y1 = pos[v]
        ax.plot(
            [x0, x1],
            [y0, y1],
            color="#aaaaaa",
            linewidth=0.8,
            linestyle=style,
            alpha=alpha,
            zorder=1,
        )

    nx.draw_networkx_nodes(G, pos, ax=ax, node_color=node_colors, node_size=node_sizes, alpha=0.9)
    nx.draw_networkx_labels(
        G,
        pos,
        ax=ax,
        labels={n: G.nodes[n].get("label", n) for n in G.nodes()},
        font_size=7,
        font_color="white",
    )

    if community_labels:
        patches = [
            mpatches.Patch(
                color=COMMUNITY_COLORS[cid % len(COMMUNITY_COLORS)],
                label=f"{label} ({len(communities.get(cid, []))})",
            )
            for cid, label in sorted(community_labels.items())
        ]
        ax.legend(
            handles=patches,
            loc="upper left",
            framealpha=0.7,
            facecolor="#2a2a4e",
            labelcolor="white",
            fontsize=8,
        )

    plt.tight_layout()
    plt.savefig(output_path, format="svg", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
