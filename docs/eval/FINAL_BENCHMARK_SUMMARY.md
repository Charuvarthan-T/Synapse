# Final benchmark summary

Tested query code: `319511ece4d797337eaf1a0cf499dd86a2a6babe`. Original Graphify: `a9860f70dc3cc2e9c2d8cb923484cfa0592b9067` (v0.9.88). Artifacts: `tools/eval/results/`.

## Comparison

| Configuration | P@10 | R@10 | MRR | Median CLI latency |
|---|---:|---:|---:|---:|
| Original Graphify | 0.045 | 0.400 | 0.260 | 0.66 s |
| Synapse unweighted | 0.050 | 0.450 | 0.249 | 0.81 s |
| Synapse weighted (baseline) | 0.070 | 0.650 | 0.265 | 1.03 s |
| Synapse community-aware | 0.075 | 0.700 | 0.265 | 0.94 s |
| Synapse learned, no feedback file | 0.070 | 0.650 | 0.265 | 0.89 s |
| Synapse PageRank | 0.060 | 0.550 | 0.250 | 1.54 s |
| PageRank + learned + community flags | 0.060 | 0.550 | 0.252 | 1.28 s |

n = 20 questions, 260 nodes, 536 edges. Exact node ids.

## Strongest improvement

Community-aware Synapse versus upstream Graphify: Recall@10 0.70 versus 0.40 (+0.30), Precision@10 0.075 versus 0.045 (+0.030).

## Largest retrieval regression among Synapse modes

PageRank versus community-aware Synapse: Recall@10 0.55 versus 0.70. PageRank MRR (0.250) is also slightly below upstream (0.260). Combining `--ppr` with the other flags keeps the PageRank result, because `--ppr` replaces neighborhood ranking.

## Best configuration by benchmark

| Benchmark | Best measured choice |
|---|---|
| Retrieval precision and recall | Synapse `--community-aware` |
| Retrieval MRR | Synapse weighted or community-aware (both 0.265) |
| Learned weights | No retrieval change without a feedback file |
| Validation, multigraph, claim checks | Synapse only; Graphify has no equivalent score |
| CrossCodeEval | BLOCKED |
| Extension GUI | PARTIAL (package installed, screens not exercised) |

## Feature impact

- Weights and community search account for the retrieval gain over upstream.
- Learned weights moved `imports` from 0.40 to 0.657 on synthetic dev feedback and ignored a held-out benchmark record. That was not applied to the 20-question run.
- Multigraph reload kept both `calls` and `handles`.
- Validation: 7/7 conservative labels, 0 false contradictions. One bad method stayed UNKNOWN.
- Graph checks: 4/4 supported or unknown as labelled.
- Multi-root: unit tests pass. GUI switching was not clicked.

## Not completed

- CrossCodeEval (no API key).
- NDCG and MAP (labels are not graded).
- Independent re-annotation of the 20 questions.
- Leiden (Python 3.13).
- Live Ask / Search / Verify / Enrich / Graph Map.
- A fully green engine suite: 3,624 passed, 64 failed, 149 skipped. Six of those failures passed when rerun alone. The Windows pending-path failure is fixed.
- Agent token use was measured on Groq `openai/gpt-oss-20b` until the 200,000-token daily cap. Synapse full did not run. See `docs/eval/AGENT_EFFICIENCY_REPORT.md`. The earlier character estimates are not provider tokens.

## Reproduce

```text
git clone --depth 1 https://github.com/Graphify-Labs/graphify.git
python tools/eval/final_retrieval.py
python tools/eval/run_graphify_comparison.py
python tools/eval/run_local_benchmarks.py
python tools/eval/run_agent_efficiency.py
python -m pytest tests/test_agent_efficiency.py -q
python -m pytest tests -q
```

Set `GRAPHIFY_UPSTREAM` if the clone is not in the temp directory the comparison script expects.
