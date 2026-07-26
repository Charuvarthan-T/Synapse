"""Deterministic structural extraction from source code using tree-sitter. Outputs nodes+edges dicts."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .cache import load_cached, save_cached
from .mcp_ingest import extract_mcp_config, is_mcp_config_path
from .manifest_ingest import extract_package_manifest, is_package_manifest_path
from .resolver_registry import (
    LanguageResolver,
    register as register_language_resolver,
    run_language_resolvers,
)
from .ruby_resolution import resolve_ruby_member_calls
from .pascal_resolution import resolve_pascal_inherited_calls

from graphify.extractors.base import (  # noqa: F401
    _LANGUAGE_BUILTIN_GLOBALS,
    _file_stem,
    _make_id,
    _read_text,
)
from graphify.extractors.apex import extract_apex  # noqa: F401
from graphify.extractors.bash import extract_bash  # noqa: F401
from graphify.extractors.blade import extract_blade  # noqa: F401
from graphify.extractors.csharp import (
    CsharpNameResolver,
    _resolve_cross_file_csharp_imports,
    _resolve_csharp_type_references,
)
from graphify.extractors.dart import extract_dart  # noqa: F401
from graphify.extractors.dm import extract_dm, extract_dmf, extract_dmi, extract_dmm  # noqa: F401
from graphify.extractors.elixir import extract_elixir  # noqa: F401
from graphify.extractors.fortran import _cpp_preprocess, extract_fortran  # noqa: F401
from graphify.extractors.go import extract_go  # noqa: F401
from graphify.extractors.json_config import extract_json  # noqa: F401
from graphify.extractors.markdown import extract_markdown  # noqa: F401
from graphify.extractors.pascal_forms import extract_delphi_form, extract_lazarus_form  # noqa: F401
from graphify.extractors.powershell import extract_powershell, extract_powershell_manifest  # noqa: F401
from graphify.extractors.razor import extract_razor  # noqa: F401
from graphify.extractors.rust import extract_rust  # noqa: F401
from graphify.extractors.sln import extract_sln  # noqa: F401
from graphify.extractors.sql import extract_sql  # noqa: F401
from graphify.extractors.terraform import extract_terraform  # noqa: F401
from graphify.extractors.verilog import extract_verilog  # noqa: F401
from graphify.extractors.zig import extract_zig  # noqa: F401
from graphify.security import sanitize_metadata
from graphify.paths import disambiguate_ambiguous_candidates

from graphify.extractors.models import (
    LanguageConfig,
    _JS_CACHE_BYPASS_SUFFIXES,
    _NamespaceExportFact,
    _StarExportFact,
    _SymbolAliasFact,
    _SymbolDeclarationFact,
    _SymbolExportFact,
    _SymbolImportFact,
    _SymbolResolutionFacts,
    _SymbolUseFact,
    _WORKSPACE_PACKAGE_CACHE,
)  # noqa: E402,F401

from graphify.extractors.resolution import (  # noqa: E402,F401
    _DECLDEF_HEADER_SUFFIXES,
    _DECLDEF_IMPL_SUFFIXES,
    _EXPORT_CONDITION_PRIORITY,
    _JS_INDEX_FILES,
    _JS_PRIMITIVE_TYPES,
    _JS_RESOLVE_EXTS,
    _TSCONFIG_ALIAS_CACHE,
    _VUE_SCRIPT_LANG_RE,
    _VUE_SCRIPT_RE,
    _WORKSPACE_MANIFEST_NAMES,
    _apply_symbol_resolution_facts,
    _augment_symbol_resolution_edges,
    _collect_js_symbol_resolution_facts,
    _collect_python_symbol_resolution_facts,
    _contained_in_package,
    _decldef_class_stem,
    _disambiguate_colliding_node_ids,
    _find_workspace_root,
    _is_type_like_definition,
    _js_call_identifier,
    _js_default_export_name,
    _js_default_import_name,
    _js_export_clause,
    _js_export_statement_is_star,
    _js_exported_declaration_names,
    _js_lexical_aliases,
    _js_module_specifier,
    _js_named_specifiers,
    _js_namespace_export_name,
    _js_source_path,
    _js_top_level_function_bodies,
    _load_tsconfig_aliases,
    _load_tsconfig_base_url,
    _load_workspace_packages,
    _match_tsconfig_alias,
    _merge_decl_def_classes,
    _node_disambiguation_source_key,
    _package_entry_candidates,
    _parse_js_tree,
    _parse_python_tree,
    _pascal_class_stem_cache,
    _pascal_project_root,
    _pascal_resolve_class,
    _pascal_resolve_unit,
    _pascal_unit_cache,
    _pnpm_workspace_globs,
    _python_call_identifier,
    _python_import_from_module,
    _python_imported_names,
    _python_top_level_function_bodies,
    _read_tsconfig_aliases,
    _resolve_c_include_path,
    _resolve_cross_file_imports,
    _resolve_cross_file_java_imports,
    _resolve_export_target,
    _resolve_java_type_references,
    _resolve_php_type_references,
    _resolve_js_import_path,
    _resolve_js_import_target,
    _resolve_js_module_path,
    _resolve_lua_import_target,
    _resolve_python_module_path,
    _resolve_tsconfig_alias,
    _resolve_workspace_import,
    _source_key,
    _strip_jsonc,
    _ts_collect_type_refs,
    _ts_heritage_clause_entries,
    _ts_walk_class_members,
    _vue_mask_non_script,
    _walk_js_tree,
    _walk_python_tree,
    _workspace_globs,
)

from graphify.symbol_resolution import resolve_bash_source_edges  # noqa: E402

from graphify.extractors.engine import (
    REFERENCE_CONTEXTS,
    _CSHARP_TYPE_PARAMETER_SCOPE_DECLARATIONS,
    _C_PRIMITIVE_TYPE_NODES,
    _JAVA_BUILTIN_TYPES,
    _JAVA_TYPE_PARAMETER_SCOPE_DECLARATIONS,
    _JS_FUNCTION_VALUE_TYPES,
    _JS_SCOPE_BOUNDARY,
    _PYTHON_ANNOTATION_NOISE,
    _PYTHON_TYPE_CONTAINERS,
    _RUBY_CLASS_FACTORIES,
    _c_collect_type_refs,
    _cpp_collect_type_refs,
    _cpp_declarator_name,
    _cpp_local_var_types,
    _csharp_attribute_names,
    _csharp_classify_base,
    _csharp_collect_type_refs,
    _csharp_extra_walk,
    _csharp_member_type_table,
    _csharp_namespace_id,
    _csharp_namespace_name,
    _csharp_pre_scan_interfaces,
    _csharp_type_parameters_in_scope,
    _dynamic_import_js,
    _extract_generic,
    _find_body,
    _find_require_call,
    _get_cpp_func_name,
    _java_annotation_names,
    _java_collect_type_refs,
    _java_extra_walk,
    _java_type_parameters_in_scope,
    _js_collect_pattern_idents,
    _js_dispatch_value_idents,
    _js_extra_walk,
    _js_local_bound_names,
    _js_member_assignment_target,
    _js_module_bound_names,
    _kotlin_collect_type_refs,
    _kotlin_function_return_type_node,
    _kotlin_property_type_node,
    _kotlin_user_type_name,
    _php_collect_type_refs,
    _php_method_return_type_node,
    _php_name_text,
    _python_collect_assignment_targets,
    _python_collect_param_refs,
    _python_collect_type_refs,
    _python_local_bound_names,
    _python_module_bound_names,
    _python_param_names,
    _read_csharp_type_name,
    _require_imports_js,
    _ruby_const_last_name,
    _ruby_extra_walk,
    _ruby_local_class_bindings,
    _ruby_new_class_name,
    _scala_collect_type_refs,
    _semantic_reference_edge,
    _source_location,
    _swift_classify_base,
    _swift_collect_type_refs,
    _swift_constructor_type,
    _swift_declaration_keyword,
    _swift_extra_walk,
    _swift_local_var_types,
    _swift_pre_scan,
    _swift_property_name,
    _swift_property_type_node,
    _swift_receiver_name,
    _swift_user_type_name,
    _ts_decorator_name,
    _ts_descendant_decorators,
    _ts_emit_decorator_edges,
    _ts_extra_walk,
    _ts_method_name,
    _ts_receiver_type_table,
)  # noqa: E402,F401

from graphify.extractors.pascal import (
    _PAS_BEGIN_END_TOKEN_RE,
    _PAS_CALL_RE,
    _PAS_END_SEMI_RE,
    _PAS_IMPL_HEADER_RE,
    _PAS_KEYWORDS,
    _PAS_METHOD_DECL_RE,
    _PAS_MODULE_RE,
    _PAS_TOKEN_RE,
    _PAS_TYPE_HEADER_RE,
    _PAS_USES_RE,
    _extract_pascal_regex,
    _pascal_find_body,
    _pascal_split_bases,
    _pascal_split_sections,
    _pascal_split_uses,
    _pascal_strip_comments,
    extract_pascal,
)  # noqa: E402,F401

from graphify.extractors.objc import _objc_local_var_types, extract_objc  # noqa: E402,F401

from graphify.extractors.julia import extract_julia  # noqa: E402,F401

_RECURSION_LIMIT = 10_000


def _raise_recursion_limit() -> None:
    if sys.getrecursionlimit() < _RECURSION_LIMIT:
        sys.setrecursionlimit(_RECURSION_LIMIT)


def _safe_extract(extractor: Callable, path: Path) -> dict:
    try:
        return extractor(path)
    except RecursionError:
        print(f"  warning: skipped {path} (recursion limit exceeded)", file=sys.stderr, flush=True)
        return {"nodes": [], "edges": [], "error": "recursion_limit_exceeded"}
    except Exception as e:
        if os.environ.get("GRAPHIFY_DEBUG"):
            import traceback

            traceback.print_exc(file=sys.stderr)
        print(f"  warning: skipped {path} ({type(e).__name__}: {e})", file=sys.stderr, flush=True)
        return {"nodes": [], "edges": [], "error": f"{type(e).__name__}: {e}"}


def _file_node_id(rel_path: Path) -> str:
    """File-level node ID matching the skill.md spec: ``{parent_dir}_{stem}`` —
    one parent directory level, no extension. ``rel_path`` MUST be relative to
    the project root so top-level files collapse to a bare stem (``setup.py`` ->
    ``setup``) instead of picking up the root directory name. This must equal the
    ID semantic subagents generate, or AST and semantic extraction split a file
    into two disconnected ghost nodes (#1033)."""
    return _make_id(_file_stem(rel_path))


def _repoint_python_package_imports(paths, all_nodes, all_edges, root) -> None:
    """Repoint Python absolute-import edges to the real file node under a nested
    (e.g. ``src/``) package root (#2072).

    Absolute imports target an id derived from the dotted module path
    (``_make_id('pkg.mod')`` -> ``pkg_mod``), but file-node ids are
    scan-root-relative (``src_pkg_mod`` when the code lives under ``src/``), so
    the edge dangles and is silently dropped — the graph loses most ``imports``
    edges purely because of where the scan started. Build an alias map from the
    dotted-module id to the real file-node id by detecting each ``.py`` file's
    package root (the contiguous run of ancestor dirs carrying ``__init__.py``)
    and rewrite matching ``imports``/``imports_from`` edge targets. Guards: never
    shadow an existing node id, and drop an alias claimed by more than one file
    (ambiguous -> leave dangling, as before). Files whose package root IS the
    scan root are skipped (ids already coincide)."""
    try:
        root = Path(root).resolve()
    except OSError:
        root = Path(root)
    node_ids = {n.get("id") for n in all_nodes if isinstance(n, dict)}
    alias_to_files: dict[str, set[str]] = {}
    for p in paths:
        if p.suffix.lower() not in (".py", ".pyi"):
            continue
        try:
            rel = Path(p).resolve().relative_to(root)
        except (ValueError, OSError):
            continue
        parts = rel.parts
        if len(parts) < 2:
            continue
        d = Path(p).resolve().parent
        levels = 0
        while levels < len(parts) - 1 and (d / "__init__.py").is_file():
            levels += 1
            d = d.parent
        if levels == 0:
            continue
        mod_parts = parts[-(levels + 1) :]
        if len(mod_parts) == len(parts):
            continue
        file_node = _file_node_id(rel)
        alias = _make_id(str(Path(*mod_parts).with_suffix("")))
        alias_to_files.setdefault(alias, set()).add(file_node)
        if p.name in ("__init__.py", "__init__.pyi") and len(mod_parts) > 1:
            pkg_alias = _make_id(str(Path(*mod_parts[:-1])))
            alias_to_files.setdefault(pkg_alias, set()).add(file_node)
    alias_map = {
        a: next(iter(fs)) for a, fs in alias_to_files.items() if len(fs) == 1 and a not in node_ids
    }
    if not alias_map:
        return
    for e in all_edges:
        if (
            isinstance(e, dict)
            and e.get("relation") in ("imports", "imports_from")
            and str(e.get("source_file", "")).lower().endswith((".py", ".pyi"))
        ):
            tgt = e.get("target")
            if tgt in alias_map:
                e["target"] = alias_map[tgt]


SEMANTIC_RELATIONS = frozenset(
    {
        "inherits",
        "implements",
        "mixes_in",
        "embeds",
        "references",
        "calls",
        "imports",
        "imports_from",
        "re_exports",
        "contains",
        "method",
    }
)


def _resolve_name(node, source: bytes, config: LanguageConfig) -> str | None:
    """Get the name from a node using config.name_field, falling back to child types."""
    if config.resolve_function_name_fn is not None:
        return None
    n = node.child_by_field_name(config.name_field)
    if n:
        return _read_text(n, source)
    for child in node.children:
        if child.type in config.name_fallback_child_types:
            return _read_text(child, source)
    return None


def _import_python(
    node,
    source: bytes,
    file_nid: str,
    stem: str,
    edges: list,
    str_path: str,
    scope_stack: list[str] | None = None,
) -> None:
    t = node.type
    if t == "import_statement":
        for child in node.children:
            if child.type in ("dotted_name", "aliased_import"):
                raw = _read_text(child, source)
                raw_module, _, raw_alias = raw.partition(" as ")
                module_name = raw_module.strip().lstrip(".")
                tgt_nid = _make_id(module_name)
                edge = {
                    "source": file_nid,
                    "target": tgt_nid,
                    "relation": "imports",
                    "context": "import",
                    "confidence": "EXTRACTED",
                    "source_file": str_path,
                    "source_location": f"L{node.start_point[0] + 1}",
                    "weight": 1.0,
                }
                if raw_alias:
                    edge["local_alias"] = raw_alias.strip()
                edges.append(edge)
    elif t == "import_from_statement":
        module_node = node.child_by_field_name("module_name")
        if module_node:
            raw = _read_text(module_node, source)
            if raw.startswith("."):
                dots = len(raw) - len(raw.lstrip("."))
                module_name = raw.lstrip(".")
                base = Path(str_path).parent
                for _ in range(dots - 1):
                    base = base.parent
                rel = (module_name.replace(".", "/") + ".py") if module_name else "__init__.py"
                tgt_nid = _make_id(str(base / rel))
            else:
                tgt_nid = _make_id(raw)
            edges.append(
                {
                    "source": file_nid,
                    "target": tgt_nid,
                    "relation": "imports_from",
                    "context": "import",
                    "confidence": "EXTRACTED",
                    "source_file": str_path,
                    "source_location": f"L{node.start_point[0] + 1}",
                    "weight": 1.0,
                }
            )


def _import_js(
    node,
    source: bytes,
    file_nid: str,
    stem: str,
    edges: list,
    str_path: str,
    scope_stack: list[str] | None = None,
) -> None:
    is_reexport = node.type == "export_statement"
    if is_reexport:
        has_from = any(
            child.type == "from" or (_read_text(child, source) == "from")
            for child in node.children
            if child.type in ("from", "identifier")
        )
        if not has_from:
            has_from = any(child.type == "string" for child in node.children)
            if not has_from:
                return

    resolved_path: "Path | None" = None
    module_string = None
    for child in node.children:
        if child.type == "string":
            module_string = child
            break
        if child.type == "import_require_clause":
            module_string = next((sub for sub in child.children if sub.type == "string"), None)
            break
    if module_string is not None:
        raw = _read_text(module_string, source).strip("'\"` ")
        resolved = _resolve_js_import_target(raw, str_path)
        if resolved is not None:
            tgt_nid, resolved_path = resolved
            edge = {
                "source": file_nid,
                "target": tgt_nid,
                "relation": "imports_from",
                "context": "re-export" if is_reexport else "import",
                "confidence": "EXTRACTED",
                "source_file": str_path,
                "source_location": f"L{node.start_point[0] + 1}",
                "weight": 1.0,
            }
            if resolved_path is not None:
                edge["target_file"] = str(resolved_path)
            edges.append(edge)

    if resolved_path is not None:
        target_stem = _file_stem(resolved_path)
        line = node.start_point[0] + 1

        if is_reexport:
            for child in node.children:
                if child.type == "export_clause":
                    for spec in child.children:
                        if spec.type == "export_specifier":
                            name_node = spec.child_by_field_name("name")
                            if name_node:
                                sym = _read_text(name_node, source)
                                if sym == "default":
                                    continue
                                edges.append(
                                    {
                                        "source": file_nid,
                                        "target": _make_id(target_stem, sym),
                                        "relation": "re_exports",
                                        "context": "re-export",
                                        "confidence": "EXTRACTED",
                                        "source_file": str_path,
                                        "source_location": f"L{line}",
                                        "weight": 1.0,
                                        "target_file": str(resolved_path),
                                    }
                                )
        else:
            for child in node.children:
                if child.type == "import_clause":
                    for sub in child.children:
                        if sub.type == "named_imports":
                            for spec in sub.children:
                                if spec.type == "import_specifier":
                                    name_node = spec.child_by_field_name("name")
                                    if name_node:
                                        sym = _read_text(name_node, source)
                                        edges.append(
                                            {
                                                "source": file_nid,
                                                "target": _make_id(target_stem, sym),
                                                "relation": "imports",
                                                "context": "import",
                                                "confidence": "EXTRACTED",
                                                "source_file": str_path,
                                                "source_location": f"L{line}",
                                                "weight": 1.0,
                                                "target_file": str(resolved_path),
                                            }
                                        )


def _import_java(
    node,
    source: bytes,
    file_nid: str,
    stem: str,
    edges: list,
    str_path: str,
    scope_stack: list[str] | None = None,
) -> None:
    def _walk_scoped(n) -> str:
        parts: list[str] = []
        cur = n
        while cur:
            if cur.type == "scoped_identifier":
                name_node = cur.child_by_field_name("name")
                if name_node:
                    parts.append(_read_text(name_node, source))
                cur = cur.child_by_field_name("scope")
            elif cur.type == "identifier":
                parts.append(_read_text(cur, source))
                break
            else:
                break
        parts.reverse()
        return ".".join(parts)

    for child in node.children:
        if child.type in ("scoped_identifier", "identifier"):
            path_str = _walk_scoped(child)
            module_name = path_str.split(".")[-1].strip("*").strip(".") or (
                path_str.split(".")[-2] if len(path_str.split(".")) > 1 else path_str
            )
            if module_name:
                tgt_nid = _make_id(module_name)
                edges.append(
                    {
                        "source": file_nid,
                        "target": tgt_nid,
                        "relation": "imports",
                        "context": "import",
                        "confidence": "EXTRACTED",
                        "source_file": str_path,
                        "source_location": f"L{node.start_point[0] + 1}",
                        "weight": 1.0,
                    }
                )
            break


def _import_c(
    node,
    source: bytes,
    file_nid: str,
    stem: str,
    edges: list,
    str_path: str,
    scope_stack: list[str] | None = None,
) -> None:
    for child in node.children:
        if child.type in ("string_literal", "system_lib_string", "string"):
            raw = _read_text(child, source).strip('"<> ')
            if child.type != "system_lib_string":
                resolved = _resolve_c_include_path(raw, str_path)
                if resolved is not None:
                    tgt_nid = _make_id(str(resolved))
                    edges.append(
                        {
                            "source": file_nid,
                            "target": tgt_nid,
                            "relation": "imports",
                            "context": "import",
                            "confidence": "EXTRACTED",
                            "source_file": str_path,
                            "source_location": f"L{node.start_point[0] + 1}",
                            "weight": 1.0,
                        }
                    )
                    break
            module_name = raw.split("/")[-1].split(".")[0]
            if module_name:
                tgt_nid = _make_id(module_name)
                edges.append(
                    {
                        "source": file_nid,
                        "target": tgt_nid,
                        "relation": "imports",
                        "context": "import",
                        "confidence": "EXTRACTED",
                        "source_file": str_path,
                        "source_location": f"L{node.start_point[0] + 1}",
                        "weight": 1.0,
                    }
                )
            break


def _import_csharp(
    node,
    source: bytes,
    file_nid: str,
    stem: str,
    edges: list,
    str_path: str,
    scope_stack: list[str] | None = None,
) -> None:
    text = _read_text(node, source).strip().rstrip(";")
    if text.startswith("global "):
        text = text[len("global ") :].strip()
    if not text.startswith("using"):
        return
    body = text[len("using") :].strip()
    using_kind, alias, target_fqn = "namespace", None, body
    if body.startswith("static "):
        using_kind, target_fqn = "static", body[len("static ") :].strip()
    elif "=" in body:
        lhs, rhs = body.split("=", 1)
        using_kind, alias, target_fqn = "alias", lhs.strip(), rhs.strip()
    if not target_fqn:
        return
    edges.append(
        {
            "source": file_nid,
            "target": _make_id(target_fqn),
            "relation": "imports",
            "context": "import",
            "confidence": "EXTRACTED",
            "source_file": str_path,
            "source_location": f"L{node.start_point[0] + 1}",
            "weight": 1.0,
            "metadata": sanitize_metadata(
                {
                    k: v
                    for k, v in {
                        "using_kind": using_kind,
                        "alias": alias,
                        "target_fqn": target_fqn,
                        "scope_kind": "namespace" if scope_stack else "file",
                        "scope_id": scope_stack[-1] if scope_stack else None,
                    }.items()
                    if v is not None
                }
            ),
        }
    )


def _import_kotlin(
    node,
    source: bytes,
    file_nid: str,
    stem: str,
    edges: list,
    str_path: str,
    scope_stack: list[str] | None = None,
) -> None:
    path_node = node.child_by_field_name("path")
    if path_node:
        raw = _read_text(path_node, source)
        module_name = raw.split(".")[-1].strip()
        if module_name:
            tgt_nid = _make_id(module_name)
            edges.append(
                {
                    "source": file_nid,
                    "target": tgt_nid,
                    "relation": "imports",
                    "context": "import",
                    "confidence": "EXTRACTED",
                    "source_file": str_path,
                    "source_location": f"L{node.start_point[0] + 1}",
                    "weight": 1.0,
                }
            )
        return
    for child in node.children:
        if child.type == "identifier":
            raw = _read_text(child, source)
            tgt_nid = _make_id(raw)
            edges.append(
                {
                    "source": file_nid,
                    "target": tgt_nid,
                    "relation": "imports",
                    "context": "import",
                    "confidence": "EXTRACTED",
                    "source_file": str_path,
                    "source_location": f"L{node.start_point[0] + 1}",
                    "weight": 1.0,
                }
            )
            break


def _import_scala(
    node,
    source: bytes,
    file_nid: str,
    stem: str,
    edges: list,
    str_path: str,
    scope_stack: list[str] | None = None,
) -> None:
    for child in node.children:
        if child.type in ("stable_id", "identifier"):
            raw = _read_text(child, source)
            module_name = raw.split(".")[-1].strip("{} ")
            if module_name and module_name != "_":
                tgt_nid = _make_id(module_name)
                edges.append(
                    {
                        "source": file_nid,
                        "target": tgt_nid,
                        "relation": "imports",
                        "context": "import",
                        "confidence": "EXTRACTED",
                        "source_file": str_path,
                        "source_location": f"L{node.start_point[0] + 1}",
                        "weight": 1.0,
                    }
                )
            break


def _import_php(
    node,
    source: bytes,
    file_nid: str,
    stem: str,
    edges: list,
    str_path: str,
    scope_stack: list[str] | None = None,
) -> None:
    for child in node.children:
        if child.type in ("qualified_name", "name", "identifier"):
            raw = _read_text(child, source)
            module_name = raw.split("\\")[-1].strip()
            if module_name:
                tgt_nid = _make_id(module_name)
                edges.append(
                    {
                        "source": file_nid,
                        "target": tgt_nid,
                        "relation": "imports",
                        "context": "import",
                        "confidence": "EXTRACTED",
                        "source_file": str_path,
                        "source_location": f"L{node.start_point[0] + 1}",
                        "weight": 1.0,
                    }
                )
            break


def _get_c_func_name(node, source: bytes) -> str | None:
    """Recursively unwrap declarator to find the innermost identifier (C)."""
    if node.type == "identifier":
        return _read_text(node, source)
    decl = node.child_by_field_name("declarator")
    if decl:
        return _get_c_func_name(decl, source)
    for child in node.children:
        if child.type == "identifier":
            return _read_text(child, source)
    return None


_PYTHON_CONFIG = LanguageConfig(
    ts_module="tree_sitter_python",
    class_types=frozenset({"class_definition"}),
    function_types=frozenset({"function_definition"}),
    import_types=frozenset({"import_statement", "import_from_statement"}),
    call_types=frozenset({"call"}),
    call_function_field="function",
    call_accessor_node_types=frozenset({"attribute"}),
    call_accessor_field="attribute",
    call_accessor_object_field="object",
    function_boundary_types=frozenset({"function_definition"}),
    import_handler=_import_python,
)

_JS_CONFIG = LanguageConfig(
    ts_module="tree_sitter_javascript",
    class_types=frozenset({"class_declaration"}),
    function_types=frozenset(
        {"function_declaration", "generator_function_declaration", "method_definition"}
    ),
    import_types=frozenset({"import_statement", "export_statement"}),
    call_types=frozenset({"call_expression", "new_expression"}),
    call_function_field="function",
    call_accessor_node_types=frozenset({"member_expression"}),
    call_accessor_field="property",
    call_accessor_object_field="object",
    function_boundary_types=frozenset(
        {
            "function_declaration",
            "generator_function_declaration",
            "arrow_function",
            "method_definition",
        }
    ),
    import_handler=_import_js,
)

_TS_CONFIG = LanguageConfig(
    ts_module="tree_sitter_typescript",
    ts_language_fn="language_typescript",
    class_types=frozenset(
        {
            "class_declaration",
            "abstract_class_declaration",
            "interface_declaration",
            "enum_declaration",
            "type_alias_declaration",
        }
    ),
    function_types=frozenset(
        {
            "function_declaration",
            "generator_function_declaration",
            "method_definition",
            "method_signature",
        }
    ),
    import_types=frozenset({"import_statement", "export_statement"}),
    call_types=frozenset({"call_expression", "new_expression"}),
    call_function_field="function",
    call_accessor_node_types=frozenset({"member_expression"}),
    call_accessor_field="property",
    call_accessor_object_field="object",
    function_boundary_types=frozenset(
        {
            "function_declaration",
            "generator_function_declaration",
            "arrow_function",
            "method_definition",
        }
    ),
    import_handler=_import_js,
)

_TSX_CONFIG = LanguageConfig(
    ts_module="tree_sitter_typescript",
    ts_language_fn="language_tsx",
    class_types=_TS_CONFIG.class_types,
    function_types=_TS_CONFIG.function_types,
    import_types=_TS_CONFIG.import_types,
    call_types=_TS_CONFIG.call_types,
    call_function_field=_TS_CONFIG.call_function_field,
    call_accessor_node_types=_TS_CONFIG.call_accessor_node_types,
    call_accessor_field=_TS_CONFIG.call_accessor_field,
    call_accessor_object_field=_TS_CONFIG.call_accessor_object_field,
    function_boundary_types=_TS_CONFIG.function_boundary_types,
    import_handler=_TS_CONFIG.import_handler,
)

_JAVA_CONFIG = LanguageConfig(
    ts_module="tree_sitter_java",
    class_types=frozenset(
        {
            "class_declaration",
            "interface_declaration",
            "record_declaration",
            "enum_declaration",
            "annotation_type_declaration",
        }
    ),
    function_types=frozenset({"method_declaration", "constructor_declaration"}),
    import_types=frozenset({"import_declaration"}),
    call_types=frozenset({"method_invocation", "object_creation_expression"}),
    call_function_field="name",
    call_accessor_node_types=frozenset(),
    function_boundary_types=frozenset({"method_declaration", "constructor_declaration"}),
    import_handler=_import_java,
)

_GROOVY_CONFIG = LanguageConfig(
    ts_module="tree_sitter_groovy",
    class_types=frozenset({"class_declaration", "interface_declaration"}),
    function_types=frozenset({"method_declaration", "constructor_declaration"}),
    import_types=frozenset({"import_declaration"}),
    call_types=frozenset({"method_invocation"}),
    call_function_field="name",
    call_accessor_node_types=frozenset(),
    function_boundary_types=frozenset({"method_declaration", "constructor_declaration"}),
    import_handler=_import_java,
)

_C_CONFIG = LanguageConfig(
    ts_module="tree_sitter_c",
    class_types=frozenset(),
    function_types=frozenset({"function_definition"}),
    import_types=frozenset({"preproc_include"}),
    call_types=frozenset({"call_expression"}),
    call_function_field="function",
    call_accessor_node_types=frozenset({"field_expression"}),
    call_accessor_field="field",
    function_boundary_types=frozenset({"function_definition"}),
    import_handler=_import_c,
    resolve_function_name_fn=_get_c_func_name,
)

_CPP_CONFIG = LanguageConfig(
    ts_module="tree_sitter_cpp",
    class_types=frozenset({"class_specifier", "struct_specifier"}),
    function_types=frozenset({"function_definition"}),
    import_types=frozenset({"preproc_include"}),
    call_types=frozenset({"call_expression"}),
    call_function_field="function",
    call_accessor_node_types=frozenset({"field_expression", "qualified_identifier"}),
    call_accessor_field="field",
    function_boundary_types=frozenset({"function_definition"}),
    import_handler=_import_c,
    resolve_function_name_fn=_get_cpp_func_name,
)

_RUBY_CONFIG = LanguageConfig(
    ts_module="tree_sitter_ruby",
    class_types=frozenset({"class", "module"}),
    function_types=frozenset({"method", "singleton_method"}),
    import_types=frozenset(),
    call_types=frozenset({"call"}),
    call_function_field="method",
    call_accessor_node_types=frozenset(),
    name_fallback_child_types=("constant", "scope_resolution", "identifier"),
    body_fallback_child_types=("body_statement",),
    function_boundary_types=frozenset({"method", "singleton_method"}),
)

_CSHARP_CONFIG = LanguageConfig(
    ts_module="tree_sitter_c_sharp",
    class_types=frozenset(
        {
            "class_declaration",
            "interface_declaration",
            "enum_declaration",
            "struct_declaration",
            "record_declaration",
        }
    ),
    function_types=frozenset({"method_declaration"}),
    import_types=frozenset({"using_directive"}),
    call_types=frozenset({"invocation_expression"}),
    call_function_field="function",
    call_accessor_node_types=frozenset({"member_access_expression"}),
    call_accessor_field="name",
    body_fallback_child_types=("declaration_list",),
    function_boundary_types=frozenset({"method_declaration"}),
    import_handler=_import_csharp,
)

_KOTLIN_CONFIG = LanguageConfig(
    ts_module="tree_sitter_kotlin",
    class_types=frozenset({"class_declaration", "object_declaration"}),
    function_types=frozenset({"function_declaration"}),
    import_types=frozenset({"import_header"}),
    call_types=frozenset({"call_expression"}),
    call_function_field="",
    call_accessor_node_types=frozenset({"navigation_expression"}),
    call_accessor_field="",
    name_fallback_child_types=("simple_identifier", "identifier"),
    body_fallback_child_types=("function_body", "class_body", "enum_class_body"),
    function_boundary_types=frozenset({"function_declaration"}),
    import_handler=_import_kotlin,
)

_SCALA_CONFIG = LanguageConfig(
    ts_module="tree_sitter_scala",
    class_types=frozenset({"class_definition", "object_definition"}),
    function_types=frozenset({"function_definition"}),
    import_types=frozenset({"import_declaration"}),
    call_types=frozenset({"call_expression"}),
    call_function_field="",
    call_accessor_node_types=frozenset({"field_expression"}),
    call_accessor_field="field",
    name_fallback_child_types=("identifier",),
    body_fallback_child_types=("template_body",),
    function_boundary_types=frozenset({"function_definition"}),
    import_handler=_import_scala,
)

_PHP_CONFIG = LanguageConfig(
    ts_module="tree_sitter_php",
    ts_language_fn="language_php",
    class_types=frozenset({"class_declaration"}),
    function_types=frozenset({"function_definition", "method_declaration"}),
    import_types=frozenset({"namespace_use_clause"}),
    call_types=frozenset(
        {
            "function_call_expression",
            "member_call_expression",
            "scoped_call_expression",
            "class_constant_access_expression",
        }
    ),
    static_prop_types=frozenset({"scoped_property_access_expression"}),
    helper_fn_names=frozenset({"config"}),
    container_bind_methods=frozenset({"bind", "singleton", "scoped", "instance"}),
    event_listener_properties=frozenset({"listen", "subscribe"}),
    call_function_field="function",
    call_accessor_node_types=frozenset({"member_call_expression"}),
    call_accessor_field="name",
    name_fallback_child_types=("name",),
    body_fallback_child_types=("declaration_list", "compound_statement"),
    function_boundary_types=frozenset({"function_definition", "method_declaration"}),
    import_handler=_import_php,
)


def _import_lua(
    node,
    source: bytes,
    file_nid: str,
    stem: str,
    edges: list,
    str_path: str,
    scope_stack: list[str] | None = None,
) -> None:
    """Extract require('module') from Lua variable_declaration nodes."""
    text = _read_text(node, source)
    import re

    m = re.search(r"""require\s*[\('"]\s*['"]?([^'")\s]+)""", text)
    if m:
        raw_module = m.group(1)
        if raw_module:
            tgt_nid = _resolve_lua_import_target(raw_module, str_path)
            if tgt_nid:
                edges.append(
                    {
                        "source": file_nid,
                        "target": tgt_nid,
                        "relation": "imports",
                        "context": "import",
                        "confidence": "EXTRACTED",
                        "confidence_score": 1.0,
                        "source_file": str_path,
                        "source_location": str(node.start_point[0] + 1),
                        "weight": 1.0,
                    }
                )


_LUA_CONFIG = LanguageConfig(
    ts_module="tree_sitter_lua",
    ts_language_fn="language",
    class_types=frozenset(),
    function_types=frozenset({"function_declaration"}),
    import_types=frozenset({"variable_declaration"}),
    call_types=frozenset({"function_call"}),
    call_function_field="name",
    call_accessor_node_types=frozenset({"method_index_expression"}),
    call_accessor_field="name",
    name_fallback_child_types=("identifier", "method_index_expression"),
    body_fallback_child_types=("block",),
    function_boundary_types=frozenset({"function_declaration"}),
    import_handler=_import_lua,
)


def _import_swift(
    node,
    source: bytes,
    file_nid: str,
    stem: str,
    edges: list,
    str_path: str,
    scope_stack: list[str] | None = None,
) -> list[tuple[str, str]]:
    """Emit module-level ``imports`` edges and report the imported modules.

    A Swift ``import CoreKit`` names a module, not a file path, so — unlike the
    file-resolving JS/TS handlers — there is no existing node for the edge to
    point at. The returned ``(id, label)`` pairs let the extractor materialize a
    ``type=module`` anchor node so the edge survives; without it ``build_from_json``
    prunes every Swift import edge as a dangling/external reference (#1327).
    """
    modules: list[tuple[str, str]] = []
    for child in node.children:
        if child.type == "identifier":
            raw = _read_text(child, source)
            tgt_nid = _make_id(raw)
            edges.append(
                {
                    "source": file_nid,
                    "target": tgt_nid,
                    "relation": "imports",
                    "context": "import",
                    "confidence": "EXTRACTED",
                    "source_file": str_path,
                    "source_location": f"L{node.start_point[0] + 1}",
                    "weight": 1.0,
                }
            )
            modules.append((tgt_nid, raw))
            break
    return modules


_SWIFT_CONFIG = LanguageConfig(
    ts_module="tree_sitter_swift",
    class_types=frozenset({"class_declaration", "protocol_declaration"}),
    function_types=frozenset(
        {"function_declaration", "init_declaration", "deinit_declaration", "subscript_declaration"}
    ),
    import_types=frozenset({"import_declaration"}),
    call_types=frozenset({"call_expression"}),
    call_function_field="",
    call_accessor_node_types=frozenset({"navigation_expression"}),
    call_accessor_field="",
    name_fallback_child_types=("simple_identifier", "type_identifier", "user_type"),
    body_fallback_child_types=("class_body", "protocol_body", "function_body", "enum_class_body"),
    function_boundary_types=frozenset(
        {"function_declaration", "init_declaration", "deinit_declaration", "subscript_declaration"}
    ),
    import_handler=_import_swift,
)


_RATIONALE_PREFIXES = (
    "# NOTE:",
    "# IMPORTANT:",
    "# HACK:",
    "# WHY:",
    "# RATIONALE:",
    "# TODO:",
    "# FIXME:",
)


def _is_autogenerated_python(source: bytes) -> bool:
    """Return True if this Python file is auto-generated and its module docstring is noise.

    Covers: Alembic/Flask-Migrate revisions, Django migrations, protobuf/gRPC/OpenAPI stubs.
    Module docstrings in these files are change annotations or boilerplate, not rationale.
    """
    head = source[:2048].decode("utf-8", errors="replace")
    if any(m in head for m in ("DO NOT EDIT", "@generated", "Generated by the protocol buffer")):
        return True
    if (
        re.search(r"^revision\s*[:=]", head, re.MULTILINE)
        and "def upgrade(" in head
        and "down_revision" in head
    ):
        return True
    if "class Migration(migrations.Migration)" in head and "operations" in head:
        return True
    return False


def _extract_python_rationale(path: Path, result: dict) -> None:
    """Post-pass: extract docstrings and rationale comments from Python source.
    Mutates result in-place by appending to result['nodes'] and result['edges'].
    """
    try:
        import tree_sitter_python as tspython
        from tree_sitter import Language, Parser

        language = Language(tspython.language())
        parser = Parser(language)
        source = path.read_bytes()
        tree = parser.parse(source)
        root = tree.root_node
    except Exception:
        return

    stem = _file_stem(path)
    str_path = str(path)
    nodes = result["nodes"]
    edges = result["edges"]
    seen_ids = {n["id"] for n in nodes}
    file_nid = _make_id(str(path))

    def _get_docstring(body_node) -> tuple[str, int] | None:
        if not body_node:
            return None
        for child in body_node.children:
            if child.type == "expression_statement":
                for sub in child.children:
                    if sub.type in ("string", "concatenated_string"):
                        text = source[sub.start_byte : sub.end_byte].decode(
                            "utf-8", errors="replace"
                        )
                        text = text.strip("\"'").strip('"""').strip("'''").strip()
                        if len(text) > 20:
                            return text, child.start_point[0] + 1
            break
        return None

    def _add_rationale(text: str, line: int, parent_nid: str) -> None:
        label = text[:80].replace("\r\n", " ").replace("\r", " ").replace("\n", " ").strip()
        rid = _make_id(stem, "rationale", str(line))
        if rid not in seen_ids:
            seen_ids.add(rid)
            nodes.append(
                {
                    "id": rid,
                    "label": label,
                    "file_type": "rationale",
                    "source_file": str_path,
                    "source_location": f"L{line}",
                }
            )
        edges.append(
            {
                "source": rid,
                "target": parent_nid,
                "relation": "rationale_for",
                "confidence": "EXTRACTED",
                "source_file": str_path,
                "source_location": f"L{line}",
                "weight": 1.0,
            }
        )

    if not _is_autogenerated_python(source):
        ds = _get_docstring(root)
        if ds:
            _add_rationale(ds[0], ds[1], file_nid)

    def walk_docstrings(node, parent_nid: str) -> None:
        t = node.type
        if t == "class_definition":
            name_node = node.child_by_field_name("name")
            body = node.child_by_field_name("body")
            if name_node and body:
                class_name = source[name_node.start_byte : name_node.end_byte].decode(
                    "utf-8", errors="replace"
                )
                nid = _make_id(stem, class_name)
                ds = _get_docstring(body)
                if ds:
                    _add_rationale(ds[0], ds[1], nid)
                for child in body.children:
                    walk_docstrings(child, nid)
            return
        if t == "function_definition":
            name_node = node.child_by_field_name("name")
            body = node.child_by_field_name("body")
            if name_node and body:
                func_name = source[name_node.start_byte : name_node.end_byte].decode(
                    "utf-8", errors="replace"
                )
                nid = (
                    _make_id(parent_nid, func_name)
                    if parent_nid != file_nid
                    else _make_id(stem, func_name)
                )
                ds = _get_docstring(body)
                if ds:
                    _add_rationale(ds[0], ds[1], nid)
            return
        for child in node.children:
            walk_docstrings(child, parent_nid)

    walk_docstrings(root, file_nid)

    source_text = source.decode("utf-8", errors="replace")
    for lineno, line_text in enumerate(source_text.splitlines(), start=1):
        stripped = line_text.strip()
        if any(stripped.startswith(p) for p in _RATIONALE_PREFIXES):
            _add_rationale(stripped, lineno, file_nid)


def extract_python(path: Path) -> dict:
    """Extract classes, functions, and imports from a .py file via tree-sitter AST."""
    result = _extract_generic(path, _PYTHON_CONFIG)
    if "error" not in result:
        _extract_python_rationale(path, result)
    return result


def extract_js(path: Path) -> dict:
    """Extract classes, functions, arrow functions, and imports from a .js/.ts/.tsx/.mts/.cts file."""
    suffix = path.suffix.lower()
    if suffix == ".tsx":
        config = _TSX_CONFIG
    elif suffix in (".ts", ".mts", ".cts"):
        config = _TS_CONFIG
    else:
        config = _JS_CONFIG
    result = _extract_generic(path, config)
    if "error" not in result:
        _extract_js_rationale(path, result)
    return result


_JS_RATIONALE_PREFIXES = (
    "// NOTE:",
    "// IMPORTANT:",
    "// HACK:",
    "// WHY:",
    "// RATIONALE:",
    "// TODO:",
    "// FIXME:",
    "* NOTE:",
    "* IMPORTANT:",
    "* HACK:",
    "* WHY:",
    "* RATIONALE:",
    "* TODO:",
    "* FIXME:",
)

_JS_DOC_REF_RE = re.compile(r"\b(ADR[- ]?\d{1,5}|RFC[- ]?\d{1,5})\b", re.IGNORECASE)

_JS_COMMENT_LINE_RE = re.compile(r"^\s*(//|/\*|\*)")


def _extract_js_rationale(path: Path, result: dict) -> None:
    """Post-pass: extract rationale comments and doc references from JS/TS source.
    Mutates result in-place by appending to result['nodes'] and result['edges'].
    """
    try:
        source_text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return

    stem = _file_stem(path)
    str_path = str(path)
    nodes = result["nodes"]
    edges = result["edges"]
    seen_ids = {n["id"] for n in nodes}
    file_nid = _make_id(str(path))
    seen_doc_refs: set[str] = set()

    def _add_rationale(text: str, line: int) -> None:
        label = text[:80].replace("\r\n", " ").replace("\r", " ").replace("\n", " ").strip()
        rid = _make_id(stem, "rationale", str(line))
        if rid not in seen_ids:
            seen_ids.add(rid)
            nodes.append(
                {
                    "id": rid,
                    "label": label,
                    "file_type": "rationale",
                    "source_file": str_path,
                    "source_location": f"L{line}",
                }
            )
        edges.append(
            {
                "source": rid,
                "target": file_nid,
                "relation": "rationale_for",
                "confidence": "EXTRACTED",
                "source_file": str_path,
                "source_location": f"L{line}",
                "weight": 1.0,
            }
        )

    def _add_doc_ref(token: str, line: int) -> None:
        kind, num = re.match(r"([A-Za-z]+)[- ]?(\d+)", token).groups()
        kind = kind.upper()
        label = f"{kind}-{num.zfill(4)}" if kind == "ADR" else f"{kind}-{num}"
        if label in seen_doc_refs:
            return
        seen_doc_refs.add(label)
        rid = _make_id("docref", label)
        if rid not in seen_ids:
            seen_ids.add(rid)
            nodes.append(
                {
                    "id": rid,
                    "label": label,
                    "file_type": "doc_ref",
                    "source_file": str_path,
                    "source_location": f"L{line}",
                }
            )
        edges.append(
            {
                "source": file_nid,
                "target": rid,
                "relation": "cites",
                "confidence": "EXTRACTED",
                "source_file": str_path,
                "source_location": f"L{line}",
                "weight": 1.0,
            }
        )

    for lineno, line_text in enumerate(source_text.splitlines(), start=1):
        stripped = line_text.strip()
        if any(stripped.startswith(p) for p in _JS_RATIONALE_PREFIXES):
            _add_rationale(stripped.lstrip("/* "), lineno)
        if _JS_COMMENT_LINE_RE.match(line_text):
            for m in _JS_DOC_REF_RE.finditer(stripped):
                _add_doc_ref(m.group(1), lineno)


def _emit_rescued_import(
    result: dict,
    existing_ids: set,
    file_node_id: str,
    path: Path,
    raw: str,
    relation: str,
    aliases,
    base_url,
) -> None:
    """Shared edge/stub emit for the Svelte/Astro/Vue regex-rescue import passes.

    Resolves the specifier the same way ``_import_js`` does — relative paths and
    tsconfig aliases both go through :func:`_resolve_js_module_path` so
    extensionless specifiers probe real on-disk extensions (``../lib/content``
    -> ``content.ts``) instead of a naive ``.js``->``.ts`` suffix swap.

    When the resolved target is a real file on disk, mirror ``_import_js``:
    emit ONLY the edge, stamped with ``target_file``, and mint no stub node.
    The #2169 canonicalization loop in :func:`extract` reads the stamp and
    repoints the edge at the real file node's canonical id. Minting a stub
    here would carry an absolute-path-derived id when the input path is
    absolute — a ghost node (e.g. ``private_tmp_..._src_lib_content``)
    duplicating the real ``src_lib_content`` node and clobbering its label on
    dedupe (#2195). Stub nodes are still minted for unresolved specifiers
    (externals, not-yet-created files) so prior behavior is preserved.
    """
    resolved_file: "Path | None" = None
    if raw.startswith("."):
        resolved = _resolve_js_module_path(Path(os.path.normpath(path.parent / raw)))
        node_id = _make_id(str(resolved))
        stub_source_file = str(resolved)
        if resolved is not None and resolved.is_file():
            resolved_file = resolved
    else:
        resolved_alias = _resolve_tsconfig_alias(raw, aliases, base_url=base_url)
        if resolved_alias is not None:
            resolved_alias = _resolve_js_module_path(resolved_alias)
            node_id = _make_id(str(resolved_alias))
            stub_source_file = str(resolved_alias)
            if resolved_alias is not None and resolved_alias.is_file():
                resolved_file = resolved_alias
        else:
            module_name = raw.split("/")[-1]
            if not module_name:
                return
            node_id = _make_id(module_name)
            stub_source_file = raw
    edge = {
        "source": file_node_id,
        "target": node_id,
        "relation": relation,
        "confidence": "EXTRACTED",
        "source_file": str(path),
    }
    if resolved_file is not None:
        edge["target_file"] = str(resolved_file)
        result.setdefault("edges", []).append(edge)
        return
    if node_id in existing_ids:
        result.setdefault("edges", []).append(edge)
        return
    result.setdefault("nodes", []).append(
        {
            "id": node_id,
            "label": raw,
            "file_type": "code",
            "source_file": stub_source_file,
            "confidence": "EXTRACTED",
        }
    )
    result.setdefault("edges", []).append(edge)
    existing_ids.add(node_id)


def extract_svelte(path: Path) -> dict:
    """Extract imports from .svelte files: script-block via JS AST + template regex fallback.

    Tree-sitter only sees the <script> block. Svelte template syntax like
    {#await import('./X.svelte')} lives in the markup layer and is invisible
    to the JS parser, so a regex pass covers those dynamic imports.
    """
    result = _extract_generic(path, _JS_CONFIG)
    try:
        import re as _re

        src = path.read_text(encoding="utf-8", errors="replace")
        existing_ids = {n["id"] for n in result.get("nodes", [])}
        file_node_id = _make_id(str(path))
        aliases = _load_tsconfig_aliases(path.parent)
        base_url = _load_tsconfig_base_url(path.parent)
        for m in _re.finditer(r"""import\(\s*['"]([^'"]+)['"]\s*\)""", src):
            raw = m.group(1)
            if not raw:
                continue
            _emit_rescued_import(
                result,
                existing_ids,
                file_node_id,
                path,
                raw,
                "dynamic_import",
                aliases,
                base_url,
            )
        script_re = _re.compile(r"<script\b[^>]*>([\s\S]*?)</script\s*>", _re.IGNORECASE)
        static_import_re = _re.compile(r"""import\s+(?:[^'"`;]+?\s+from\s+)?['"]([^'"]+)['"]""")
        for script_match in script_re.finditer(src):
            script_body = script_match.group(1)
            for m in static_import_re.finditer(script_body):
                raw = m.group(1)
                if not raw:
                    continue
                _emit_rescued_import(
                    result,
                    existing_ids,
                    file_node_id,
                    path,
                    raw,
                    "imports_from",
                    aliases,
                    base_url,
                )
    except Exception:
        pass
    return result


def extract_astro(path: Path) -> dict:
    """Extract imports from .astro files: frontmatter (TS) + template regex fallback.

    Astro files start with a ``---\\n...\\n---`` frontmatter block of TypeScript
    setup code (where almost all imports live), followed by an HTML-with-expressions
    template body, and optionally ``<script>`` blocks for client-side JS. Tree-sitter
    only sees the file usefully through the frontmatter — feeding the whole file to
    the JS parser produces a top-level ERROR node because the template is not valid
    JS, so ``import_statement`` nodes are never reached and static imports are
    silently dropped (#850). Mirrors :func:`extract_svelte` — same regex-rescue
    approach, scanning the frontmatter block and any client-side ``<script>`` blocks
    for static and dynamic imports.
    """
    result = _extract_generic(path, _JS_CONFIG)
    try:
        import re as _re

        src = path.read_text(encoding="utf-8", errors="replace")
        existing_ids = {n["id"] for n in result.get("nodes", [])}
        file_node_id = _make_id(str(path))
        aliases = _load_tsconfig_aliases(path.parent)
        base_url = _load_tsconfig_base_url(path.parent)
        for m in _re.finditer(r"""import\(\s*['"]([^'"]+)['"]\s*\)""", src):
            raw = m.group(1)
            if not raw:
                continue
            _emit_rescued_import(
                result,
                existing_ids,
                file_node_id,
                path,
                raw,
                "dynamic_import",
                aliases,
                base_url,
            )
        frontmatter_re = _re.compile(r"\A\s*---\s*\r?\n([\s\S]*?)\r?\n---\s*(?:\r?\n|\Z)")
        script_re = _re.compile(r"<script\b[^>]*>([\s\S]*?)</script\s*>", _re.IGNORECASE)
        static_import_re = _re.compile(r"""import\s+(?:[^'"`;]+?\s+from\s+)?['"]([^'"]+)['"]""")
        regions: list[str] = []
        fm = frontmatter_re.search(src)
        if fm:
            regions.append(fm.group(1))
        for script_match in script_re.finditer(src):
            regions.append(script_match.group(1))
        for region in regions:
            for m in static_import_re.finditer(region):
                raw = m.group(1)
                if not raw:
                    continue
                _emit_rescued_import(
                    result,
                    existing_ids,
                    file_node_id,
                    path,
                    raw,
                    "imports_from",
                    aliases,
                    base_url,
                )
    except Exception:
        pass
    return result


def extract_vue(path: Path) -> dict:
    """Extract imports, symbols, and type refs from a ``.vue`` SFC.

    Masks the non-``<script>`` regions and parses the script with the grammar
    its ``lang`` implies (``tsx``→TSX, ``js``/``jsx``→JS, ``ts`` or unset→TS;
    TS is a superset of JS so it is a safe default). A regex pass then recovers
    ``import('…')`` dynamic imports the AST does not edge.
    """
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"nodes": [], "edges": []}

    masked, lang = _vue_mask_non_script(src)
    if lang == "tsx":
        config = _TSX_CONFIG
    elif lang in ("js", "jsx"):
        config = _JS_CONFIG
    else:
        config = _TS_CONFIG

    result = _extract_generic(path, config, source_override=masked.encode("utf-8"))

    try:
        existing_ids = {n["id"] for n in result.get("nodes", [])}
        file_node_id = _make_id(str(path))
        aliases = _load_tsconfig_aliases(path.parent)
        base_url = _load_tsconfig_base_url(path.parent)
        for m in re.finditer(r"""import\(\s*['"]([^'"]+)['"]\s*\)""", src):
            raw = m.group(1)
            if not raw:
                continue
            _emit_rescued_import(
                result,
                existing_ids,
                file_node_id,
                path,
                raw,
                "dynamic_import",
                aliases,
                base_url,
            )
    except Exception:
        pass
    return result


