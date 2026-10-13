# Learned query-log edge weights

Fixed relation importance in `graphify/weights.py` is the prior. `graphify learn-weights` reads a JSONL feedback log and writes `learned_weights.json` beside the graph.

Each record is `{"useful": true|false, "relations": ["calls"], "source": "user"}`. Records with `source` of `benchmark`, `eval`, `held_out`, `crosscodeeval`, `ground_truth`, or `retrieval_eval`, or with `held_out: true`, are ignored. A relation keeps its prior until it has at least 3 exposures. The update is a Beta posterior mean with strength 8, clamped to `[0.05, 1.0]`.

`graphify query --learned-weights` loads that file. A missing or short file keeps the fixed table. The MCP `query_graph` tool accepts `learned_weights`.
