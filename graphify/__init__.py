"""graphify - extract · build · cluster · analyze · report."""


def __getattr__(name):
    _map = {
        "extract": ("graphify.extract", "extract"),
        "collect_files": ("graphify.extract", "collect_files"),
        "build_from_json": ("graphify.build", "build_from_json"),
        "cluster": ("graphify.cluster", "cluster"),
        "score_all": ("graphify.cluster", "score_all"),
        "cohesion_score": ("graphify.cluster", "cohesion_score"),
        "god_nodes": ("graphify.analyze", "god_nodes"),
        "surprising_connections": ("graphify.analyze", "surprising_connections"),
        "suggest_questions": ("graphify.analyze", "suggest_questions"),
        "generate": ("graphify.report", "generate"),
        "to_json": ("graphify.export", "to_json"),
        "to_html": ("graphify.export", "to_html"),
        "to_svg": ("graphify.export", "to_svg"),
        "to_canvas": ("graphify.export", "to_canvas"),
        "to_wiki": ("graphify.wiki", "to_wiki"),
        "reflect": ("graphify.reflect", "reflect"),
        "save_query_result": ("graphify.ingest", "save_query_result"),
        "RelationWeightRegistry": ("graphify.weights", "RelationWeightRegistry"),
        "get_default_registry": ("graphify.weights", "get_default_registry"),
        "expand_neighborhood": ("graphify.weighted_retrieval", "expand_neighborhood"),
        "find_shortest_path": ("graphify.weighted_retrieval", "find_shortest_path"),
        "CommunityDetector": ("graphify.community_detection", "CommunityDetector"),
        "get_community_detector": ("graphify.community_detection", "get_community_detector"),
        "CommunityIndex": ("graphify.community_index", "CommunityIndex"),
        "community_aware_expand": ("graphify.community_retrieval", "community_aware_expand"),
        "SEMANTIC_RELATION_TYPES": ("graphify.semantic_relations", "SEMANTIC_RELATION_TYPES"),
        "SemanticRelation": ("graphify.semantic_relations", "SemanticRelation"),
        "parse_semantic_relations": ("graphify.semantic_relations", "parse_semantic_relations"),
        "extract_semantic_relations": (
            "graphify.semantic_extraction",
            "extract_semantic_relations",
        ),
        "apply_semantic_relations": ("graphify.semantic_graph", "apply_semantic_relations"),
    }
    if name in _map:
        import importlib

        mod_name, attr = _map[name]
        mod = importlib.import_module(mod_name)
        return getattr(mod, attr)
    raise AttributeError(f"module 'graphify' has no attribute {name!r}")