def extract_java(path: Path) -> dict:
    """Extract classes, interfaces, methods, constructors, and imports from a .java file."""
    return _extract_generic(path, _JAVA_CONFIG)


def _is_spock_file(path: Path, ts_result: dict) -> bool:
    """Return True when the file contains Spock-style ``def "feature"()`` methods
    that tree-sitter-groovy cannot parse, detected by checking the raw source."""
    import re as _re

    _SPOCK_FEATURE_RE = _re.compile(r"""^\s*def\s+[\"']""", _re.MULTILINE)
    try:
        return bool(_SPOCK_FEATURE_RE.search(path.read_text(errors="replace")))
    except OSError:
        return False


def _extract_spock_fallback(path: Path, ts_result: dict) -> dict:
    """Regex-based fallback for Spock spec files where tree-sitter-groovy cannot parse
    ``def "feature name"()`` methods. Merges import edges from the tree-sitter pass
    (which survive reliably) with class and feature-method nodes extracted via regex.
    """
    import re as _re

    source = path.read_text(errors="replace")
    str_path = str(path)
    stem = _file_stem(path)

    file_node = next((n for n in ts_result.get("nodes", []) if n.get("label") == path.name), None)
    nodes: list[dict] = [file_node] if file_node else []
    edges: list[dict] = [e for e in ts_result.get("edges", []) if e.get("context") == "import"]
    seen_ids: set[str] = {n["id"] for n in nodes}

    def _add_node(nid: str, label: str, line: int) -> None:
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

    def _add_edge(
        src: str, tgt: str, relation: str, line: int, confidence: str = "EXTRACTED"
    ) -> None:
        edges.append(
            {
                "source": src,
                "target": tgt,
                "relation": relation,
                "confidence": confidence,
                "source_file": str_path,
                "source_location": f"L{line}",
                "weight": 1.0,
            }
        )

    lines_text = source.splitlines()

    class_re = _re.compile(r"^\s*(?:[\w@]+\s+)*class\s+(\w+)")
    feature_re = _re.compile(r"""^\s*def\s+(?:\"([^\"]+)\"|'([^']+)')\s*\(""")
    plain_method_re = _re.compile(r"""^\s*def\s+(\w+)\s*\(""")

    current_class_nid: str | None = None
    file_nid = _make_id(str_path)

    if file_nid not in seen_ids:
        _add_node(file_nid, path.name, 1)

    for lineno, line_text in enumerate(lines_text, start=1):
        cm = class_re.match(line_text)
        if cm:
            class_name = cm.group(1)
            class_nid = _make_id(stem, class_name)
            _add_node(class_nid, class_name, lineno)
            _add_edge(file_nid, class_nid, "contains", lineno)
            current_class_nid = class_nid
            continue

        if current_class_nid is None:
            continue

        fm = feature_re.match(line_text)
        if fm:
            method_name = fm.group(1) or fm.group(2)
            method_label = f'"{method_name}"'
            method_nid = _make_id(current_class_nid, method_name)
            _add_node(method_nid, method_label, lineno)
            _add_edge(current_class_nid, method_nid, "method", lineno)
            continue

        pm = plain_method_re.match(line_text)
        if pm:
            method_name = pm.group(1)
            if method_name not in ("if", "while", "for", "switch", "catch"):
                method_label = f".{method_name}()"
                method_nid = _make_id(current_class_nid, method_name)
                _add_node(method_nid, method_label, lineno)
                _add_edge(current_class_nid, method_nid, "method", lineno)

    return {"nodes": nodes, "edges": edges}


