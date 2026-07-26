"""objc — moved verbatim from graphify/extract.py."""

from __future__ import annotations

from graphify.extractors.base import _file_stem, _make_id, _read_text
from graphify.extractors.engine import _cpp_declarator_name, _semantic_reference_edge
from graphify.extractors.resolution import _resolve_c_include_path
from pathlib import Path
from typing import Any


def _objc_local_var_types(body_node, source: bytes, table: dict[str, str]) -> None:
    """Collect ``var -> ClassName`` from ObjC local declarations (``Foo *f = ...;``)
    in a method body, for receiver typing in the cross-file message-send pass
    (#1556). Only a capitalized ``type_identifier`` with a single named declarator
    is recorded; a built-in/lower-cased type or an un-nameable declarator is skipped
    (precision over recall). Reuses the C++ declarator unwrapper (identical grammar).
    """
    stack = [body_node]
    while stack:
        n = stack.pop()
        if n.type == "method_definition" and n is not body_node:
            continue
        if n.type == "declaration":
            type_node = n.child_by_field_name("type")
            if type_node is None:
                for c in n.children:
                    if c.type == "type_identifier":
                        type_node = c
                        break
            if type_node is not None and type_node.type == "type_identifier":
                type_name = _read_text(type_node, source).strip()
                declarators = [
                    c
                    for c in n.children
                    if c.type in ("identifier", "pointer_declarator", "init_declarator")
                ]
                if type_name and type_name[:1].isupper() and len(declarators) == 1:
                    var = _cpp_declarator_name(declarators[0], source)
                    if var and var not in table:
                        table[var] = type_name
        for c in n.children:
            stack.append(c)


