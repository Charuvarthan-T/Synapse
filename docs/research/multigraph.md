# Structural and semantic multigraph

`graphify/multigraph_store.py` stores each relation between a node pair as its own edge on a NetworkX `MultiDiGraph`. Confidence, provenance (`_origin`), and the rest of the edge attributes stay on that edge.

`graphify semantic-graph` converts the graph with `ensure_multigraph` before applying relations, so a semantic `handles` edge can sit beside a structural `calls` edge. Older `secondary_relations` annotations are expanded on load and by `graphify migrate-multigraph`. A simple graph with one edge per pair still loads as a simple graph.

Retrieval uses `edge_datas`, which already returns every parallel edge. Ranking keeps the strongest relation on a pair.