def extract_groovy(path: Path) -> dict:
    """Extract classes, methods, constructors, and imports from a .groovy/.gradle file.

    Falls back to a regex-based Spock extractor when tree-sitter-groovy cannot parse
    ``def "feature name"()`` methods (common in Spock specification classes).
    """
    result = _extract_generic(path, _GROOVY_CONFIG)
    if _is_spock_file(path, result):
        result = _extract_spock_fallback(path, result)
    return result


def extract_c(path: Path) -> dict:
    """Extract functions and includes from a .c/.h file."""
    return _extract_generic(path, _C_CONFIG)


def extract_cpp(path: Path) -> dict:
    """Extract functions, classes, and includes from a .cpp/.cc/.cxx/.hpp file."""
    return _extract_generic(path, _CPP_CONFIG)


def extract_ruby(path: Path) -> dict:
    """Extract classes, methods, singleton methods, and calls from a .rb file."""
    return _extract_generic(path, _RUBY_CONFIG)


def extract_csharp(path: Path) -> dict:
    """Extract C# type declarations, methods, namespaces, and usings from a .cs file."""
    return _extract_generic(path, _CSHARP_CONFIG)


def extract_kotlin(path: Path) -> dict:
    """Extract classes, objects, functions, and imports from a .kt/.kts file."""
    return _extract_generic(path, _KOTLIN_CONFIG)


def extract_scala(path: Path) -> dict:
    """Extract classes, objects, functions, and imports from a .scala file."""
    return _extract_generic(path, _SCALA_CONFIG)


def extract_php(path: Path) -> dict:
    """Extract classes, functions, methods, namespace uses, and calls from a .php file."""
    return _extract_generic(path, _PHP_CONFIG)


def extract_lua(path: Path) -> dict:
    """Extract functions, methods, require() imports, and calls from a .lua file."""
    return _extract_generic(path, _LUA_CONFIG)


def extract_swift(path: Path) -> dict:
    """Extract classes, structs, protocols, functions, imports, and calls from a .swift file."""
    return _extract_generic(path, _SWIFT_CONFIG)


def _canonicalize_csharp_namespace_nodes(all_nodes: list[dict], all_edges: list[dict]) -> None:
    """Collapse duplicate C# namespace node entries to one canonical node per label."""
    by_label: dict[str, list[dict]] = {}
    for node in all_nodes:
        if node.get("type") != "namespace":
            continue
        label = node.get("label")
        if isinstance(label, str):
            by_label.setdefault(label, []).append(node)

    remap: dict[str, str] = {}
    drop_node_ids: set[int] = set()
    for group in by_label.values():
        if len(group) < 2:
            continue
        canonical = sorted(
            group,
            key=lambda node: (
                str(node.get("source_file") or ""),
                str(node.get("source_location") or ""),
                str(node.get("id") or ""),
            ),
        )[0]
        canonical_id = canonical.get("id")
        for node in group:
            if node is canonical:
                continue
            drop_node_ids.add(id(node))
            dup_id = node.get("id")
            if isinstance(dup_id, str) and isinstance(canonical_id, str):
                remap[dup_id] = canonical_id

    if remap:
        for edge in all_edges:
            if edge.get("source") in remap:
                edge["source"] = remap[str(edge["source"])]
            if edge.get("target") in remap:
                edge["target"] = remap[str(edge["target"])]

    if drop_node_ids:
        all_nodes[:] = [node for node in all_nodes if id(node) not in drop_node_ids]


_CASE_INSENSITIVE_EXTS = frozenset(
    {
        ".php",
        ".phtml",
        ".php3",
        ".php4",
        ".php5",
        ".php7",
        ".phps",
        ".sql",
        ".nim",
        ".nims",
        ".nimble",
    }
)


def _lang_is_case_insensitive(source_file: object) -> bool:
    """True when the file's language resolves identifiers case-insensitively (#1581)."""
    if not source_file:
        return False
    return Path(str(source_file)).suffix.lower() in _CASE_INSENSITIVE_EXTS


_LANG_FAMILY_BY_EXT: dict[str, str] = {
    ".js": "jsts",
    ".jsx": "jsts",
    ".mjs": "jsts",
    ".cjs": "jsts",
    ".ts": "jsts",
    ".tsx": "jsts",
    ".mts": "jsts",
    ".cts": "jsts",
    ".vue": "jsts",
    ".svelte": "jsts",
    ".astro": "jsts",
    ".java": "jvm",
    ".kt": "jvm",
    ".kts": "jvm",
    ".scala": "jvm",
    ".groovy": "jvm",
    ".gradle": "jvm",
    ".c": "native",
    ".h": "native",
    ".cpp": "native",
    ".cc": "native",
    ".cxx": "native",
    ".hpp": "native",
    ".cu": "native",
    ".cuh": "native",
    ".metal": "native",
    ".m": "native",
    ".mm": "native",
    ".swift": "native",
    ".py": "python",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".rake": "ruby",
    ".php": "php",
    ".phtml": "php",
    ".php3": "php",
    ".php4": "php",
    ".php5": "php",
    ".php7": "php",
    ".phps": "php",
    ".cs": "dotnet",
    ".razor": "dotnet",
    ".cshtml": "dotnet",
    ".xaml": "dotnet",
    ".lua": "lua",
    ".luau": "lua",
    ".zig": "zig",
    ".ex": "elixir",
    ".exs": "elixir",
    ".jl": "julia",
    ".dart": "dart",
    ".sh": "shell",
    ".bash": "shell",
    ".ps1": "powershell",
    ".psm1": "powershell",
    ".psd1": "powershell",
}


def _lang_family(source_file: object) -> str | None:
    """Interop family of the file's language, or None when unknown/not code."""
    if not source_file:
        return None
    return _LANG_FAMILY_BY_EXT.get(Path(str(source_file)).suffix.lower())


def _node_label_key(node: dict, fold: bool = False) -> str:
    label = str(node.get("label", "")).strip()
    key = re.sub(r"[^a-zA-Z0-9]+", "", label)
    return key.lower() if fold else key


def _is_top_level_function_definition(node: dict) -> bool:
    """A free/top-level function def (label ``name()``), not a method or type.

    Methods carry a leading dot (``.foo()``) or a qualifier (``Class.foo()``);
    excluding those keeps a bare-name reference from binding to a receiver-scoped
    method, which the receiver-typed resolvers own (#1781).
    """
    label = str(node.get("label", "")).strip()
    return (
        node.get("file_type") == "code"
        and label.endswith(")")
        and not label.startswith(".")
        and "." not in label
    )


