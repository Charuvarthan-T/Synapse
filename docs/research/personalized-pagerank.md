# Personalized PageRank retrieval

`graphify/pagerank_retrieval.py` runs NetworkX Personalized PageRank. Edge transition weight is the strongest relation importance on that directed pair, including parallel multigraph edges. The personalization vector is uniform over the query seed nodes. Damping is 0.85, tolerance `1e-6`, and the iteration cap is 100.

Empty seeds return no scores. A graph with no edges returns the personalization mass. The walk is deterministic for a fixed graph and seed set.

Select it with `graphify query --ppr` or MCP `query_graph` `retrieval: "ppr"`. Weighted traversal remains the default. The VS Code setting `synapse.retrieval` can be `weighted` or `ppr`.