def extract_objc(path: Path) -> dict:
    """Extract interfaces, implementations, protocols, methods, and imports from .m/.mm/.h files."""
    try:
        import tree_sitter_objc as tsobjc
        from tree_sitter import Language, Parser
    except ImportError:
        return {"nodes": [], "edges": [], "error": "tree_sitter_objc not installed"}

    try:
        language = Language(tsobjc.language())
        parser = Parser(language)
        source = path.read_bytes()
        _OBJC_BLANK_MACROS = (b"NS_ASSUME_NONNULL_BEGIN", b"NS_ASSUME_NONNULL_END")
        for _m in _OBJC_BLANK_MACROS:
            source = source.replace(_m, b" " * len(_m))
        tree = parser.parse(source)
        root = tree.root_node
    except Exception as e:
        return {"nodes": [], "edges": [], "error": str(e)}

    stem = _file_stem(path)
    str_path = str(path)
    nodes: list[dict] = []
    edges: list[dict] = []
    seen_ids: set[str] = set()
    method_bodies: list[tuple[str, Any, str]] = []
    raw_calls: list[dict] = []
    objc_type_table: dict[str, str] = {}

    def add_node(nid: str, label: str, line: int) -> None:
        if nid not in seen_ids:
            seen_ids.add(nid)
            nodes.append(
                {
                    "id": nid,
                    "label": label,
                    "file_type": "code",
                    "source_file": str_path,
                    "source_location": f"L{line}",
                }
            )

    def add_edge(
        src: str,
        tgt: str,
        relation: str,
        line: int,
        confidence: str = "EXTRACTED",
        weight: float = 1.0,
        context: str | None = None,
    ) -> None:
        edge = {
            "source": src,
            "target": tgt,
            "relation": relation,
            "confidence": confidence,
            "source_file": str_path,
            "source_location": f"L{line}",
            "weight": weight,
        }
        if context:
            edge["context"] = context
        edges.append(edge)

    file_nid = _make_id(str(path))
    add_node(file_nid, path.name, 1)

    def _read(node) -> str:
        return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    def _get_name(node, field: str) -> str | None:
        n = node.child_by_field_name(field)
        return _read(n) if n else None

    def _type_identifiers(node):
        """Yield every type_identifier under a property's type node, descending
        through generic_specifier/type_name so NSArray<Product *> yields both
        NSArray and the element type Product (the generic case was invisible
        because the type was wrapped in a generic_specifier, not a bare
        type_identifier child) (#1475)."""
        if node.type == "type_identifier":
            yield node
            return
        for c in node.children:
            yield from _type_identifiers(c)

    def ensure_named_node(name: str, line: int) -> str:
        nid = _make_id(stem, name)
        if nid in seen_ids:
            return nid
        nid = _make_id(name)
        if nid not in seen_ids:
            seen_ids.add(nid)
            nodes.append(
                {
                    "id": nid,
                    "label": name,
                    "file_type": "code",
                    "source_file": "",
                    "source_location": "",
                    "origin_file": str_path,
                }
            )
        return nid

    def walk(node, parent_nid: str | None = None) -> None:
        t = node.type
        line = node.start_point[0] + 1

        if t == "preproc_include":
            for child in node.children:
                if child.type == "system_lib_string":
                    raw = _read(child).strip("<>")
                    module = raw.split("/")[-1].replace(".h", "")
                    if module:
                        tgt_nid = _make_id(module)
                        add_edge(file_nid, tgt_nid, "imports", line, context="import")
                elif child.type == "string_literal":
                    for sub in child.children:
                        if sub.type == "string_content":
                            raw = _read(sub)
                            resolved = _resolve_c_include_path(raw, str_path)
                            if resolved is not None:
                                add_edge(
                                    file_nid,
                                    _make_id(str(resolved)),
                                    "imports",
                                    line,
                                    context="import",
                                )
                            else:
                                module = raw.split("/")[-1].replace(".h", "")
                                if module:
                                    add_edge(
                                        file_nid,
                                        _make_id(module),
                                        "imports",
                                        line,
                                        context="import",
                                    )
            return

        if t == "module_import":
            path_node = node.child_by_field_name("path")
            if path_node is not None:
                module = _read(path_node).split(".")[0].strip()
                if module:
                    add_edge(file_nid, _make_id(module), "imports", line, context="import")
            return

        if t == "class_interface":
            identifiers = [c for c in node.children if c.type == "identifier"]
            if not identifiers:
                for child in node.children:
                    walk(child, parent_nid)
                return
            name = _read(identifiers[0])
            cls_nid = _make_id(stem, name)
            add_node(cls_nid, name, line)
            add_edge(file_nid, cls_nid, "contains", line)
            colon_seen = False
            for child in node.children:
                if child.type == ":":
                    colon_seen = True
                elif colon_seen and child.type == "identifier":
                    super_nid = ensure_named_node(_read(child), line)
                    add_edge(cls_nid, super_nid, "inherits", line)
                    colon_seen = False
                elif child.type == "parameterized_arguments":
                    for sub in child.children:
                        if sub.type == "type_name":
                            for s in sub.children:
                                if s.type == "type_identifier":
                                    proto_nid = ensure_named_node(_read(s), line)
                                    add_edge(cls_nid, proto_nid, "implements", line)
                elif child.type == "property_declaration":
                    prop_line = child.start_point[0] + 1
                    for sub in child.children:
                        if sub.type == "struct_declaration":
                            seen_types: set[str] = set()
                            for s in sub.children:
                                if s.type in ("struct_declarator", ";"):
                                    continue
                                for ti in _type_identifiers(s):
                                    tname = _read(ti)
                                    if tname in seen_types:
                                        continue
                                    seen_types.add(tname)
                                    type_nid = ensure_named_node(tname, prop_line)
                                    edges.append(
                                        _semantic_reference_edge(
                                            cls_nid, type_nid, "field", str_path, prop_line
                                        )
                                    )
                elif child.type == "method_declaration":
                    walk(child, cls_nid)
            return

        if t == "class_implementation":
            name = None
            for child in node.children:
                if child.type == "identifier":
                    name = _read(child)
                    break
            if not name:
                for child in node.children:
                    walk(child, parent_nid)
                return
            impl_nid = _make_id(stem, name)
            if impl_nid not in seen_ids:
                add_node(impl_nid, name, line)
                add_edge(file_nid, impl_nid, "contains", line)
            for child in node.children:
                if child.type == "implementation_definition":
                    for sub in child.children:
                        walk(sub, impl_nid)
            return

        if t == "protocol_declaration":
            name = None
            for child in node.children:
                if child.type == "identifier":
                    name = _read(child)
                    break
            if name:
                proto_nid = _make_id(stem, name)
                add_node(proto_nid, f"<{name}>", line)
                add_edge(file_nid, proto_nid, "contains", line)
                for child in node.children:
                    if child.type == "protocol_reference_list":
                        for sub in child.children:
                            if sub.type == "identifier":
                                base_nid = ensure_named_node(_read(sub), line)
                                if base_nid != proto_nid:
                                    add_edge(proto_nid, base_nid, "implements", line)
                for child in node.children:
                    walk(child, proto_nid)
            return

        if t in ("method_declaration", "method_definition"):
            container = parent_nid or file_nid
            prefix = "-"
            for child in node.children:
                if child.type in ("+", "-"):
                    prefix = child.type
                    break
            parts = [_read(c) for c in node.children if c.type == "identifier"]
            method_name = "".join(parts) if parts else None
            if method_name:
                method_nid = _make_id(container, method_name)
                add_node(method_nid, f"{prefix}{method_name}", line)
                add_edge(container, method_nid, "method", line)
                if t == "method_definition":
                    method_bodies.append((method_nid, node, container))
            return

        for child in node.children:
            walk(child, parent_nid)

    walk(root)

    all_method_nids = {n["id"] for n in nodes if n["id"] != file_nid}
    class_method_nids: dict[str, set[str]] = {}
    for m_nid, _, container_nid in method_bodies:
        class_method_nids.setdefault(container_nid, set()).add(m_nid)
    seen_calls: set[tuple[str, str]] = set()
    for _m_nid, body_node, _container in method_bodies:
        _objc_local_var_types(body_node, source, objc_type_table)

    for caller_nid, body_node, container_nid in method_bodies:
        sibling_nids = class_method_nids.get(container_nid, set())

        def walk_calls(n) -> None:
            if n.type == "message_expression":
                meth = n.child_by_field_name("method")
                recv = n.child_by_field_name("receiver")
                if (
                    meth is not None
                    and meth.type == "identifier"
                    and _read(meth) == "alloc"
                    and recv is not None
                    and recv.type == "identifier"
                ):
                    tname = _read(recv)
                    ref_line = n.start_point[0] + 1
                    type_nid = ensure_named_node(tname, ref_line)
                    if type_nid != caller_nid:
                        edges.append(
                            _semantic_reference_edge(
                                caller_nid, type_nid, "type", str_path, ref_line
                            )
                        )
                sel_parts = [
                    _read(child)
                    for i, child in enumerate(n.children)
                    if n.field_name_for_child(i) == "method" and child.type == "identifier"
                ]
                method_name = "".join(sel_parts)
                if method_name:
                    needle = _make_id("", method_name).lstrip("_")
                    for candidate in all_method_nids:
                        if candidate.endswith(needle):
                            pair = (caller_nid, candidate)
                            if pair not in seen_calls and caller_nid != candidate:
                                seen_calls.add(pair)
                                add_edge(
                                    caller_nid,
                                    candidate,
                                    "calls",
                                    n.start_point[0] + 1,
                                    confidence="EXTRACTED",
                                    weight=1.0,
                                    context="call",
                                )
                    if recv is not None and recv.type == "identifier":
                        raw_calls.append(
                            {
                                "caller_nid": caller_nid,
                                "callee": method_name,
                                "is_member_call": True,
                                "source_file": str_path,
                                "source_location": f"L{n.start_point[0] + 1}",
                                "receiver": _read(recv),
                                "lang": "objc",
                            }
                        )
            elif n.type == "field_expression":
                for child in n.children:
                    if child.type == "field_identifier":
                        field_name = _read(child)
                        target = _make_id(container_nid, field_name)
                        if target in sibling_nids and target != caller_nid:
                            pair = (caller_nid, target)
                            if pair not in seen_calls:
                                seen_calls.add(pair)
                                add_edge(
                                    caller_nid,
                                    target,
                                    "accesses",
                                    n.start_point[0] + 1,
                                    confidence="EXTRACTED",
                                    weight=1.0,
                                )
            elif n.type == "selector_expression":
                sel_parts = [_read(c) for c in n.children if c.type == "identifier"]
                sel_name = "".join(sel_parts)
                if sel_name:
                    matches = sorted(
                        {
                            m
                            for m, _, cont in method_bodies
                            if m == _make_id(cont, sel_name) and m != caller_nid
                        }
                    )
                    if len(matches) == 1:
                        pair = (caller_nid, matches[0])
                        if pair not in seen_calls:
                            seen_calls.add(pair)
                            add_edge(
                                caller_nid,
                                matches[0],
                                "calls",
                                n.start_point[0] + 1,
                                confidence="EXTRACTED",
                                weight=1.0,
                                context="call",
                            )
            for child in n.children:
                walk_calls(child)

        walk_calls(body_node)

    result = {
        "nodes": nodes,
        "edges": edges,
        "raw_calls": raw_calls,
        "input_tokens": 0,
        "output_tokens": 0,
    }
    if objc_type_table:
        result["objc_type_table"] = {"path": str_path, "table": objc_type_table}
    return result