def _rewire_unique_stub_nodes(nodes: list[dict], edges: list[dict]) -> None:
    """Map unresolved no-source stubs to a unique real definition with the same label."""
    real_by_label: dict[str, list[dict]] = {}
    real_by_label_ci: dict[str, list[dict]] = {}
    func_by_label: dict[str, list[dict]] = {}
    stubs: list[dict] = []

    for node in nodes:
        key = _node_label_key(node)
        if not key:
            continue
        if node.get("source_file"):
            if _is_type_like_definition(node):
                real_by_label.setdefault(key, []).append(node)
                if _lang_is_case_insensitive(node.get("source_file")):
                    real_by_label_ci.setdefault(_node_label_key(node, fold=True), []).append(node)
            elif _is_top_level_function_definition(node):
                func_by_label.setdefault(key, []).append(node)
            continue
        stubs.append(node)

    stub_ids = {str(s.get("id")) for s in stubs if s.get("id")}
    stub_families: dict[str, set] = {}
    supertype_stub_ids: set[str] = set()
    _SUPERTYPE_RELATIONS = {"inherits", "implements", "extends"}
    for edge in edges:
        rel = edge.get("relation")
        for endpoint in ("source", "target"):
            nid = edge.get(endpoint)
            if nid in stub_ids:
                fam = _lang_family(edge.get("source_file"))
                if fam is not None:
                    stub_families.setdefault(str(nid), set()).add(fam)
                if endpoint == "target" and rel in _SUPERTYPE_RELATIONS:
                    supertype_stub_ids.add(str(nid))

    remap: dict[str, str] = {}
    for stub in stubs:
        stub_id = str(stub.get("id", ""))
        if not stub_id:
            continue
        candidates = real_by_label.get(_node_label_key(stub), [])
        if len(candidates) != 1:
            candidates = real_by_label_ci.get(_node_label_key(stub, fold=True), [])
        if len(candidates) != 1:
            fcands = func_by_label.get(_node_label_key(stub), [])
            if len(fcands) == 1 and stub_id not in supertype_stub_ids:
                fams = stub_families.get(stub_id, set())
                cand_fam = _lang_family(fcands[0].get("source_file"))
                if not fams or cand_fam is None or cand_fam in fams:
                    candidates = fcands
        if len(candidates) != 1:
            continue
        target_id = candidates[0].get("id")
        if isinstance(target_id, str) and target_id and target_id != stub_id:
            remap[stub_id] = target_id

    if not remap:
        return

    by_id = {node.get("id"): node for node in nodes if node.get("id")}
    csharp_scoped_relations = {"inherits", "implements", "references", "imports"}
    for edge in edges:
        is_csharp_scoped_edge = (
            str(edge.get("source_file", "")).endswith(".cs")
            and edge.get("relation") in csharp_scoped_relations
        )
        source = edge.get("source")
        if source in remap:
            remapped_source = remap[str(source)]
            if not (
                is_csharp_scoped_edge
                and str(by_id.get(remapped_source, {}).get("source_file", "")).endswith(".cs")
            ):
                edge["source"] = remapped_source
        target = edge.get("target")
        if target in remap:
            remapped_target = remap[str(target)]
            if not (
                is_csharp_scoped_edge
                and str(by_id.get(remapped_target, {}).get("source_file", "")).endswith(".cs")
            ):
                edge["target"] = remapped_target

    referenced = {x for e in edges for x in (e.get("source"), e.get("target"))}
    drop_ids = {stub_id for stub_id in remap if stub_id not in referenced}
    nodes[:] = [node for node in nodes if node.get("id") not in drop_ids]


def _augment_js_reexport_edges(
    paths: list[Path],
    nodes: list[dict],
    edges: list[dict],
    root: Path,
) -> None:
    """Compatibility wrapper for the JS/TS symbol-resolution post-pass."""
    facts = _SymbolResolutionFacts()
    _collect_js_symbol_resolution_facts(paths, facts)
    _apply_symbol_resolution_facts(paths, nodes, edges, root, facts)


def _merge_swift_extensions(
    per_file: list[dict],
    all_nodes: list[dict],
    all_edges: list[dict],
) -> None:
    """Collapse cross-file Swift `extension Foo` nodes into the canonical `Foo`.

    tree-sitter-swift reuses `class_declaration` for both `class Foo` and
    `extension Foo`, and node ids carry the file stem, so each file that
    extends `Foo` produces its own `Foo` node. The match is done by label:
    when exactly one non-extension declaration shares the label, extension
    nodes redirect onto it. Extensions of types outside the corpus (no match)
    and ambiguous labels (more than one match) are left untouched — picking
    arbitrarily would invent edges.
    """
    extension_nids: set[str] = set()
    extension_labels: dict[str, str] = {}
    for result in per_file:
        for ext in result.get("swift_extensions", []) or []:
            extension_nids.add(ext["nid"])
            extension_labels[ext["nid"]] = ext["label"]

    if not extension_nids:
        return

    label_to_canonical: dict[str, list[str]] = {}
    for n in all_nodes:
        if n.get("id") in extension_nids:
            continue
        label = n.get("label")
        if not label:
            continue
        label_to_canonical.setdefault(label, []).append(n["id"])

    remap: dict[str, str] = {}
    for ext_nid in extension_nids:
        candidates = label_to_canonical.get(extension_labels[ext_nid], [])
        if len(candidates) != 1:
            continue
        canonical_nid = candidates[0]
        if canonical_nid != ext_nid:
            remap[ext_nid] = canonical_nid

    if not remap:
        return

    all_nodes[:] = [n for n in all_nodes if n.get("id") not in remap]

    rewritten: list[dict] = []
    seen_keys: set[tuple] = set()
    for e in all_edges:
        src = remap.get(e.get("source"), e.get("source"))
        tgt = remap.get(e.get("target"), e.get("target"))
        if src == tgt:
            continue
        e["source"] = src
        e["target"] = tgt
        key = (src, tgt, e.get("relation"), e.get("source_file"), e.get("source_location"))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        rewritten.append(e)
    all_edges[:] = rewritten


def _resolve_swift_member_calls(
    per_file: list[dict],
    all_nodes: list[dict],
    all_edges: list[dict],
) -> None:
    """Resolve cross-file Swift member calls (``recv.method()``) to the real
    definition of the receiver's type (#1356).

    The shared cross-file call pass drops every ``is_member_call`` because a bare
    method name (``update``) collides across the corpus and inflates god-nodes
    (#543/#1219). Swift extractors record the receiver of each member call and a
    per-file ``name -> type`` table (``swift_type_table``); this pass uses them to
    type the receiver, then emits an edge ONLY when that type name resolves to
    exactly one definition. A type-qualified call (``Type.staticMethod()``) is
    EXTRACTED (the type is named explicitly in source); an instance call typed via
    local inference (``obj.method()``) is INFERRED. The shared-pass member-call drop
    stays intact: this is purely additive and fires only on receiver-typed Swift calls.

    Must run after id-disambiguation so node ids and caller_nids are final.
    """
    type_table_by_file: dict[str, dict[str, str]] = {}
    for result in per_file:
        tt = result.get("swift_type_table")
        if tt and tt.get("path"):
            type_table_by_file[tt["path"]] = tt.get("table", {})
    if not type_table_by_file:
        return

    def _key(label: str) -> str:
        return re.sub(r"[^a-zA-Z0-9]+", "", str(label)).lower()

    contained = {e.get("target") for e in all_edges if e.get("relation") == "contains"}

    type_def_nids: dict[str, list[str]] = {}
    node_by_id: dict[str, dict] = {}
    for n in all_nodes:
        node_by_id[n.get("id")] = n
        if n.get("source_file") and n.get("id") in contained and _is_type_like_definition(n):
            type_def_nids.setdefault(_key(n.get("label", "")), []).append(n["id"])

    method_index: dict[tuple[str, str], str] = {}
    for e in all_edges:
        if e.get("relation") != "method":
            continue
        src, tgt = e.get("source"), e.get("target")
        tnode = node_by_id.get(tgt)
        if tnode is not None:
            method_index[(src, _key(tnode.get("label", "")))] = tgt

    all_raw_calls: list[dict] = []
    for result in per_file:
        all_raw_calls.extend(result.get("raw_calls", []))

    existing_pairs = {(e.get("source"), e.get("target")) for e in all_edges}
    for rc in all_raw_calls:
        if not rc.get("is_member_call"):
            continue
        receiver = rc.get("receiver")
        callee = rc.get("callee")
        if not receiver or not callee:
            continue
        if receiver[:1].isupper():
            type_name = receiver
            type_qualified = True
        else:
            type_name = type_table_by_file.get(rc.get("source_file", ""), {}).get(receiver)
            type_qualified = False
        if not type_name:
            continue
        if type_name in _LANGUAGE_BUILTIN_GLOBALS:
            continue
        type_defs = type_def_nids.get(_key(type_name), [])
        if len(type_defs) != 1:
            continue
        type_nid = type_defs[0]
        caller = rc.get("caller_nid")
        if not caller:
            continue
        method_nid = method_index.get((type_nid, _key(callee)))
        target = method_nid or type_nid
        relation = "calls" if method_nid else "references"
        if target == caller or (caller, target) in existing_pairs:
            continue
        existing_pairs.add((caller, target))
        all_edges.append(
            {
                "source": caller,
                "target": target,
                "relation": relation,
                "context": "call",
                "confidence": "EXTRACTED" if type_qualified else "INFERRED",
                "confidence_score": 1.0 if type_qualified else 0.8,
                "source_file": rc.get("source_file", ""),
                "source_location": rc.get("source_location"),
                "weight": 1.0,
            }
        )


def _resolve_python_member_calls(
    per_file: list[dict],
    all_nodes: list[dict],
    all_edges: list[dict],
) -> None:
    """Resolve cross-file Python qualified class-method calls (``ClassName.method()``)
    to the class-qualified method node (#1446).

    The shared cross-file call pass drops every ``is_member_call`` because a bare
    method name (``log``) collides across the corpus and inflates god-nodes
    (#543/#1219). That guard is right for *instance* calls (``obj.method()``) but
    misses *class-qualified* calls (``ClassName.method()``), where the receiver is
    an explicitly-named class — an exact, unambiguous reference. This pass uses the
    receiver captured by the extractor, and when it is a capitalized name resolving
    to exactly one class node that owns the called method, emits an EXTRACTED
    ``calls`` edge. Purely additive (only member calls the shared pass skipped),
    with a single-definition god-node guard.

    Must run after id-disambiguation so node ids and caller_nids are final.
    """

    def _key(label: str) -> str:
        return re.sub(r"[^a-zA-Z0-9]+", "", str(label)).lower()

    node_by_id: dict[str, dict] = {n.get("id"): n for n in all_nodes}

    class_def_nids: dict[str, list[str]] = {}
    method_index: dict[tuple[str, str], str] = {}
    for e in all_edges:
        if e.get("relation") != "method":
            continue
        src, tgt = e.get("source"), e.get("target")
        cnode = node_by_id.get(src)
        if cnode is not None:
            class_def_nids.setdefault(_key(cnode.get("label", "")), []).append(src)
        tnode = node_by_id.get(tgt)
        if tnode is not None:
            method_index[(src, _key(tnode.get("label", "")))] = tgt
    for k in list(class_def_nids):
        class_def_nids[k] = sorted(set(class_def_nids[k]))

    all_raw_calls: list[dict] = []
    for result in per_file:
        all_raw_calls.extend(result.get("raw_calls", []))

    contains_children: dict[str, dict[str, list[str]]] = {}
    file_of_node: dict[str, str] = {}
    for e in all_edges:
        if e.get("relation") == "contains":
            src, tgt = e.get("source"), e.get("target")
            tnode = node_by_id.get(tgt)
            if tnode is not None:
                contains_children.setdefault(src, {}).setdefault(
                    _key(tnode.get("label", "")), []
                ).append(tgt)
                file_of_node[tgt] = src
    imported_by_filenode: dict[str, set[str]] = {}
    import_alias_by_filenode: dict[str, dict[str, str]] = {}
    for e in all_edges:
        if e.get("relation") in ("imports", "imports_from"):
            imported_by_filenode.setdefault(e.get("source"), set()).add(e.get("target"))
            alias = e.get("local_alias")
            if alias:
                import_alias_by_filenode.setdefault(e.get("source"), {})[e.get("target")] = _key(
                    alias
                )

    def _module_stem_key(nid: str) -> str:
        n = node_by_id.get(nid)
        if not n:
            return ""
        sf = n.get("source_file") or ""
        stem = Path(sf).stem if sf else ""
        return _key(stem or n.get("label", ""))

    existing_pairs = {(e.get("source"), e.get("target")) for e in all_edges}

    def _emit_call(caller: str, target_nid: "str | None", rc: dict) -> None:
        if not target_nid or target_nid == caller or (caller, target_nid) in existing_pairs:
            return
        existing_pairs.add((caller, target_nid))
        all_edges.append(
            {
                "source": caller,
                "target": target_nid,
                "relation": "calls",
                "context": "call",
                "confidence": "EXTRACTED",
                "confidence_score": 1.0,
                "source_file": rc.get("source_file", ""),
                "source_location": rc.get("source_location"),
                "weight": 1.0,
            }
        )

    for rc in all_raw_calls:
        if not rc.get("is_member_call"):
            continue
        receiver = rc.get("receiver")
        callee = rc.get("callee")
        caller = rc.get("caller_nid")
        if not receiver or not callee or not caller:
            continue
        if receiver[:1].isupper():
            class_nids = class_def_nids.get(_key(receiver), [])
            if len(class_nids) != 1:
                continue
            _emit_call(caller, method_index.get((class_nids[0], _key(callee))), rc)
        else:
            rkey = _key(receiver)
            caller_file = file_of_node.get(caller)
            file_aliases = import_alias_by_filenode.get(caller_file, {})
            mods = [
                t
                for t in imported_by_filenode.get(caller_file, ())
                if t in contains_children
                and (_module_stem_key(t) == rkey or file_aliases.get(t) == rkey)
            ]
            if len(mods) != 1:
                continue
            children = contains_children[mods[0]].get(_key(callee), [])
            if len(children) != 1:
                continue
            _emit_call(caller, children[0], rc)


def _resolve_typescript_member_calls(
    per_file: list[dict],
    all_nodes: list[dict],
    all_edges: list[dict],
) -> None:
    """Resolve cross-file TS/JS member calls via constructor-injection type tables (#1316).

    ``this.repo.findById()`` drops out in the shared cross-file pass because bare
    ``findById`` collides across the corpus (god-node guard).  TS constructors with
    parameter-property modifiers (``private repo: IUserRepository``) produce a
    per-file type table mapping field names to their declared types.  This pass
    looks up the receiver field's type, finds a single-definition class/interface
    owning a method with the callee name, and emits an EXTRACTED ``calls`` edge.
    """
    type_table_by_file: dict[str, dict[str, str]] = {}
    for result in per_file:
        tt = result.get("ts_type_table")
        if tt and tt.get("path"):
            type_table_by_file[tt["path"]] = tt.get("table", {})
    if not type_table_by_file:
        return

    def _key(label: str) -> str:
        return re.sub(r"[^a-zA-Z0-9]+", "", str(label)).lower()

    contained = {e.get("target") for e in all_edges if e.get("relation") == "contains"}

    type_def_nids: dict[str, list[str]] = {}
    node_by_id: dict[str, dict] = {}
    for n in all_nodes:
        node_by_id[n.get("id")] = n
        if n.get("source_file") and n.get("id") in contained and _is_type_like_definition(n):
            type_def_nids.setdefault(_key(n.get("label", "")), []).append(n["id"])

    method_index: dict[tuple[str, str], str] = {}
    for e in all_edges:
        if e.get("relation") != "method":
            continue
        src, tgt = e.get("source"), e.get("target")
        tnode = node_by_id.get(tgt)
        if tnode is not None:
            method_index[(src, _key(tnode.get("label", "")))] = tgt

    all_raw_calls: list[dict] = []
    for result in per_file:
        all_raw_calls.extend(result.get("raw_calls", []))

    existing_pairs = {(e.get("source"), e.get("target")) for e in all_edges}
    for rc in all_raw_calls:
        if not rc.get("is_member_call"):
            continue
        receiver = rc.get("receiver")
        callee = rc.get("callee")
        caller = rc.get("caller_nid")
        if not receiver or not callee or not caller:
            continue
        if receiver[:1].isupper():
            type_name = receiver
        else:
            type_name = type_table_by_file.get(rc.get("source_file", ""), {}).get(receiver)
        if not type_name:
            continue
        if type_name in _LANGUAGE_BUILTIN_GLOBALS:
            continue
        type_defs = type_def_nids.get(_key(type_name), [])
        if len(type_defs) != 1:
            continue
        type_nid = type_defs[0]
        method_nid = method_index.get((type_nid, _key(callee)))
        target = method_nid or type_nid
        relation = "calls" if method_nid else "references"
        if target == caller or (caller, target) in existing_pairs:
            continue
        existing_pairs.add((caller, target))
        all_edges.append(
            {
                "source": caller,
                "target": target,
                "relation": relation,
                "context": "call",
                "confidence": "EXTRACTED",
                "confidence_score": 1.0,
                "source_file": rc.get("source_file", ""),
                "source_location": rc.get("source_location"),
                "weight": 1.0,
            }
        )


def _resolve_cpp_member_calls(
    per_file: list[dict],
    all_nodes: list[dict],
    all_edges: list[dict],
) -> None:
    """Resolve cross-file C++ member calls (``f.bar()``, ``f->bar()``,
    ``Foo::bar()``, ``this->bar()``) to the real definition of the receiver's type
    (#1547).

    The shared cross-file pass drops every ``is_member_call`` because a bare method
    name (``bar``) collides across the corpus and inflates god-nodes (#543/#1219).
    The C++ extractor records each member call's receiver and a per-file
    ``var -> ClassName`` table (``cpp_type_table``) built from local declarations.
    This pass types the receiver, then emits an edge ONLY when that type resolves
    to exactly ONE definition (the god-node guard).

    Receiver typing, by precision tier:
      * ``Foo::bar()`` — the scope ``Foo`` names the type explicitly -> EXTRACTED.
      * ``this->bar()`` — the receiver is the caller's own enclosing class -> EXTRACTED.
      * ``f.bar()`` / ``f->bar()`` — ``f`` typed via the file's local table -> INFERRED.
    A receiver whose type can't be inferred locally is SKIPPED (no guess): a false
    call edge is worse than a missing one. The ``_merge_decl_def_classes`` pass has
    already folded each header/impl class pair into one node, so a paired class is a
    single definition and clears the single-definition guard.

    Must run after id-disambiguation so node ids and caller_nids are final.
    """
    type_table_by_file: dict[str, dict[str, str]] = {}
    for result in per_file:
        tt = result.get("cpp_type_table")
        if tt and tt.get("path"):
            type_table_by_file[tt["path"]] = tt.get("table", {})

    def _key(label: str) -> str:
        return re.sub(r"[^a-zA-Z0-9]+", "", str(label)).lower()

    contained = {e.get("target") for e in all_edges if e.get("relation") == "contains"}

    type_def_nids: dict[str, list[str]] = {}
    node_by_id: dict[str, dict] = {}
    for n in all_nodes:
        node_by_id[n.get("id")] = n
        if n.get("source_file") and n.get("id") in contained and _is_type_like_definition(n):
            type_def_nids.setdefault(_key(n.get("label", "")), []).append(n["id"])

    method_index: dict[tuple[str, str], str] = {}
    enclosing_type: dict[str, str] = {}
    for rel in ("defines", "method"):
        for e in all_edges:
            if e.get("relation") != rel:
                continue
            src, tgt = e.get("source"), e.get("target")
            tnode = node_by_id.get(tgt)
            if tnode is None:
                continue
            enclosing_type.setdefault(tgt, src)
            method_index[(src, _key(tnode.get("label", "")))] = tgt

    all_raw_calls: list[dict] = []
    for result in per_file:
        all_raw_calls.extend(result.get("raw_calls", []))

    existing_pairs = {(e.get("source"), e.get("target")) for e in all_edges}
    for rc in all_raw_calls:
        if not rc.get("is_member_call"):
            continue
        receiver = rc.get("receiver")
        callee = rc.get("callee")
        caller = rc.get("caller_nid")
        if not receiver or not callee or not caller:
            continue
        src_file = rc.get("source_file", "")
        if rc.get("lang") != "cpp":
            continue
        if receiver == "this":
            type_nid = enclosing_type.get(caller)
            if not type_nid:
                continue
            type_qualified = True
        elif receiver[:1].isupper():
            type_defs = type_def_nids.get(_key(receiver), [])
            if len(type_defs) != 1:
                continue
            type_nid = type_defs[0]
            type_qualified = True
        else:
            type_name = type_table_by_file.get(src_file, {}).get(receiver)
            if not type_name:
                continue
            type_defs = type_def_nids.get(_key(type_name), [])
            if len(type_defs) != 1:
                continue
            type_nid = type_defs[0]
            type_qualified = False
        method_nid = method_index.get((type_nid, _key(callee)))
        target = method_nid or type_nid
        relation = "calls" if method_nid else "references"
        if target == caller or (caller, target) in existing_pairs:
            continue
        existing_pairs.add((caller, target))
        all_edges.append(
            {
                "source": caller,
                "target": target,
                "relation": relation,
                "context": "call",
                "confidence": "EXTRACTED" if type_qualified else "INFERRED",
                "confidence_score": 1.0 if type_qualified else 0.8,
                "source_file": src_file,
                "source_location": rc.get("source_location"),
                "weight": 1.0,
            }
        )


def _resolve_csharp_member_calls(
    per_file: list[dict],
    all_nodes: list[dict],
    all_edges: list[dict],
) -> None:
    """Resolve C# member calls (``recv.Method()``) to the receiver's declared type
    (#1609), namespace-aware (#1620).

    The shared cross-file pass drops every ``is_member_call`` because a bare method
    name collides across the corpus — and for C# an in-file bare match silently
    mis-bound ``_server.Save()`` to an unrelated ``Cache.Save()``. The C# extractor
    now records each member call's receiver plus a per-file ``name -> Type`` table
    (``csharp_type_table``) of fields/properties/params/locals (with conflicting
    rebindings POISONED out, so a shadowing local of a different type produces no
    edge rather than a wrong one). This pass types the receiver, then resolves the
    declared type name with the same namespace/using/alias scoping machinery the
    type-reference pass uses (``CsharpNameResolver``), so a class name duplicated
    across namespaces still binds to the one in scope; only when scoping knows
    nothing about the name does it fall back to the corpus-wide unique bare-name
    match (the god-node guard). An untypable/ambiguous receiver is skipped — never
    a guess.

    Receiver typing, by precision tier:
      * ``this.M()`` — receiver is the caller's own enclosing class -> EXTRACTED.
      * ``base.M()`` — the caller's single resolvable base class -> EXTRACTED.
      * ``Type.M()`` (capitalized) — the type is named explicitly in source -> EXTRACTED.
      * ``recv.M()`` / ``this.recv.M()`` — ``recv`` typed via the file's
        field/param/local table -> INFERRED.

    A method not declared on the receiver's type is looked up through its
    ``inherits`` chain; a chain containing an unresolvable (out-of-corpus) base
    poisons the lookup — the method may live there, so no edge is emitted.

    Must run after id-disambiguation so node ids and caller_nids are final.
    """
    type_table_by_file: dict[str, dict[str, str]] = {}
    for result in per_file:
        tt = result.get("csharp_type_table")
        if tt and tt.get("path"):
            type_table_by_file[tt["path"]] = tt.get("table", {})

    def _key(label: str) -> str:
        return re.sub(r"[^a-zA-Z0-9]+", "", str(label)).lower()

    contained = {e.get("target") for e in all_edges if e.get("relation") == "contains"}

    type_def_nids: dict[str, list[str]] = {}
    node_by_id: dict[str, dict] = {}
    for n in all_nodes:
        node_by_id[n.get("id")] = n
        if n.get("source_file") and n.get("id") in contained and _is_type_like_definition(n):
            type_def_nids.setdefault(_key(n.get("label", "")), []).append(n["id"])

    resolver = CsharpNameResolver(all_nodes, all_edges)

    method_index: dict[tuple[str, str], str] = {}
    enclosing_type: dict[str, str] = {}
    for e in all_edges:
        if e.get("relation") != "method":
            continue
        src, tgt = e.get("source"), e.get("target")
        tnode = node_by_id.get(tgt)
        if tnode is None:
            continue
        enclosing_type.setdefault(tgt, src)
        method_index[(src, _key(tnode.get("label", "")))] = tgt

    bases_of: dict[str, list[str]] = {}
    unresolved_base: set[str] = set()
    for e in all_edges:
        if e.get("relation") != "inherits":
            continue
        src_file = e.get("source_file")
        if not (isinstance(src_file, str) and src_file.endswith(".cs")):
            continue
        src, tgt = e.get("source"), e.get("target")
        if not (isinstance(src, str) and isinstance(tgt, str)):
            continue
        tnode = node_by_id.get(tgt)
        if tnode is None or not tnode.get("source_file"):
            unresolved_base.add(src)
        else:
            bucket = bases_of.setdefault(src, [])
            if tgt not in bucket:
                bucket.append(tgt)

    def _method_on_type_or_bases(type_nid: str, callee_key: str) -> str | None:
        """The method's definition on the type or its resolvable base chain.

        A type that declares the method directly wins (overrides shadow the
        base). Otherwise walk `inherits` upward; an unresolved base anywhere the
        walk actually reaches poisons the lookup (no edge), as does anything
        other than exactly one declaration found.
        """
        hits: set[str] = set()
        seen: set[str] = set()
        frontier = [type_nid]
        while frontier:
            nid = frontier.pop()
            if nid in seen:
                continue
            seen.add(nid)
            method_nid = method_index.get((nid, callee_key))
            if method_nid:
                hits.add(method_nid)
                continue
            if nid in unresolved_base:
                return None
            frontier.extend(bases_of.get(nid, []))
        return next(iter(hits)) if len(hits) == 1 else None

    def _resolve_type_name_nid(
        type_name: str | None, caller_node: dict | None, src_file: str
    ) -> str | None:
        """Resolve a declared type name to exactly one definition node id.

        Namespace/using/alias scoping first (so `Svc` duplicated across
        namespaces binds to the one in scope); when scoping is decisive but
        ambiguous, bail. Only when scoping knows nothing about the name fall
        back to the corpus-wide unique bare-name match (which also covers
        nested types, absent from the scoped index).
        """
        if not type_name:
            return None
        if caller_node is not None:
            resolved, decisive = resolver.resolve_type_name(type_name, caller_node, src_file)
            if resolved:
                return resolved
            if decisive:
                return None
        type_defs = type_def_nids.get(_key(type_name), [])
        return type_defs[0] if len(type_defs) == 1 else None

    all_raw_calls: list[dict] = []
    for result in per_file:
        all_raw_calls.extend(result.get("raw_calls", []))

    existing_pairs = {(e.get("source"), e.get("target")) for e in all_edges}
    for rc in all_raw_calls:
        if rc.get("lang") != "csharp" or not rc.get("is_member_call"):
            continue
        receiver = rc.get("receiver")
        callee = rc.get("callee")
        caller = rc.get("caller_nid")
        if not receiver or not callee or not caller:
            continue
        src_file = rc.get("source_file", "")
        caller_node = node_by_id.get(caller)
        if receiver == "this":
            type_nid = enclosing_type.get(caller)
            if not type_nid:
                continue
            type_qualified = True
        elif receiver == "base":
            enclosing = enclosing_type.get(caller)
            if not enclosing or enclosing in unresolved_base:
                continue
            bases = bases_of.get(enclosing, [])
            if len(bases) != 1:
                continue
            type_nid = bases[0]
            type_qualified = True
        elif receiver[:1].isupper():
            type_nid = _resolve_type_name_nid(receiver, caller_node, src_file)
            if not type_nid:
                type_name = type_table_by_file.get(src_file, {}).get(receiver)
                type_nid = _resolve_type_name_nid(type_name, caller_node, src_file)
                if not type_nid:
                    continue
            type_qualified = True
        else:
            type_name = type_table_by_file.get(src_file, {}).get(receiver)
            if not type_name:
                continue
            type_nid = _resolve_type_name_nid(type_name, caller_node, src_file)
            if not type_nid:
                continue
            type_qualified = False
        method_nid = _method_on_type_or_bases(type_nid, _key(callee))
        if not method_nid:
            continue
        if method_nid == caller or (caller, method_nid) in existing_pairs:
            continue
        existing_pairs.add((caller, method_nid))
        all_edges.append(
            {
                "source": caller,
                "target": method_nid,
                "relation": "calls",
                "context": "call",
                "confidence": "EXTRACTED" if type_qualified else "INFERRED",
                "confidence_score": 1.0 if type_qualified else 0.8,
                "source_file": src_file,
                "source_location": rc.get("source_location"),
                "weight": 1.0,
            }
        )


def _resolve_java_member_calls(
    per_file: list[dict],
    all_nodes: list[dict],
    all_edges: list[dict],
) -> None:
    """Resolve Java member calls against the receiver's declared type.

    Explicit type receivers and ``this`` are exact. Fields declared on the
    caller's class plus method parameters and explicit locals are inferred from
    the extractor's method-scoped type table. A missing or ambiguous receiver
    type is skipped rather than falling back to a bare method-name match.
    """

    def key(label: str) -> str:
        return str(label).strip().removeprefix(".").removesuffix("()")

    contained = {edge.get("target") for edge in all_edges if edge.get("relation") == "contains"}
    node_by_id = {node.get("id"): node for node in all_nodes}

    type_def_nids: dict[str, list[str]] = {}
    for node in all_nodes:
        if (
            node.get("source_file")
            and node.get("id") in contained
            and _is_type_like_definition(node)
        ):
            type_def_nids.setdefault(key(node.get("label", "")), []).append(node["id"])

    method_index: dict[tuple[str, str], set[str]] = {}
    enclosing_type: dict[str, str] = {}
    for edge in all_edges:
        if edge.get("relation") != "method":
            continue
        owner, method = edge.get("source"), edge.get("target")
        method_node = node_by_id.get(method)
        if method_node is None:
            continue
        enclosing_type.setdefault(method, owner)
        method_index.setdefault((owner, key(method_node.get("label", ""))), set()).add(method)

    existing_pairs = {(edge.get("source"), edge.get("target")) for edge in all_edges}
    for result in per_file:
        for raw_call in result.get("raw_calls", []):
            if raw_call.get("lang") != "java" or not raw_call.get("is_member_call"):
                continue
            receiver = raw_call.get("receiver")
            callee = raw_call.get("callee")
            caller = raw_call.get("caller_nid")
            if not receiver or not callee or not caller:
                continue

            exact = False
            if receiver == "this":
                type_nid = enclosing_type.get(caller)
                exact = True
                if not type_nid:
                    continue
            else:
                type_name = raw_call.get("receiver_type")
                if not type_name and receiver[:1].isupper():
                    type_name = receiver
                    exact = True
                if not type_name:
                    continue
                type_defs = type_def_nids.get(key(type_name), [])
                if len(type_defs) != 1:
                    continue
                type_nid = type_defs[0]

            method_nids = method_index.get((type_nid, key(callee)), set())
            if len(method_nids) != 1:
                continue
            method_nid = next(iter(method_nids))
            if method_nid == caller or (caller, method_nid) in existing_pairs:
                continue
            existing_pairs.add((caller, method_nid))
            all_edges.append(
                {
                    "source": caller,
                    "target": method_nid,
                    "relation": "calls",
                    "context": "call",
                    "confidence": "EXTRACTED" if exact else "INFERRED",
                    "confidence_score": 1.0 if exact else 0.8,
                    "source_file": raw_call.get("source_file", ""),
                    "source_location": raw_call.get("source_location"),
                    "weight": 1.0,
                }
            )


def _resolve_objc_member_calls(
    per_file: list[dict],
    all_nodes: list[dict],
    all_edges: list[dict],
) -> None:
    """Resolve cross-file Objective-C message sends (``[recv sel]``) to the real
    definition of the receiver's type (#1556).

    The ObjC extractor keeps its same-file selector matching (alloc/init refs,
    dot-syntax accesses, @selector) and additionally emits ``raw_calls`` for every
    message send, with the receiver and the reconstructed selector as the callee.
    This pass types the receiver and emits a cross-file ``calls`` edge ONLY when the
    type resolves to exactly ONE definition (the god-node guard).

    Receiver typing:
      * ``self`` / ``super`` — the caller's own enclosing class -> EXTRACTED.
      * Capitalized receiver (``[Foo new]``) — the type named explicitly -> EXTRACTED.
      * ``[f doThing]`` — ``f`` typed via the file's ``Foo *f`` local table -> INFERRED.
    An uninferable receiver is SKIPPED (no guess), so an ambiguous selector across
    classes never fans out. ``_merge_decl_def_classes`` folds each @interface/@impl
    pair into one node, so a paired class clears the single-definition guard.

    Must run after id-disambiguation so node ids and caller_nids are final.
    """
    type_table_by_file: dict[str, dict[str, str]] = {}
    for result in per_file:
        tt = result.get("objc_type_table")
        if tt and tt.get("path"):
            type_table_by_file[tt["path"]] = tt.get("table", {})

    def _key(label: str) -> str:
        return re.sub(r"[^a-zA-Z0-9]+", "", str(label)).lower()

    contained = {e.get("target") for e in all_edges if e.get("relation") == "contains"}

    type_def_nids: dict[str, list[str]] = {}
    node_by_id: dict[str, dict] = {}
    for n in all_nodes:
        node_by_id[n.get("id")] = n
        if n.get("source_file") and n.get("id") in contained and _is_type_like_definition(n):
            type_def_nids.setdefault(_key(n.get("label", "")), []).append(n["id"])

    method_index: dict[tuple[str, str], str] = {}
    enclosing_type: dict[str, str] = {}
    for e in all_edges:
        if e.get("relation") != "method":
            continue
        src, tgt = e.get("source"), e.get("target")
        enclosing_type.setdefault(tgt, src)
        tnode = node_by_id.get(tgt)
        if tnode is not None:
            method_index[(src, _key(tnode.get("label", "")))] = tgt

    all_raw_calls: list[dict] = []
    for result in per_file:
        all_raw_calls.extend(result.get("raw_calls", []))

    existing_pairs = {(e.get("source"), e.get("target")) for e in all_edges}
    for rc in all_raw_calls:
        if not rc.get("is_member_call"):
            continue
        receiver = rc.get("receiver")
        callee = rc.get("callee")
        caller = rc.get("caller_nid")
        if not receiver or not callee or not caller:
            continue
        src_file = rc.get("source_file", "")
        if rc.get("lang") != "objc":
            continue
        if receiver in ("self", "super"):
            type_nid = enclosing_type.get(caller)
            if not type_nid:
                continue
            type_qualified = True
        elif receiver[:1].isupper():
            type_defs = type_def_nids.get(_key(receiver), [])
            if len(type_defs) != 1:
                continue
            type_nid = type_defs[0]
            type_qualified = True
        else:
            type_name = type_table_by_file.get(src_file, {}).get(receiver)
            if not type_name:
                continue
            type_defs = type_def_nids.get(_key(type_name), [])
            if len(type_defs) != 1:
                continue
            type_nid = type_defs[0]
            type_qualified = False
        method_nid = method_index.get((type_nid, _key(callee)))
        target = method_nid or type_nid
        relation = "calls" if method_nid else "references"
        if target == caller or (caller, target) in existing_pairs:
            continue
        existing_pairs.add((caller, target))
        all_edges.append(
            {
                "source": caller,
                "target": target,
                "relation": relation,
                "context": "call",
                "confidence": "EXTRACTED" if type_qualified else "INFERRED",
                "confidence_score": 1.0 if type_qualified else 0.8,
                "source_file": src_file,
                "source_location": rc.get("source_location"),
                "weight": 1.0,
            }
        )


register_language_resolver(
    LanguageResolver("swift_member_calls", frozenset({".swift"}), _resolve_swift_member_calls)
)
register_language_resolver(
    LanguageResolver("python_member_calls", frozenset({".py"}), _resolve_python_member_calls)
)
register_language_resolver(
    LanguageResolver("ruby_member_calls", frozenset({".rb", ".rake"}), resolve_ruby_member_calls)
)
register_language_resolver(
    LanguageResolver(
        "typescript_member_calls",
        frozenset({".ts", ".tsx", ".mts", ".cts", ".js", ".jsx"}),
        _resolve_typescript_member_calls,
    )
)
register_language_resolver(
    LanguageResolver(
        "cpp_member_calls",
        frozenset({".cpp", ".cc", ".cxx", ".hpp", ".cu", ".cuh", ".metal", ".h"}),
        _resolve_cpp_member_calls,
    )
)
register_language_resolver(
    LanguageResolver(
        "objc_member_calls",
        frozenset({".m", ".mm", ".h"}),
        _resolve_objc_member_calls,
    )
)
register_language_resolver(
    LanguageResolver("csharp_member_calls", frozenset({".cs"}), _resolve_csharp_member_calls)
)
register_language_resolver(
    LanguageResolver("java_member_calls", frozenset({".java"}), _resolve_java_member_calls)
)
register_language_resolver(
    LanguageResolver(
        "pascal_inherited_calls",
        frozenset({".pas", ".pp", ".dpr", ".dpk", ".inc"}),
        resolve_pascal_inherited_calls,
    )
)


_PROJECT_XML_MAX_BYTES = 2 * 1024 * 1024


def _project_xml_is_safe(src: bytes) -> bool:
    """Reject XML that declares DTDs or entities.

    Stdlib ``xml.etree.ElementTree`` does not cap entity expansion, so a
    crafted project file could trigger a billion-laughs style DoS. External
    entity resolution is already disabled by pyexpat defaults, but rejecting
    ``<!DOCTYPE`` / ``<!ENTITY`` outright is defense in depth.

    Legitimate MSBuild and Lazarus package files never contain a DOCTYPE
    or ENTITY declaration, so this is a zero-false-positive screen.
    """
    lowered = src.lower()
    return b"<!doctype" not in lowered and b"<!entity" not in lowered


def extract_lazarus_package(path: Path) -> dict:
    """Extract package metadata from Lazarus .lpk package files (XML format).

    .lpk is an XML file listing the package name, required dependencies,
    and the Pascal units that belong to the package.

    Produces nodes for:
    - The package file itself
    - The package (by name)
    - Each required package (dependency)
    - Each listed unit file (resolved to path-based IDs where possible)

    Produces edges for:
    - file --contains--> package
    - package --imports--> required dependency (context: "import")
    - package --contains--> listed unit
    """
    try:
        import xml.etree.ElementTree as ET

        src = path.read_bytes()
    except OSError as e:
        return {"nodes": [], "edges": [], "error": str(e)}

    if len(src) > _PROJECT_XML_MAX_BYTES:
        return {"nodes": [], "edges": [], "error": "package file too large"}
    if not _project_xml_is_safe(src):
        return {"nodes": [], "edges": [], "error": "refusing XML with DOCTYPE/ENTITY declaration"}

    try:
        xml_root = ET.fromstring(src)
    except Exception as e:
        return {"nodes": [], "edges": [], "error": str(e)}

    str_path = str(path)
    stem = _file_stem(path)
    nodes: list[dict] = []
    edges: list[dict] = []
    seen_ids: set[str] = set()

    def add_node(nid: str, label: str) -> None:
        if nid not in seen_ids:
            seen_ids.add(nid)
            nodes.append(
                {
                    "id": nid,
                    "label": label,
                    "file_type": "code",
                    "source_file": str_path,
                    "source_location": "L1",
                }
            )

    def add_edge(src: str, tgt: str, relation: str, context: str | None = None) -> None:
        edge: dict[str, Any] = {
            "source": src,
            "target": tgt,
            "relation": relation,
            "confidence": "EXTRACTED",
            "source_file": str_path,
            "source_location": "L1",
            "weight": 1.0,
        }
        if context:
            edge["context"] = context
        edges.append(edge)

    file_nid = _make_id(str(path))
    add_node(file_nid, path.name)

    name_elem = xml_root.find(".//Package/Name")
    pkg_name = name_elem.get("Value") if name_elem is not None else path.stem
    pkg_nid = _make_id(stem, pkg_name)
    add_node(pkg_nid, pkg_name)
    add_edge(file_nid, pkg_nid, "contains")

    for item in xml_root.findall(".//RequiredPkgs/"):
        dep_elem = item.find("PackageName")
        if dep_elem is not None:
            dep_name = dep_elem.get("Value", "")
            if dep_name:
                dep_nid = _make_id(dep_name)
                add_node(dep_nid, dep_name)
                add_edge(pkg_nid, dep_nid, "imports", context="import")

    for item in xml_root.findall(".//Files/"):
        unit_elem = item.find("UnitName")
        if unit_elem is not None:
            unit_name = unit_elem.get("Value", "")
            if unit_name:
                unit_nid = _pascal_resolve_unit(path, unit_name)
                add_node(unit_nid, unit_name)
                add_edge(pkg_nid, unit_nid, "contains")

    return {"nodes": nodes, "edges": edges, "input_tokens": 0, "output_tokens": 0}


def _check_tree_sitter_version() -> None:
    """Raise a clear error if tree-sitter is too old for the new Language API."""
    try:
        from tree_sitter import LANGUAGE_VERSION
    except ImportError:
        raise ImportError("tree-sitter is not installed. Run: pip install 'tree-sitter>=0.23.0'")
    if LANGUAGE_VERSION < 14:
        import tree_sitter as _ts

        raise RuntimeError(
            f"tree-sitter {getattr(_ts, '__version__', 'unknown')} is too old. "
            f"graphify requires tree-sitter >= 0.23.0 (Language API v2). "
            f"Run: pip install --upgrade tree-sitter"
        )


def extract_slnx(path: Path) -> dict:
    """Extract projects and inter-project dependencies from a .slnx file.

    .slnx is the XML-based replacement for the legacy .sln format. Projects
    are listed as ``<Project Path="..."/>`` elements (optionally nested inside
    ``<Folder>`` elements) and build-order dependencies as ``<BuildDependency
    Project="..."/>`` children. Unlike .sln there are no GUIDs -- projects are
    identified by their path.
    """
    import xml.etree.ElementTree as ET

    try:
        src = path.read_bytes()
    except OSError:
        return {"nodes": [], "edges": [], "error": f"cannot read {path}"}

    if len(src) > _PROJECT_XML_MAX_BYTES:
        return {"nodes": [], "edges": [], "error": "project file too large"}
    if not _project_xml_is_safe(src):
        return {"nodes": [], "edges": [], "error": "refusing XML with DOCTYPE/ENTITY declaration"}

    try:
        tree = ET.fromstring(src)
    except ET.ParseError as e:
        return {"nodes": [], "edges": [], "error": f"XML parse error: {e}"}

    file_nid = _make_id(str(path))
    str_path = str(path)
    nodes: list[dict] = [
        {
            "id": file_nid,
            "label": path.name,
            "file_type": "code",
            "source_file": str_path,
            "source_location": None,
        }
    ]
    edges: list[dict] = []
    seen_ids: set[str] = set()
    seen_ids.add(file_nid)

    ns = ""
    if tree.tag.startswith("{"):
        ns = tree.tag.split("}")[0] + "}"

    def _resolve(proj_path: str) -> str:
        proj_path = proj_path.replace("\\", "/")
        try:
            return str((path.parent / proj_path).resolve())
        except Exception:
            return proj_path

    project_nids: set[str] = set()
    for proj in tree.iter(f"{ns}Project"):
        proj_path = proj.get("Path")
        if not proj_path:
            continue
        abs_proj = _resolve(proj_path)
        proj_nid = _make_id(abs_proj)
        if proj_nid and proj_nid not in seen_ids:
            seen_ids.add(proj_nid)
            label = Path(proj_path).stem
            nodes.append(
                {
                    "id": proj_nid,
                    "label": label,
                    "file_type": "code",
                    "source_file": abs_proj,
                    "source_location": None,
                }
            )
            edges.append(
                {
                    "source": file_nid,
                    "target": proj_nid,
                    "relation": "contains",
                    "confidence": "EXTRACTED",
                    "source_file": str_path,
                    "weight": 1.0,
                }
            )
        if proj_nid:
            project_nids.add(proj_nid)

    for proj in tree.iter(f"{ns}Project"):
        proj_path = proj.get("Path")
        if not proj_path:
            continue
        from_nid = _make_id(_resolve(proj_path))
        for dep in proj.iter(f"{ns}BuildDependency"):
            dep_path = dep.get("Project")
            if not dep_path:
                continue
            to_nid = _make_id(_resolve(dep_path))
            if from_nid and to_nid and from_nid != to_nid and to_nid in project_nids:
                edges.append(
                    {
                        "source": from_nid,
                        "target": to_nid,
                        "relation": "imports",
                        "confidence": "EXTRACTED",
                        "source_file": str_path,
                        "weight": 1.0,
                    }
                )

    return {"nodes": nodes, "edges": edges}


def extract_csproj(path: Path) -> dict:
    """Extract packages, project refs, and target framework from a .csproj/.fsproj/.vbproj."""
    import xml.etree.ElementTree as ET

    try:
        src = path.read_bytes()
    except OSError:
        return {"nodes": [], "edges": [], "error": f"cannot read {path}"}

    if len(src) > _PROJECT_XML_MAX_BYTES:
        return {"nodes": [], "edges": [], "error": "project file too large"}
    if not _project_xml_is_safe(src):
        return {"nodes": [], "edges": [], "error": "refusing XML with DOCTYPE/ENTITY declaration"}

    try:
        tree = ET.fromstring(src)
    except ET.ParseError as e:
        return {"nodes": [], "edges": [], "error": f"XML parse error: {e}"}

    file_nid = _make_id(str(path))
    str_path = str(path)
    nodes: list[dict] = [
        {
            "id": file_nid,
            "label": path.name,
            "file_type": "code",
            "source_file": str_path,
            "source_location": None,
        }
    ]
    edges: list[dict] = []
    seen_ids: set[str] = set()
    seen_ids.add(file_nid)

    ns = ""
    root_tag = tree.tag
    if root_tag.startswith("{"):
        ns = root_tag.split("}")[0] + "}"

    def find_all(tag: str):
        return tree.iter(f"{ns}{tag}")

    for tf in find_all("TargetFramework"):
        if tf.text:
            fw_nid = _make_id("framework", tf.text.strip())
            if fw_nid and fw_nid not in seen_ids:
                seen_ids.add(fw_nid)
                nodes.append(
                    {
                        "id": fw_nid,
                        "label": tf.text.strip(),
                        "file_type": "concept",
                        "source_file": str_path,
                        "source_location": None,
                    }
                )
                edges.append(
                    {
                        "source": file_nid,
                        "target": fw_nid,
                        "relation": "references",
                        "confidence": "EXTRACTED",
                        "source_file": str_path,
                        "weight": 1.0,
                    }
                )

    for tf in find_all("TargetFrameworks"):
        if tf.text:
            for fw in tf.text.strip().split(";"):
                fw = fw.strip()
                if fw:
                    fw_nid = _make_id("framework", fw)
                    if fw_nid and fw_nid not in seen_ids:
                        seen_ids.add(fw_nid)
                        nodes.append(
                            {
                                "id": fw_nid,
                                "label": fw,
                                "file_type": "concept",
                                "source_file": str_path,
                                "source_location": None,
                            }
                        )
                        edges.append(
                            {
                                "source": file_nid,
                                "target": fw_nid,
                                "relation": "references",
                                "confidence": "EXTRACTED",
                                "source_file": str_path,
                                "weight": 1.0,
                            }
                        )

    for pkg in find_all("PackageReference"):
        name = pkg.get("Include") or pkg.get("include") or ""
        version = pkg.get("Version") or pkg.get("version") or ""
        if not name:
            continue
        pkg_nid = _make_id("nuget", name)
        label = f"{name} ({version})" if version else name
        if pkg_nid and pkg_nid not in seen_ids:
            seen_ids.add(pkg_nid)
            nodes.append(
                {
                    "id": pkg_nid,
                    "label": label,
                    "file_type": "code",
                    "source_file": str_path,
                    "source_location": None,
                }
            )
        edges.append(
            {
                "source": file_nid,
                "target": pkg_nid,
                "relation": "imports",
                "confidence": "EXTRACTED",
                "source_file": str_path,
                "weight": 1.0,
            }
        )

    for proj in find_all("ProjectReference"):
        ref_path = proj.get("Include") or proj.get("include") or ""
        if not ref_path:
            continue
        ref_path_norm = ref_path.replace("\\", "/")
        try:
            abs_ref = str((path.parent / ref_path_norm).resolve())
        except Exception:
            abs_ref = ref_path_norm
        proj_nid = _make_id(abs_ref)
        if proj_nid and proj_nid not in seen_ids:
            seen_ids.add(proj_nid)
            proj_label = Path(ref_path_norm).name
            nodes.append(
                {
                    "id": proj_nid,
                    "label": proj_label,
                    "file_type": "code",
                    "source_file": abs_ref,
                    "source_location": None,
                }
            )
        edges.append(
            {
                "source": file_nid,
                "target": proj_nid,
                "relation": "imports",
                "confidence": "EXTRACTED",
                "source_file": str_path,
                "weight": 1.0,
            }
        )

    sdk = tree.get("Sdk") or ""
    if sdk:
        sdk_nid = _make_id("sdk", sdk)
        if sdk_nid and sdk_nid not in seen_ids:
            seen_ids.add(sdk_nid)
            nodes.append(
                {
                    "id": sdk_nid,
                    "label": sdk,
                    "file_type": "concept",
                    "source_file": str_path,
                    "source_location": None,
                }
            )
            edges.append(
                {
                    "source": file_nid,
                    "target": sdk_nid,
                    "relation": "references",
                    "confidence": "EXTRACTED",
                    "source_file": str_path,
                    "weight": 1.0,
                }
            )

    return {"nodes": nodes, "edges": edges}


def _xml_local_name(name: str) -> str:
    return name.rsplit("}", 1)[-1] if name.startswith("{") else name


_EVENT_HANDLER_SIGNATURE_RE = re.compile(
    r"\(\s*object\??\s+\w+\s*,\s*[\w.]*EventArgs(?:<[^>]*>)?\s+\w+\s*\)"
)

_XAML_NON_EVENT_ATTRS = frozenset(
    {
        "Name",
        "Content",
        "Text",
        "Title",
        "Tag",
        "ToolTip",
        "Header",
        "Class",
        "Key",
        "Uid",
        "DataContext",
        "Style",
        "Source",
    }
)

_XAML_IDENT_RE = re.compile(r"[A-Za-z_]\w*")
_XAML_DESIGN_INSTANCE_TYPE_RE = re.compile(r"\bType\s*=\s*(?:\{x:Type\s+)?(?P<type>[\w.:+]+)")


def _xaml_markup_extension(value: str) -> tuple[str, str] | None:
    value = value.strip()
    if not (value.startswith("{") and value.endswith("}")):
        return None
    inner = value[1:-1].strip()
    if not inner or inner.startswith("}"):
        return None
    name, _, args = inner.partition(" ")
    return name, args.strip()


def _xaml_split_markup_args(args: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    for idx, ch in enumerate(args):
        if ch == "{":
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(args[start:idx].strip())
            start = idx + 1
    tail = args[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _xaml_static_resource_key(value: str) -> str | None:
    markup = _xaml_markup_extension(value)
    if not markup:
        return None
    name, args = markup
    if name != "StaticResource":
        return None
    for part in _xaml_split_markup_args(args):
        if "=" not in part:
            return part.strip() or None
        key, resource = part.split("=", 1)
        if key.strip() == "ResourceKey":
            return resource.strip() or None
    return None


def _xaml_binding_refs(value: str) -> tuple[str | None, str | None]:
    markup = _xaml_markup_extension(value)
    if not markup:
        return None, None
    name, args = markup
    if name != "Binding":
        return None, None

    path_ref = None
    converter_ref = None
    for part in _xaml_split_markup_args(args):
        if not part:
            continue
        if "=" not in part:
            if path_ref is None:
                path_ref = part.strip()
            continue
        key, raw_value = part.split("=", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if key == "Path":
            path_ref = raw_value
        elif key == "Converter":
            converter_ref = _xaml_static_resource_key(raw_value)

    if path_ref and ("{" in path_ref or "}" in path_ref):
        path_ref = None
    return path_ref or None, converter_ref or None


def _xaml_codebehind_path(path: Path) -> Path | None:
    expected = path.with_suffix(path.suffix + ".cs")
    if expected.exists():
        return expected
    try:
        for sibling in path.parent.iterdir():
            if sibling.name.casefold() == expected.name.casefold():
                return sibling
    except OSError:
        return None
    return None


def _xaml_codebehind_symbols(
    path: Path,
    class_name: str | None,
) -> tuple[dict | None, dict[str, dict], list[dict]]:
    codebehind = _xaml_codebehind_path(path)
    if not codebehind:
        return None, {}, []
    result = extract_csharp(codebehind)
    if result.get("error"):
        return None, {}, []

    class_simple = class_name.rsplit(".", 1)[-1] if class_name else None
    class_node = None
    if class_simple:
        for node in result.get("nodes", []):
            if node.get("label") == class_simple:
                class_node = node
                break

    class_method_edges: list[dict] = []
    if class_node:
        class_id = class_node.get("id")
        for edge in result.get("edges", []):
            if edge.get("source") == class_id and edge.get("relation") == "method":
                class_method_edges.append(edge)
    method_ids = {edge.get("target") for edge in class_method_edges} if class_node else None

    try:
        cb_lines = codebehind.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        cb_lines = []

    def _has_event_handler_signature(node: dict) -> bool:
        loc = str(node.get("source_location") or "")
        m = re.match(r"L(\d+)", loc)
        if not m or not cb_lines:
            return False
        start = int(m.group(1)) - 1
        snippet = " ".join(cb_lines[start : start + 3])
        return _EVENT_HANDLER_SIGNATURE_RE.search(snippet) is not None

    methods: dict[str, dict] = {}
    for node in result.get("nodes", []):
        if method_ids is not None and node.get("id") not in method_ids:
            continue
        label = str(node.get("label", ""))
        if label.startswith(".") and label.endswith("()") and _has_event_handler_signature(node):
            methods[label.strip("()").lstrip(".")] = node
    return class_node, methods, class_method_edges


def _xaml_type_simple_name(type_ref: str) -> str | None:
    type_ref = type_ref.strip().strip("{}")
    if not type_ref:
        return None
    type_ref = type_ref.split(",", 1)[0].strip()
    if type_ref.startswith("x:Type "):
        type_ref = type_ref[len("x:Type ") :].strip()
    if ":" in type_ref:
        type_ref = type_ref.rsplit(":", 1)[-1]
    if "." in type_ref:
        type_ref = type_ref.rsplit(".", 1)[-1]
    if "+" in type_ref:
        type_ref = type_ref.rsplit("+", 1)[-1]
    return type_ref if _XAML_IDENT_RE.fullmatch(type_ref) else None


def _xaml_explicit_viewmodel_names(tree) -> tuple[bool, list[str]]:
    has_data_context = False
    names: list[str] = []
    for elem in tree.iter():
        elem_type = _xml_local_name(elem.tag)
        if elem_type.endswith(".DataContext") or elem_type == "DataContext":
            has_data_context = True
            for child in list(elem):
                vm_name = _xaml_type_simple_name(_xml_local_name(child.tag))
                if vm_name and vm_name not in names:
                    names.append(vm_name)
        for key, value in elem.attrib.items():
            if _xml_local_name(key) != "DataContext" or not value:
                continue
            has_data_context = True
            match = _XAML_DESIGN_INSTANCE_TYPE_RE.search(value)
            if match:
                vm_name = _xaml_type_simple_name(match.group("type"))
                if vm_name and vm_name not in names:
                    names.append(vm_name)
    return has_data_context, names


def _xaml_prism_autowire_viewmodel(tree) -> bool:
    for elem in tree.iter():
        for key, value in elem.attrib.items():
            if (
                _xml_local_name(key).endswith("ViewModelLocator.AutoWireViewModel")
                and value.strip().lower() == "true"
            ):
                return True
    return False


def _xaml_inferred_viewmodel_names(view_name: str | None) -> list[str]:
    if not view_name:
        return []
    names: list[str] = []

    def add(name: str) -> None:
        if name.endswith("ViewModel") and name not in names:
            names.append(name)

    if view_name == "MainWindow":
        add("MainWindowViewModel")
        add("MainViewModel")
    for suffix in ("UserControl", "View", "Page", "Control"):
        if view_name.endswith(suffix) and len(view_name) > len(suffix):
            add(view_name[: -len(suffix)] + "ViewModel")
            break
    return names


def _xaml_project_root(path: Path) -> Path:
    project_markers = (".csproj", ".fsproj", ".vbproj", ".sln", ".slnx")
    root = path.parent
    for directory in (path.parent, *path.parent.parents):
        try:
            if any(child.suffix in project_markers for child in directory.iterdir()):
                root = directory
                break
        except OSError:
            continue
    if _XAML_ACTIVE_EXTRACT_ROOT is None:
        return root
    boundary = _XAML_ACTIVE_EXTRACT_ROOT.resolve()
    try:
        root.resolve().relative_to(boundary)
        return root
    except ValueError:
        return boundary


def _xaml_csharp_class_nodes(path: Path) -> dict[str, list[dict]]:
    from graphify.detect import _is_ignored, _is_noise_dir, _load_graphifyignore

    root = _xaml_project_root(path)
    cache_key = str(root.resolve()) if _XAML_ACTIVE_EXTRACT_ROOT is not None else None
    if cache_key and cache_key in _XAML_CSHARP_CLASS_CACHE:
        return _XAML_CSHARP_CLASS_CACHE[cache_key]
    classes: dict[str, list[dict]] = {}
    patterns = _load_graphifyignore(root)
    ignore_cache: dict[Path, bool] = {}
    import os as _os

    _DIR_CAP = 20000
    cs_files: list[Path] = []
    visited = 0
    try:
        for dirpath, dirnames, filenames in _os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and not _is_noise_dir(d)]
            for fn in filenames:
                if fn.endswith(".cs"):
                    cs_files.append(Path(dirpath) / fn)
            visited += 1
            if visited >= _DIR_CAP:
                break
    except OSError:
        return classes
    cs_files.sort()
    for cs_path in cs_files:
        if patterns and _is_ignored(cs_path, root, patterns, _cache=ignore_cache):
            continue
        result = extract_csharp(cs_path)
        if result.get("error"):
            continue
        for node in result.get("nodes", []):
            label = str(node.get("label", ""))
            if not label.endswith("ViewModel") or not _XAML_IDENT_RE.fullmatch(label):
                continue
            if node.get("source_file"):
                classes.setdefault(label, []).append(node)
    if cache_key:
        _XAML_CSHARP_CLASS_CACHE[cache_key] = classes
    return classes


def _xaml_pascal_name(name: str) -> str | None:
    name = name.strip().lstrip("_")
    if name.startswith("m_"):
        name = name[2:]
    return name[:1].upper() + name[1:] if _XAML_IDENT_RE.fullmatch(name) else None


_XAML_TOOLKIT_FIELD_RE = re.compile(r"\b(?P<name>_?m?_?[A-Za-z_]\w*)\s*(?:=.*)?;")
_XAML_TOOLKIT_METHOD_RE = re.compile(r"\b(?P<name>[A-Za-z_]\w*)\s*\(")
_XAML_ACTIVE_EXTRACT_ROOT: Path | None = None
_XAML_CSHARP_CLASS_CACHE: dict[str, dict[str, list[dict]]] = {}


def _xaml_communitytoolkit_members(vm_node: dict) -> tuple[dict[str, dict], list[dict]]:
    source_file = vm_node.get("source_file")
    vm_id = vm_node.get("id")
    if not source_file or not vm_id:
        return {}, []
    try:
        lines = Path(source_file).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}, []

    members: dict[str, dict] = {}
    edges: list[dict] = []

    def add_member(label: str, line_no: int, context: str) -> None:
        nid = _make_id(vm_id, label)
        members[label] = {
            "id": nid,
            "label": label,
            "file_type": "code",
            "source_file": source_file,
            "source_location": f"L{line_no}",
        }
        edges.append(
            {
                "source": vm_id,
                "target": nid,
                "relation": "defines",
                "confidence": "INFERRED",
                "source_file": source_file,
                "source_location": f"L{line_no}",
                "weight": 1.0,
                "context": context,
            }
        )

    pending: tuple[str, int] | None = None
    for line_no, line in enumerate(lines, 1):
        remainder = line.split("]", 1)[1].strip() if "]" in line else ""
        if "[" in line and "ObservableProperty" in line:
            pending = ("property", line_no)
            if not remainder:
                continue
            line = remainder
        if "[" in line and "RelayCommand" in line:
            pending = ("command", line_no)
            if not remainder:
                continue
            line = remainder
        if not pending or not line.strip() or line.lstrip().startswith("["):
            continue

        kind, attr_line = pending
        pending = None
        if kind == "property":
            match = _XAML_TOOLKIT_FIELD_RE.search(line)
            label = _xaml_pascal_name(match.group("name")) if match else None
            if label:
                add_member(label, attr_line, "communitytoolkit_observable_property")
        else:
            match = _XAML_TOOLKIT_METHOD_RE.search(line)
            if match:
                method = match.group("name").removesuffix("Async")
                add_member(f"{method}Command", attr_line, "communitytoolkit_relay_command")

    return members, edges


def extract_xaml(path: Path) -> dict:
    """Extract WPF/XAML structure, bindings, x:Class, and event handler references."""
    import xml.etree.ElementTree as ET

    try:
        src = path.read_bytes()
    except OSError:
        return {"nodes": [], "edges": [], "error": f"cannot read {path}"}

    if len(src) > _PROJECT_XML_MAX_BYTES:
        return {"nodes": [], "edges": [], "error": "xaml file too large"}
    if not _project_xml_is_safe(src):
        return {"nodes": [], "edges": [], "error": "refusing XML with DOCTYPE/ENTITY declaration"}

    try:
        tree = ET.fromstring(src)
    except ET.ParseError as e:
        return {"nodes": [], "edges": [], "error": f"XML parse error: {e}"}

    text = src.decode("utf-8", errors="replace")
    lines = text.splitlines()
    str_path = str(path)
    stem = _file_stem(path)
    file_nid = _make_id(str(path))
    root_type = _xml_local_name(tree.tag)
    root_nid = _make_id(stem, root_type)
    nodes: list[dict] = []
    edges: list[dict] = []
    seen_ids: set[str] = set()
    seen_edges: set[tuple[str, str, str, str | None]] = set()

    def line_for(value: str | None) -> int:
        if value:
            for idx, line in enumerate(lines, 1):
                if value in line:
                    return idx
        return 1

    def add_node(
        nid: str,
        label: str,
        line: int | None,
        *,
        file_type: str = "code",
        source_file: str = str_path,
    ) -> None:
        if nid in seen_ids:
            return
        seen_ids.add(nid)
        nodes.append(
            {
                "id": nid,
                "label": label,
                "file_type": file_type,
                "source_file": source_file,
                "source_location": f"L{line}" if line else None,
            }
        )

    def add_existing_node(node: dict | None) -> None:
        if not node:
            return
        nid = node.get("id")
        if not nid or nid in seen_ids:
            return
        seen_ids.add(nid)
        nodes.append(dict(node))

    def add_edge(
        src_nid: str,
        tgt_nid: str,
        relation: str,
        line: int,
        *,
        context: str | None = None,
        source_file: str = str_path,
        confidence: str = "EXTRACTED",
    ) -> None:
        key = (src_nid, tgt_nid, relation, context)
        if key in seen_edges:
            return
        seen_edges.add(key)
        edge = {
            "source": src_nid,
            "target": tgt_nid,
            "relation": relation,
            "confidence": confidence,
            "source_file": source_file,
            "source_location": f"L{line}",
            "weight": 1.0,
        }
        if context:
            edge["context"] = context
        edges.append(edge)

    def add_existing_edge(edge: dict) -> None:
        key = (edge.get("source"), edge.get("target"), edge.get("relation"), edge.get("context"))
        if key in seen_edges:
            return
        seen_edges.add(key)
        edges.append(dict(edge))

    add_node(file_nid, path.name, 1)
    add_node(root_nid, root_type, 1)
    add_edge(file_nid, root_nid, "contains", 1)

    class_name = None
    for key, value in tree.attrib.items():
        if _xml_local_name(key) == "Class" and value:
            class_name = value.strip()
            break

    class_node, codebehind_methods, class_method_edges = _xaml_codebehind_symbols(path, class_name)
    if class_name:
        if class_node:
            class_nid = class_node["id"]
            add_existing_node(class_node)
        else:
            class_label = class_name.rsplit(".", 1)[-1]
            class_nid = _make_id(stem, class_label)
            add_node(class_nid, class_label, line_for(class_name))
        add_edge(root_nid, class_nid, "references", line_for(class_name), context="x_class")

    has_data_context, vm_names = _xaml_explicit_viewmodel_names(tree)
    prism_autowire = _xaml_prism_autowire_viewmodel(tree)
    vm_confidence = "EXTRACTED"
    if not has_data_context:
        view_name = class_name.rsplit(".", 1)[-1] if class_name else None
        view_name = view_name or (path.stem if prism_autowire else None)
        vm_names = _xaml_inferred_viewmodel_names(view_name)
        vm_confidence = "INFERRED"
    generated_members: dict[str, dict] = {}
    generated_member_edges: list[dict] = []
    if vm_names:
        csharp_classes = _xaml_csharp_class_nodes(path)
        vm_candidates = []
        for vm_name in vm_names:
            vm_candidates.extend(csharp_classes.get(vm_name, []))
        by_id = {node.get("id"): node for node in vm_candidates if node.get("id")}
        if len(by_id) == 1:
            vm_node = next(iter(by_id.values()))
            add_existing_node(vm_node)
            add_edge(
                root_nid,
                vm_node["id"],
                "references",
                line_for(vm_node["label"]),
                context="view_model",
                confidence=vm_confidence,
            )
            generated_members, generated_member_edges = _xaml_communitytoolkit_members(vm_node)
            for member in generated_members.values():
                add_existing_node(member)
            for member_edge in generated_member_edges:
                add_existing_edge(member_edge)

    for elem in tree.iter():
        elem_type = _xml_local_name(elem.tag)
        elem_name = None
        for key, value in elem.attrib.items():
            if _xml_local_name(key) == "Name" and value:
                elem_name = value.strip()
                break
        owner_nid = root_nid
        if elem_name:
            owner_nid = _make_id(stem, elem_name)
            add_node(owner_nid, elem_name, line_for(elem_name))
            add_edge(root_nid, owner_nid, "contains", line_for(elem_name))
            type_nid = _make_id("xaml", elem_type)
            add_node(type_nid, elem_type, line_for(elem_name), file_type="concept")
            add_edge(owner_nid, type_nid, "references", line_for(elem_name), context="type")

        for key, value in elem.attrib.items():
            value = value or ""
            attr_local = _xml_local_name(key)
            if attr_local not in _XAML_NON_EVENT_ATTRS and _XAML_IDENT_RE.fullmatch(value):
                method = codebehind_methods.get(value)
                if method:
                    add_existing_node(method)
                    add_edge(
                        owner_nid, method["id"], "references", line_for(value), context="event"
                    )
                    for method_edge in class_method_edges:
                        if method_edge.get("target") == method["id"]:
                            add_existing_node(class_node)
                            add_existing_edge(method_edge)
                            break
            binding_path, binding_converter = _xaml_binding_refs(value)
            if binding_path:
                bind_nid = _make_id("binding", binding_path)
                add_node(bind_nid, binding_path, line_for(value), file_type="concept")
                binding_context = (
                    "binding_command"
                    if attr_local == "Command" or attr_local.endswith(".Command")
                    else "binding_path"
                )
                add_edge(
                    owner_nid, bind_nid, "references", line_for(value), context=binding_context
                )
                generated_member = generated_members.get(binding_path)
                if generated_member:
                    add_existing_node(generated_member)
                    add_edge(
                        owner_nid,
                        generated_member["id"],
                        "references",
                        line_for(value),
                        context=binding_context,
                        confidence="INFERRED",
                    )
            if binding_converter:
                converter_nid = _make_id("binding_converter", binding_converter)
                add_node(converter_nid, binding_converter, line_for(value), file_type="concept")
                add_edge(
                    owner_nid,
                    converter_nid,
                    "references",
                    line_for(value),
                    context="binding_converter",
                )
            if elem_type == "Binding" and attr_local == "Path":
                direct_path = value.strip()
                if direct_path and "{" not in direct_path and "}" not in direct_path:
                    bind_nid = _make_id("binding", direct_path)
                    add_node(bind_nid, direct_path, line_for(value), file_type="concept")
                    add_edge(
                        owner_nid, bind_nid, "references", line_for(value), context="binding_path"
                    )
            if elem_type == "Binding" and attr_local == "Converter":
                direct_converter = _xaml_static_resource_key(value)
                if direct_converter:
                    converter_nid = _make_id("binding_converter", direct_converter)
                    add_node(converter_nid, direct_converter, line_for(value), file_type="concept")
                    add_edge(
                        owner_nid,
                        converter_nid,
                        "references",
                        line_for(value),
                        context="binding_converter",
                    )

    return {"nodes": nodes, "edges": edges}


_DISPATCH: dict[str, Any] = {
    ".py": extract_python,
    ".js": extract_js,
    ".jsx": extract_js,
    ".mjs": extract_js,
    ".cjs": extract_js,
    ".ts": extract_js,
    ".tsx": extract_js,
    ".mts": extract_js,
    ".cts": extract_js,
    ".go": extract_go,
    ".rs": extract_rust,
    ".java": extract_java,
    ".groovy": extract_groovy,
    ".gradle": extract_groovy,
    ".c": extract_c,
    ".h": extract_c,
    ".cpp": extract_cpp,
    ".cc": extract_cpp,
    ".cxx": extract_cpp,
    ".hpp": extract_cpp,
    ".cu": extract_cpp,
    ".cuh": extract_cpp,
    ".metal": extract_cpp,
    ".rb": extract_ruby,
    ".rake": extract_ruby,
    ".cs": extract_csharp,
    ".kt": extract_kotlin,
    ".kts": extract_kotlin,
    ".scala": extract_scala,
    ".php": extract_php,
    ".swift": extract_swift,
    ".lua": extract_lua,
    ".luau": extract_lua,
    ".toc": extract_lua,
    ".zig": extract_zig,
    ".ps1": extract_powershell,
    ".psm1": extract_powershell,
    ".psd1": extract_powershell_manifest,
    ".ex": extract_elixir,
    ".exs": extract_elixir,
    ".m": extract_objc,
    ".mm": extract_objc,
    ".jl": extract_julia,
    ".f": extract_fortran,
    ".F": extract_fortran,
    ".f90": extract_fortran,
    ".F90": extract_fortran,
    ".f95": extract_fortran,
    ".F95": extract_fortran,
    ".f03": extract_fortran,
    ".F03": extract_fortran,
    ".f08": extract_fortran,
    ".F08": extract_fortran,
    ".vue": extract_vue,
    ".svelte": extract_svelte,
    ".astro": extract_astro,
    ".dart": extract_dart,
    ".v": extract_verilog,
    ".sv": extract_verilog,
    ".svh": extract_verilog,
    ".sql": extract_sql,
    ".md": extract_markdown,
    ".mdx": extract_markdown,
    ".qmd": extract_markdown,
    ".skill": extract_markdown,
    ".pas": extract_pascal,
    ".pp": extract_pascal,
    ".dpr": extract_pascal,
    ".dpk": extract_pascal,
    ".lpr": extract_pascal,
    ".inc": extract_pascal,
    ".dfm": extract_delphi_form,
    ".lfm": extract_lazarus_form,
    ".lpk": extract_lazarus_package,
    ".sh": extract_bash,
    ".bash": extract_bash,
    ".json": extract_json,
    ".tf": extract_terraform,
    ".tfvars": extract_terraform,
    ".hcl": extract_terraform,
    ".dm": extract_dm,
    ".dme": extract_dm,
    ".dmi": extract_dmi,
    ".dmm": extract_dmm,
    ".dmf": extract_dmf,
    ".sln": extract_sln,
    ".slnx": extract_slnx,
    ".csproj": extract_csproj,
    ".fsproj": extract_csproj,
    ".vbproj": extract_csproj,
    ".xaml": extract_xaml,
    ".razor": extract_razor,
    ".cshtml": extract_razor,
    ".cls": extract_apex,
    ".trigger": extract_apex,
}


_EXTRA_FOR_EXTENSION = {
    ".sql": "sql",
    ".tf": "terraform",
    ".tfvars": "terraform",
    ".hcl": "terraform",
    ".dm": "dm",
    ".dme": "dm",
}


_SHEBANG_DISPATCH: dict[str, Any] = {
    "python": extract_python,
    "python2": extract_python,
    "python3": extract_python,
    "bash": extract_bash,
    "sh": extract_bash,
    "dash": extract_bash,
    "zsh": extract_bash,
    "ksh": extract_bash,
    "node": extract_js,
    "nodejs": extract_js,
    "ruby": extract_ruby,
    "lua": extract_lua,
    "php": extract_php,
    "julia": extract_julia,
}


_OBJC_HEADER_MARKERS = (b"@interface", b"@protocol", b"@implementation", b"@import", b"#import")


def _is_objc_header(path: Path) -> bool:
    """Whether a `.h` file is Objective-C rather than C/C++ (#1475).

    `.h` is shared by C, C++, and ObjC; the suffix map routes it to extract_c,
    which silently drops every @interface/@protocol/@property/method (1 node, 0
    edges). Sniffing for an ObjC-only directive reroutes genuine ObjC headers to
    extract_objc while leaving every C/C++ header on its existing extractor.
    """
    try:
        head = path.read_bytes()[: 256 * 1024]
    except OSError:
        return False
    return any(marker in head for marker in _OBJC_HEADER_MARKERS)


_CPP_HEADER_MARKERS = (
    b"class ",
    b"namespace ",
    b"template",
    b"::",
    b"public:",
    b"private:",
    b"protected:",
)


def _is_objc_source(path: Path) -> bool:
    """Whether a `.m` file is Objective-C rather than MATLAB/Octave (#1702).

    `.m` is shared by Objective-C implementation files and MATLAB (also Octave).
    The suffix map routes `.m` to extract_objc unconditionally, which force-parses
    MATLAB through the Objective-C tree-sitter grammar and emits garbage nodes/edges
    (worse than skipping). A genuine ObjC `.m` always carries an ObjC directive
    (@implementation/@interface/@import/#import); MATLAB has none of them. Reuses
    the same marker set as the `.h` sniff. `.mm` is unambiguously Objective-C++ and
    is not sniffed.
    """
    return _is_objc_header(path)


def _is_cpp_header(path: Path) -> bool:
    """Whether a `.h` file is C++ rather than plain C (#1547).

    Mirrors `_is_objc_header`: sniffs for a C++-only token. Used only to reroute
    a `.h` from extract_c to extract_cpp when no ObjC marker is present (ObjC has
    priority). Conservative by construction — a plain C header matches nothing
    here and keeps its existing extract_c routing.
    """
    try:
        head = path.read_bytes()[: 256 * 1024]
    except OSError:
        return False
    return any(marker in head for marker in _CPP_HEADER_MARKERS)


def _get_extractor(path: Path) -> Any | None:
    """Return the correct extractor function for a file, or None if unsupported."""
    if path.name.lower().endswith(".blade.php"):
        return extract_blade
    if is_mcp_config_path(path):
        return extract_mcp_config
    if is_package_manifest_path(path):
        return extract_package_manifest
    suffix = path.suffix
    if suffix not in _DISPATCH and suffix.lower() in _DISPATCH:
        suffix = suffix.lower()
    if suffix == ".h":
        if _is_objc_header(path):
            return extract_objc
        if _is_cpp_header(path):
            return extract_cpp
    if suffix == ".m" and not _is_objc_source(path):
        return None
    # Without this, detect labels e.g. `#!/usr/bin/env bash` CLIs as code but
    if not suffix:
        from graphify.detect import _shebang_interpreter

        interp = _shebang_interpreter(path)
        if interp is not None:
            return _SHEBANG_DISPATCH.get(interp)
    return _DISPATCH.get(suffix)


def _safe_extract_with_xaml_root(extractor, path: Path, root: Path) -> dict:
    global _XAML_ACTIVE_EXTRACT_ROOT
    previous_root = _XAML_ACTIVE_EXTRACT_ROOT
    _XAML_ACTIVE_EXTRACT_ROOT = root.resolve()
    try:
        return _safe_extract(extractor, path)
    finally:
        _XAML_ACTIVE_EXTRACT_ROOT = previous_root


def _extract_single_file(args: tuple) -> tuple[int, dict]:
    """Worker function for parallel extraction. Runs in a subprocess.

    Must be at module level (not a closure) so it can be pickled by
    ProcessPoolExecutor.

    Args:
        args: (index, path_str, root_str, cache_location_str) tuple. ``root``
            anchors hash keys / node ids / the XAML boundary; ``cache_location``
            is where the cache dir is written, decoupled per #1774. A legacy
            3-tuple (no cache_location) is still accepted for back-compat.

    Returns:
        (index, result_dict) so results can be placed back in order.
    """
    if len(args) == 4:
        idx, path_str, root_str, cache_location_str = args
    else:
        idx, path_str, root_str = args
        cache_location_str = root_str
    path = Path(path_str)
    root = Path(root_str)
    cache_location = Path(cache_location_str)
    _raise_recursion_limit()
    bypass_cache = path.suffix in _JS_CACHE_BYPASS_SUFFIXES

    if not bypass_cache:
        cached = load_cached(path, root, cache_root=cache_location)
        if cached is not None:
            return idx, cached

    extractor = _get_extractor(path)
    if extractor is None:
        return idx, {"nodes": [], "edges": []}

    result = _safe_extract_with_xaml_root(extractor, path, root)
    if not bypass_cache and "error" not in result and result.get("nodes"):
        save_cached(path, result, root, cache_root=cache_location)
    return idx, result


def _extract_parallel(
    uncached_work: list[tuple[int, Path]],
    per_file: list[dict | None],
    root: Path,
    max_workers: int | None,
    total_files: int,
    cache_location: Path | None = None,
) -> bool:
    """Extract uncached files in parallel using ProcessPoolExecutor.

    Returns True if the pool ran to completion. Returns False if the pool
    failed in a recoverable way (typically Windows-spawn without an
    ``if __name__ == "__main__"`` guard in the calling script, which causes
    BrokenProcessPool); the caller should fall back to sequential extraction.
    """
    import concurrent.futures

    if max_workers is None:
        env_raw = os.environ.get("GRAPHIFY_MAX_WORKERS", "").strip()
        env_cap = None
        if env_raw:
            try:
                v = int(env_raw)
                if v > 0:
                    env_cap = v
            except ValueError:
                pass
        cpu_cap = env_cap if env_cap is not None else (os.cpu_count() or 4)
        max_workers = min(cpu_cap, len(uncached_work))

    if sys.platform == "win32":
        max_workers = min(max_workers, 61)
    max_workers = max(max_workers, 1)

    if max_workers == 1:
        return False

    root_str = str(root)
    cache_loc_str = str(cache_location if cache_location is not None else root)
    work_items = [(idx, str(path), root_str, cache_loc_str) for idx, path in uncached_work]

    done_count = 0
    _PROGRESS_INTERVAL = 100
    try:
        with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_extract_single_file, item): pos for pos, item in enumerate(work_items)
            }
            for future in concurrent.futures.as_completed(futures):
                try:
                    idx, result = future.result()
                    per_file[idx] = result
                except Exception as exc:
                    pos = futures[future]
                    print(
                        f"  warning: worker failed for {work_items[pos][1]}: {exc}",
                        file=sys.stderr,
                        flush=True,
                    )
                done_count += 1
                if total_files >= _PROGRESS_INTERVAL and done_count % _PROGRESS_INTERVAL == 0:
                    print(
                        f"  AST extraction: {done_count}/{len(uncached_work)} uncached files "
                        f"({done_count * 100 // len(uncached_work)}%) [{max_workers} workers]",
                        flush=True,
                    )
    except concurrent.futures.process.BrokenProcessPool:
        print(
            "  warning: parallel extraction failed (BrokenProcessPool); "
            "falling back to sequential. On Windows this usually means the "
            'caller is missing an `if __name__ == "__main__":` guard. Pass '
            "parallel=False to extract() to skip the pool entirely.",
            flush=True,
        )
        return False
    if total_files >= _PROGRESS_INTERVAL:
        _done = len(uncached_work)
        print(
            f"  AST extraction: {_done}/{_done} uncached files (100%) [{max_workers} workers]",
            flush=True,
        )
    return True


def _extract_sequential(
    uncached_work: list[tuple[int, Path]],
    per_file: list[dict | None],
    root: Path,
    total_files: int,
    cache_location: Path | None = None,
) -> None:
    """Extract uncached files sequentially (fallback for small batches)."""
    _PROGRESS_INTERVAL = 100
    for work_idx, (idx, path) in enumerate(uncached_work):
        if (
            total_files >= _PROGRESS_INTERVAL
            and work_idx % _PROGRESS_INTERVAL == 0
            and work_idx > 0
        ):
            print(
                f"  AST extraction: {work_idx}/{len(uncached_work)} uncached files ({work_idx * 100 // len(uncached_work)}%)",
                flush=True,
            )
        extractor = _get_extractor(path)
        if extractor is None:
            per_file[idx] = {"nodes": [], "edges": []}
            continue
        bypass_cache = path.suffix in _JS_CACHE_BYPASS_SUFFIXES
        result = _safe_extract_with_xaml_root(extractor, path, root)
        if not bypass_cache and "error" not in result and result.get("nodes"):
            save_cached(path, result, root, cache_root=cache_location)
        per_file[idx] = result
    if total_files >= _PROGRESS_INTERVAL:
        _done = len(uncached_work)
        print(f"  AST extraction: {_done}/{_done} uncached files (100%)", flush=True)


_PARALLEL_THRESHOLD = 20


def extract(
    paths: list[Path],
    cache_root: Path | None = None,
    *,
    root: Path | None = None,
    parallel: bool = True,
    max_workers: int | None = None,
) -> dict:
    """Extract AST nodes and edges from a list of code files.

    Two-pass process:
    1. Per-file structural extraction (classes, functions, imports)
    2. Cross-file import resolution: turns file-level imports into
       class-level INFERRED edges (DigestAuth --uses--> Response)

    Args:
        paths: files to extract from
        root: explicit anchor for source_file relativization, node ids, and
            symbol resolution. Pass the SCAN root whenever the cache lives
            somewhere else (`--out`); without it the anchor falls back to
            cache_root and every scanned file reads as out-of-root (#1941).
        cache_root: explicit root for graphify-out/cache/ (overrides the
            inferred common path prefix). Pass Path('.') when running on a
            subdirectory so the cache stays at ./graphify-out/cache/.
            Anchors ids/source_file only as a fallback when `root` is unset.
        parallel: if True and there are >= _PARALLEL_THRESHOLD uncached files,
            use ProcessPoolExecutor for multi-core extraction.
        max_workers: max subprocess count. Defaults to cpu_count (or the
            value of GRAPHIFY_MAX_WORKERS if set), bounded by len(uncached_work).
    """
    paths = [Path(p) for p in paths]
    anchor_root = Path(root) if root is not None else None
    _check_tree_sitter_version()
    _raise_recursion_limit()
    _WORKSPACE_PACKAGE_CACHE.clear()
    _XAML_CSHARP_CLASS_CACHE.clear()

    try:
        if not paths:
            root = Path(".")
        elif len(paths) == 1:
            root = paths[0].parent
        else:
            min_parts = min(len(p.parts) for p in paths)
            common_len = 0
            for i in range(min_parts):
                if len({p.parts[i] for p in paths}) == 1:
                    common_len += 1
                else:
                    break
            root = Path(*paths[0].parts[:common_len]) if common_len else Path(".")
    except Exception:
        root = Path(".")
    if anchor_root is not None:
        root = anchor_root
    elif cache_root is not None:
        root = cache_root
    root = root.resolve()

    cache_location = (cache_root if cache_root is not None else Path(".")).resolve()
    total = len(paths)

    per_file: list[dict | None] = [None] * total
    uncached_work: list[tuple[int, Path]] = []

    for i, path in enumerate(paths):
        if _get_extractor(path) is None:
            per_file[i] = {"nodes": [], "edges": []}
            continue
        bypass_cache = path.suffix in _JS_CACHE_BYPASS_SUFFIXES
        if not bypass_cache:
            cached = load_cached(path, root, cache_root=cache_location)
            if cached is not None:
                per_file[i] = cached
                continue
        uncached_work.append((i, path))

    if uncached_work:
        ran_parallel = False
        if parallel and len(uncached_work) >= _PARALLEL_THRESHOLD:
            ran_parallel = _extract_parallel(
                uncached_work, per_file, root, max_workers, total, cache_location
            )
        if not ran_parallel:
            _extract_sequential(uncached_work, per_file, root, total, cache_location)

    for i in range(total):
        if per_file[i] is None:
            per_file[i] = {"nodes": [], "edges": []}

    _empty_sources: list[str] = []
    for i, _p in enumerate(paths):
        _res = per_file[i] or {}
        if _res.get("nodes") or _res.get("error"):
            continue
        if _get_extractor(_p) is not None:
            _empty_sources.append(str(_p))
    if _empty_sources:
        _shown = ", ".join(Path(x).name for x in _empty_sources[:5])
        _more = f" (+{len(_empty_sources) - 5} more)" if len(_empty_sources) > 5 else ""
        print(
            f"  warning: {len(_empty_sources)} source file(s) produced zero nodes and "
            f"are absent from the graph: {_shown}{_more}. A re-run will retry them "
            f"(empties are no longer cached); if it persists, please report the "
            f"file(s) (#1666).",
            file=sys.stderr,
            flush=True,
        )

    from graphify.detect import CODE_EXTENSIONS as _CODE_EXTS

    _no_extractor: dict[str, int] = {}
    for _p in paths:
        _ext = _p.suffix.lower()
        if _ext in _CODE_EXTS and _get_extractor(_p) is None:
            _no_extractor[_ext] = _no_extractor.get(_ext, 0) + 1
    if _no_extractor:
        _by_count = ", ".join(
            f"{ext} ({n})"
            for ext, n in sorted(_no_extractor.items(), key=lambda kv: (-kv[1], kv[0]))
        )
        _tot = sum(_no_extractor.values())
        print(
            f"  warning: {_tot} file(s) are classified as code but graphify has no AST "
            f"extractor for their language, so they contributed nothing to the graph: "
            f"{_by_count}. Please open an issue to request support for these (#1689).",
            file=sys.stderr,
            flush=True,
        )

    _missing_dep_count: dict[str, int] = {}
    _missing_dep_error: dict[str, str] = {}
    for i, _p in enumerate(paths):
        _err = (per_file[i] or {}).get("error") or ""
        if "not installed" in _err:
            _ext = _p.suffix.lower()
            _missing_dep_count[_ext] = _missing_dep_count.get(_ext, 0) + 1
            _missing_dep_error.setdefault(_ext, _err)
    for _ext, _n in sorted(_missing_dep_count.items(), key=lambda kv: (-kv[1], kv[0])):
        _extra = _EXTRA_FOR_EXTENSION.get(_ext)
        if _extra:
            _reason = _missing_dep_error[_ext].split(". ")[0]
            _hint = f' Install it with: pip install "graphifyy[{_extra}]"'
        else:
            _reason = _missing_dep_error[_ext]
            _hint = ""
        print(
            f"  warning: {_n} {_ext} file(s) contributed nothing to the graph "
            f"because a dependency is missing: {_reason}.{_hint} (#1745)",
            file=sys.stderr,
            flush=True,
        )

    all_nodes: list[dict] = []
    all_edges: list[dict] = []
    all_raw_calls: list[dict] = []
    for result in per_file:
        all_nodes.extend(result.get("nodes", []))
        all_edges.extend(result.get("edges", []))
        all_raw_calls.extend(result.get("raw_calls", []))
    callable_nids: set[str] = set()

    _augment_symbol_resolution_edges(paths, all_nodes, all_edges, root)

    _merge_decl_def_classes(all_nodes, all_edges)

    id_remap: dict[str, str] = {}
    prefix_remap: dict[Path, list[tuple[str, str]]] = {}
    stem_forms: dict[Path, tuple[str, list[str]]] = {}
    remap_paths: list[Path] = list(paths)
    _remap_seen: set[Path] = set()
    for _p in paths:
        try:
            _remap_seen.add(_p.resolve())
        except (OSError, RuntimeError):
            pass
    for _e in all_edges:
        _tf = _e.get("target_file")
        if not _tf:
            continue
        try:
            _tp = Path(_tf).resolve()
        except (OSError, RuntimeError):
            continue
        if _tp in _remap_seen:
            continue
        _remap_seen.add(_tp)
        try:
            _tp.relative_to(root)
        except ValueError:
            continue
        try:
            if not _tp.is_file():
                continue
        except OSError:
            continue
        remap_paths.append(_tp)
    for path in remap_paths:
        old_id = _make_id(str(path))
        try:
            rel = path.relative_to(root)
        except ValueError:
            try:
                rel = path.resolve().relative_to(root)
            except ValueError:
                continue
        new_id = _file_node_id(rel)
        if old_id != new_id:
            id_remap[old_id] = new_id
        old_id_abs = _make_id(str(path.resolve()))
        if old_id_abs != new_id:
            id_remap[old_id_abs] = new_id
        old_prefs: list[tuple[str, str]] = []
        old_pref = _file_node_id(path)
        if old_pref != new_id:
            old_prefs.append((old_pref, new_id))
        old_pref_abs = _file_node_id(path.resolve())
        if old_pref_abs != new_id and old_pref_abs != old_pref:
            old_prefs.append((old_pref_abs, new_id))
        if old_prefs:
            prefix_remap[path.resolve()] = old_prefs
        stem_forms[path.resolve()] = (new_id, [old_pref_abs, old_pref, new_id])
    if id_remap:
        for n in all_nodes:
            if n.get("id") in id_remap:
                n["id"] = id_remap[n["id"]]
        for e in all_edges:
            if e.get("source") in id_remap:
                e["source"] = id_remap[e["source"]]
            if e.get("target") in id_remap:
                e["target"] = id_remap[e["target"]]
    if prefix_remap:
        sym_remap: dict[str, str] = {}
        edge_alias_candidates: dict[str, set[str]] = {}
        for n in all_nodes:
            sf = n.get("source_file")
            if not sf:
                continue
            if n.get("type") == "package":
                continue
            try:
                entry = prefix_remap.get(Path(sf).resolve())
            except Exception:
                continue
            if entry is None:
                continue
            nid = n.get("id", "")
            canonical_nid: str | None = None
            for old_pref, new_pref in entry:
                if nid.startswith(old_pref + "_"):
                    canonical_nid = new_pref + nid[len(old_pref) :]
                    if canonical_nid != nid:
                        sym_remap[nid] = canonical_nid
                    break
                if nid.startswith(new_pref + "_"):
                    canonical_nid = nid
                    break
            if canonical_nid is None:
                continue
            for old_pref, new_pref in entry:
                if not canonical_nid.startswith(new_pref + "_"):
                    continue
                old_nid = old_pref + canonical_nid[len(new_pref) :]
                if old_nid != canonical_nid:
                    edge_alias_candidates.setdefault(old_nid, set()).add(canonical_nid)
        if sym_remap:
            for n in all_nodes:
                if n.get("id") in sym_remap:
                    n["id"] = sym_remap[n["id"]]
            for e in all_edges:
                if e.get("source") in sym_remap:
                    e["source"] = sym_remap[e["source"]]
                if e.get("target") in sym_remap:
                    e["target"] = sym_remap[e["target"]]
            for rc in all_raw_calls:
                cn = rc.get("caller_nid")
                if cn in sym_remap:
                    rc["caller_nid"] = sym_remap[cn]
        if edge_alias_candidates:

            def _edge_key(edge: dict) -> str:
                return json.dumps(
                    {k: v for k, v in edge.items() if k != "target_file"},
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                )

            edge_key_counts = Counter(_edge_key(edge) for edge in all_edges)
            owned_node_ids = {node.get("id") for node in all_nodes}
            deduped_edges: list[dict] = []
            for edge in all_edges:
                if edge.get("relation") == "re_exports":
                    candidates = edge_alias_candidates.get(edge.get("target", ""), set())
                    if len(candidates) == 1 and edge.get("target") not in owned_node_ids:
                        edge["target"] = next(iter(candidates))
                    deduped_edges.append(edge)
                    continue
                candidates = (
                    edge_alias_candidates.get(edge.get("target", ""), set())
                    if edge.get("relation") == "imports"
                    else set()
                )
                if len(candidates) == 1:
                    candidate = next(iter(candidates))
                    twin_key = _edge_key({**edge, "target": candidate})
                    if edge_key_counts[twin_key]:
                        if edge.get("target") in owned_node_ids:
                            edge_key_counts[twin_key] -= 1
                        continue
                deduped_edges.append(edge)
            all_edges[:] = deduped_edges

    if stem_forms:
        owned_ids = {n.get("id") for n in all_nodes}

        def _decompose(target: str, tf: str) -> "tuple[str, str] | None":
            try:
                forms = stem_forms.get(Path(tf).resolve())
            except (OSError, RuntimeError):
                return None
            if not forms:
                return None
            canonical, prefixes = forms
            for pref in prefixes:
                if pref and target.startswith(pref + "_"):
                    return canonical, target[len(pref) + 1 :]
            return None

        chain: dict[tuple[str, str], set] = {}

        def _resolve1(key) -> "str | None":
            targets = chain.get(key)
            return next(iter(targets)) if targets and len(targets) == 1 else None

        def _learn(e: dict) -> None:
            tf = e.get("target_file")
            if not tf or e.get("target") not in owned_ids:
                return
            dec = _decompose(e.get("target", ""), tf)
            if dec is not None:
                chain.setdefault((e.get("source"), dec[1]), set()).add(e["target"])

        for e in all_edges:
            if e.get("relation") == "re_exports":
                _learn(e)

        pending = [
            e
            for e in all_edges
            if e.get("relation") in ("re_exports", "imports")
            and e.get("target_file")
            and e.get("target") not in owned_ids
        ]
        for _ in range(8):
            progressed = False
            still: list[dict] = []
            for e in pending:
                dec = _decompose(e.get("target", ""), e["target_file"])
                resolved_target = _resolve1((dec[0], dec[1])) if dec else None
                if resolved_target is None:
                    still.append(e)
                    continue
                e["target"] = resolved_target
                if e.get("relation") == "re_exports":
                    chain.setdefault((e.get("source"), dec[1]), set()).add(resolved_target)
                progressed = True
            pending = still
            if not progressed:
                break
        for e in pending:
            dec = _decompose(e.get("target", ""), e["target_file"])
            if dec is not None:
                e["target"] = f"{dec[0]}_{dec[1]}"

    _repoint_python_package_imports(paths, all_nodes, all_edges, root)
    _merge_swift_extensions(per_file, all_nodes, all_edges)
    _disambiguate_colliding_node_ids(all_nodes, all_edges, all_raw_calls, root)
    _canonicalize_csharp_namespace_nodes(all_nodes, all_edges)
    _php_exts = {".php", ".phtml", ".php3", ".php4", ".php5", ".php7", ".phps"}
    _php_sel = [
        (r, p)
        for r, p in zip(per_file, paths)
        if p.suffix.lower() in _php_exts and not p.name.lower().endswith(".blade.php")
    ]
    if _php_sel:
        try:
            _resolve_php_type_references(
                [r for r, _ in _php_sel], [p for _, p in _php_sel], all_nodes, all_edges
            )
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning(
                "PHP type-reference resolution failed, skipping: %s", exc
            )
    _rewire_unique_stub_nodes(all_nodes, all_edges)

    py_paths = [p for p in paths if p.suffix == ".py"]
    if py_paths:
        py_results = [r for r, p in zip(per_file, paths) if p.suffix == ".py"]
        try:
            cross_file_edges = _resolve_cross_file_imports(py_results, py_paths)
            all_edges.extend(cross_file_edges)
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning(
                "Cross-file import resolution failed, skipping: %s", exc
            )

    java_paths = [p for p in paths if p.suffix == ".java"]
    if java_paths:
        java_results = [r for r, p in zip(per_file, paths) if p.suffix == ".java"]
        try:
            all_edges.extend(_resolve_cross_file_java_imports(java_results, java_paths))
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning(
                "Java cross-file import resolution failed, skipping: %s", exc
            )
        try:
            _resolve_java_type_references(java_results, java_paths, all_nodes, all_edges)
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning(
                "Java type-reference resolution failed, skipping: %s", exc
            )

    cs_paths = [p for p in paths if p.suffix == ".cs"]
    if cs_paths:
        cs_results = [r for r, p in zip(per_file, paths) if p.suffix == ".cs"]
        try:
            _resolve_csharp_type_references(cs_results, cs_paths, all_nodes, all_edges)
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning(
                "C# type-reference resolution failed, skipping: %s", exc
            )
        try:
            _resolve_cross_file_csharp_imports(cs_results, cs_paths, all_nodes, all_edges)
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning(
                "C# cross-file import resolution failed, skipping: %s", exc
            )

    # _SHEBANG_DISPATCH routes a `#!/usr/bin/env bash` file with no extension to
    def _looks_like_bash(result: object) -> bool:
        if not isinstance(result, dict):
            return False
        nodes = result.get("nodes")
        if not isinstance(nodes, list):
            return False
        for n in nodes:
            if not isinstance(n, dict):
                continue
            md = n.get("metadata")
            if isinstance(md, dict) and md.get("language") == "bash":
                return True
        return False

    sh_pairs = [
        (r, p)
        for r, p in zip(per_file, paths)
        if p.suffix in (".sh", ".bash") or _looks_like_bash(r)
    ]
    if sh_pairs:
        sh_results = [r for r, _ in sh_pairs]
        sh_paths = [p for _, p in sh_pairs]
        try:
            all_edges.extend(
                resolve_bash_source_edges(sh_results, sh_paths, root, existing_edges=all_edges)
            )
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning(
                "Bash cross-file call resolution failed, skipping: %s", exc
            )

    global_label_to_nids: dict[str, list[str]] = {}
    global_label_to_nids_ci: dict[str, list[str]] = {}
    for n in all_nodes:
        if n.get("file_type") == "rationale" or n.get("type") == "namespace":
            continue
        raw = n.get("label", "")
        normalised = raw.strip("()").lstrip(".")
        if normalised:
            global_label_to_nids.setdefault(normalised, []).append(n["id"])
            if _lang_is_case_insensitive(n.get("source_file")):
                global_label_to_nids_ci.setdefault(normalised.lower(), []).append(n["id"])

    callable_nids = {n["id"] for n in all_nodes if n.get("_callable")}
    class_nids = {n["id"] for n in all_nodes if n.get("_callable_class")}

    file_to_symbol_imports: dict[str, set[str]] = {}
    file_to_module_imports: dict[str, set[str]] = {}
    for e in all_edges:
        if e.get("relation") == "imports":
            file_to_symbol_imports.setdefault(e["source"], set()).add(e["target"])
        elif e.get("relation") == "imports_from":
            file_to_module_imports.setdefault(e["source"], set()).add(e["target"])

    sf_to_file_nid: dict[str, str] = {}
    for n in all_nodes:
        sf = n.get("source_file")
        if sf and n.get("label") == Path(str(sf)).name:
            sf_to_file_nid.setdefault(str(sf), n["id"])
    nid_to_file_nid: dict[str, str] = {}
    nid_to_source_file: dict[str, str] = {}
    for n in all_nodes:
        sf = n.get("source_file")
        if not sf:
            continue
        nid_to_source_file[n["id"]] = str(sf)
        fnid = sf_to_file_nid.get(str(sf))
        if fnid is not None:
            nid_to_file_nid[n["id"]] = fnid
            continue
        sf_path = Path(sf)
        try:
            sf_rel = sf_path.relative_to(root) if sf_path.is_absolute() else sf_path
        except ValueError:
            sf_rel = sf_path
        nid_to_file_nid[n["id"]] = _file_node_id(sf_rel)

    existing_pairs = {(e["source"], e["target"]) for e in all_edges}
    call_like_pairs = {
        (e["source"], e["target"])
        for e in all_edges
        if e.get("relation") in ("calls", "indirect_call")
    }
    _JS_TS_CALL_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
    for rc in all_raw_calls:
        callee = rc.get("callee", "")
        if not callee:
            continue
        if callee in _LANGUAGE_BUILTIN_GLOBALS:
            continue
        if rc.get("is_member_call"):
            continue
        if rc.get("is_mixin"):
            continue
        if rc.get("language") == "bash":
            continue
        candidates = global_label_to_nids.get(callee, [])
        if not candidates and _lang_is_case_insensitive(rc.get("source_file")):
            candidates = global_label_to_nids_ci.get(callee.lower(), [])
        if not candidates:
            continue
        caller_family = _lang_family(rc.get("source_file"))
        if caller_family is not None:
            candidates = [
                c
                for c in candidates
                if (candidate_family := _lang_family(nid_to_source_file.get(c))) is None
                or candidate_family == caller_family
            ]
            if not candidates:
                continue
        caller = rc["caller_nid"]
        caller_file_nid = sf_to_file_nid.get(str(rc.get("source_file", ""))) or nid_to_file_nid.get(
            caller
        )
        imported_symbols = file_to_symbol_imports.get(caller_file_nid, set())
        imported_modules = file_to_module_imports.get(caller_file_nid, set())

        def _has_import_evidence(candidate_id: str) -> bool:
            candidate_file_nid = nid_to_file_nid.get(candidate_id)
            return candidate_id in imported_symbols or (
                candidate_file_nid is not None and candidate_file_nid in imported_modules
            )

        if len(candidates) == 1:
            tgt = candidates[0]
            has_import_evidence = _has_import_evidence(tgt)
        else:
            symbol_matches = [c for c in candidates if c in imported_symbols]
            if len(symbol_matches) == 1:
                tgt = symbol_matches[0]
                has_import_evidence = True
            else:
                module_matches = [
                    c
                    for c in candidates
                    if (cf := nid_to_file_nid.get(c)) is not None and cf in imported_modules
                ]
                if len(module_matches) == 1:
                    tgt = module_matches[0]
                    has_import_evidence = True
                else:
                    tgt = disambiguate_ambiguous_candidates(
                        candidates,
                        {c: nid_to_source_file.get(c, "") for c in candidates},
                        rc.get("source_file", ""),
                    )
                    if tgt is None:
                        continue
                    has_import_evidence = False
        if rc.get("indirect"):
            if (
                tgt != caller
                and (caller, tgt) not in call_like_pairs
                and tgt in callable_nids
                and tgt not in class_nids
            ):
                call_like_pairs.add((caller, tgt))
                all_edges.append(
                    {
                        "source": caller,
                        "target": tgt,
                        "relation": "indirect_call",
                        "context": rc.get("context", "argument"),
                        "confidence": "INFERRED",
                        "confidence_score": 0.8,
                        "source_file": rc.get("source_file", ""),
                        "source_location": rc.get("source_location"),
                        "weight": 1.0,
                    }
                )
            continue
        if not has_import_evidence and str(rc.get("source_file", "")).endswith(
            _JS_TS_CALL_SUFFIXES
        ):
            continue
        if tgt != caller and (caller, tgt) not in existing_pairs:
            existing_pairs.add((caller, tgt))
            if has_import_evidence:
                confidence = "EXTRACTED"
                confidence_score = 1.0
            else:
                confidence = "INFERRED"
                confidence_score = 0.8
            all_edges.append(
                {
                    "source": caller,
                    "target": tgt,
                    "relation": "calls",
                    "context": "call",
                    "confidence": confidence,
                    "confidence_score": confidence_score,
                    "source_file": rc.get("source_file", ""),
                    "source_location": rc.get("source_location"),
                    "weight": 1.0,
                }
            )

    run_language_resolvers(paths, per_file, all_nodes, all_edges)

    def _portable_out_of_root_sf(p: Path) -> str:
        try:
            rel = os.path.relpath(str(p), str(root)).replace("\\", "/")
        except ValueError:
            return p.name
        updepth = 0
        for seg in rel.split("/"):
            if seg == "..":
                updepth += 1
            else:
                break
        return p.name if updepth > 3 else rel

    ext_id_remap: dict[str, str] = {}
    for item in all_nodes + all_edges:
        sf = item.get("source_file")
        if not sf:
            continue
        sf_path = Path(sf)
        if not sf_path.is_absolute():
            continue
        try:
            rel = sf_path.relative_to(root)
        except ValueError:
            pass
        else:
            if item.get("id") == _make_id(str(sf_path)):
                ext_id_remap[item["id"]] = _file_node_id(rel)
            item["source_file"] = rel.as_posix()
            continue
        portable = _portable_out_of_root_sf(sf_path)
        if "id" in item and item.get("id") == _make_id(str(sf_path)):
            ext_id_remap[item["id"]] = _make_id("ext", portable)
        item["source_file"] = portable

    if ext_id_remap:
        for n in all_nodes:
            if n.get("id") in ext_id_remap:
                n["id"] = ext_id_remap[n["id"]]
        for e in all_edges:
            if e.get("source") in ext_id_remap:
                e["source"] = ext_id_remap[e["source"]]
            if e.get("target") in ext_id_remap:
                e["target"] = ext_id_remap[e["target"]]

    for n in all_nodes:
        n.pop("origin_file", None)
        n.pop("_callable", None)
        n.pop("_callable_class", None)

    for e in all_edges:
        e.pop("local_alias", None)

    for n in all_nodes:
        n["_origin"] = "ast"
    for e in all_edges:
        e["_origin"] = "ast"

    return {
        "nodes": all_nodes,
        "edges": all_edges,
        "input_tokens": 0,
        "output_tokens": 0,
    }


def collect_files(
    target: Path, *, follow_symlinks: bool = False, root: Path | None = None
) -> list[Path]:
    containment_root = root if root is not None else target
    from graphify.detect import _resolves_under_root

    if target.is_file():
        return [target] if _resolves_under_root(target, containment_root) else []
    _EXTENSIONS = set(_DISPATCH.keys())
    from graphify.detect import _is_ignored, _is_noise_dir, _load_graphifyignore

    ignore_root = root if root is not None else target
    patterns = _load_graphifyignore(ignore_root)
    ignore_cache: dict[Path, bool] = {}

    def _ignored(p: Path) -> bool:
        return bool(patterns and _is_ignored(p, ignore_root, patterns, _cache=ignore_cache))

    if not follow_symlinks:
        if any(_is_noise_dir(part) for part in target.parts):
            return []
        has_negation = any(pat.startswith("!") for _, pat in patterns)
        results: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(target):
            dp = Path(dirpath)
            dirnames[:] = [
                d
                for d in dirnames
                if not _is_noise_dir(d, dp) and (has_negation or not _ignored(dp / d))
            ]
            for fname in filenames:
                p = dp / fname
                suffix = p.suffix
                if (
                    (suffix in _EXTENSIONS or suffix.lower() in _EXTENSIONS)
                    and not _ignored(p)
                    and _resolves_under_root(p, containment_root)
                ):
                    results.append(p)
        return sorted(results)
    results = []
    for dirpath, dirnames, filenames in os.walk(target, followlinks=True):
        if os.path.islink(dirpath):
            real = os.path.realpath(dirpath)
            parent_real = os.path.realpath(os.path.dirname(dirpath))
            if parent_real == real or parent_real.startswith(real + os.sep):
                dirnames.clear()
                continue
        dp = Path(dirpath)
        dirnames[:] = [
            d
            for d in dirnames
            if not _is_noise_dir(d, dp)
            and (not (dp / d).is_symlink() or _resolves_under_root(dp / d, containment_root))
        ]
        for fname in filenames:
            p = dp / fname
            suffix = p.suffix
            if (
                (suffix in _EXTENSIONS or suffix.lower() in _EXTENSIONS)
                and not _ignored(p)
                and _resolves_under_root(p, containment_root)
            ):
                results.append(p)
    return sorted(results)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m graphify.extract <file_or_dir> ...", file=sys.stderr)
        sys.exit(1)

    paths: list[Path] = []
    for arg in sys.argv[1:]:
        paths.extend(collect_files(Path(arg)))

    result = extract(paths)
    print(json.dumps(result, indent=2))
