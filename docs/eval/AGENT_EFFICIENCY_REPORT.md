# Agent efficiency and cost

Status: **PARTIALLY COMPLETE**. The agent token, cost, tool-use, and correctness experiments are **BLOCKED**. Graph indexing, query latency, retrieval-context size, and graph reliability were measured and are not agent results.

Tested commit: `f928959a5b0be581f6749f53d5d3bfbccac1e57f`. Upstream Graphify: `https://github.com/Graphify-Labs/graphify.git` at `a9860f70dc3cc2e9c2d8cb923484cfa0592b9067`. Host: Windows, Python 3.13.5. Generated 13 October 2026. Artifacts: `tools/eval/results/agent_efficiency/`.

## What was not run

`OPENAI_API_KEY`, `GROQ_API_KEY`, and `ANTHROPIC_API_KEY` were unset. The adapter recorded 20 existing retrieval questions for each of four configurations and did not send a request:

| Configuration | Graph tool | Flags |
|---|---|---|
| No graph | no | file list and file read only |
| Original Graphify | upstream `graphify query` | none |
| Synapse default | this repo | weighted traversal; PageRank, learned weights, and community mode off |
| Synapse full | this repo | `--community-aware --learned-weights --ppr` |

Every agent field for input tokens, output tokens, cached tokens, reasoning tokens, API cost, success, and correctness is null. The JSONL stores `llm_calls: 0` with an explicit note that the adapter never started. That zero is not a measured agent workload, and it is not a token count. There is no CrossCodeEval parquet in the repository, so coding-task success was not scored either. No statistical comparison of agent success or cost is possible. The price table in `tools/eval/agent_efficiency/pricing.py` is the OpenAI standard tier published on 13 October 2026 (`gpt-4.1-mini` input $0.40, cached input $0.10, output $1.60 per million). It was not applied to a live response.

## Retrieval context, not agent success

The same frozen graph used by the earlier comparison (Synapse-extracted, 260 nodes and 536 edges in the previous report) was queried at budgets 2,000, 4,000, 8,000, and 16,000. Scoring is exact node id on the 20 questions in `tools/eval/ground_truth.json`. Context size is `len(stdout) // 4`, the same local character rule as `graphify/benchmark.py`. It is not a provider usage report. Agent success and API cost are null. n = 20 and errors = 0 for every cell below.

Recall@10 and Precision@10 did not change with budget for four configurations:

| Configuration | P@10 | R@10 | MRR | Mean local context tokens at 2,000 | at 16,000 |
|---|---:|---:|---:|---:|---:|
| Original Graphify | 0.045 | 0.400 | 0.260 | 2,726 | 5,736 |
| Synapse default | 0.070 | 0.650 | 0.265 | 1,608 | 3,675 |
| Synapse community-aware | 0.075 | 0.700 | 0.265 | 1,552 | 2,347 |
| Synapse PageRank | 0.060 | 0.550 | 0.250 | 1,629 | 2,483 |

Absolute Recall@10 difference versus upstream is +0.300 for community-aware, +0.250 for the weighted default, and +0.150 for PageRank, at every budget. Relative to upstream's 0.400, those are +75%, +62.5%, and +37.5%. Upstream's local context estimate is larger at the 2,000 budget (2,726 versus 1,552 for community-aware) while its recall is lower. Duplicate-context ratio stays near 0.02. This sample is the same 20 questions as the earlier retrieval report. It is not a held-out split and it is not a claim of statistical significance.

The concatenated seven source files estimate to 57,366 local tokens. A community-aware query at budget 2,000 estimates to 1,552. That is a smaller text payload under this character rule. It is not a measured reduction in model bill, because no model was called.

The combined flags are the exception. Their Recall@10 across the four budgets was 0.600, 0.450, 0.550, and 0.500, and 13 of 20 questions had a different top-10 list. Three immediate reruns of one question at a fixed 2,000 budget also changed the fifth node, while the header stayed `Communities=[2, 1]` and `40` nodes. `--ppr` replaces neighborhood ranking. The PageRank sort is deterministic only when the scores are identical, and those scores were not stable on this combined path. Do not read the four Recall@10 numbers as an effect of the context budget. No learned-weight file existed, so the learned-weight flag did not apply a trained table. Learned weights were not fit on these labels.

## Indexing, latency, and scale

`graphify extract --code-only --no-cluster` on copies of the source files. Peak RSS is one sample of the process, via `psutil`. API cost is null.

| Corpus | Engine | Files | Seconds | Nodes | Edges | Graph bytes | Peak RSS bytes |
|---|---|---:|---:|---:|---:|---:|---:|
| frozen_1 | Synapse | 1 | 1.03 | 13 | 29 | 11,098 | 88,621,056 |
| frozen_1 | Upstream | 1 | 1.87 | 26 | 31 | 9,589 | 26,124,288 |
| frozen_7 | Synapse | 7 | 1.88 | 259 | 512 | 208,170 | 98,435,072 |
| frozen_7 | Upstream | 7 | 2.58 | 341 | 629 | 186,017 | 26,533,888 |
| package_sample | Synapse | 25 | 4.71 | 896 | 1,959 | 803,870 | 142,598,144 |

Upstream was not run on the 25-file sample. On these copies, upstream builds a larger node set and uses less RSS, and Synapse finishes the extract sooner. The retrieval table above did not use these fresh graphs. A second Synapse extract of the seven files, with the first graph left in place and one file touched, took 1.06 s and ended at 260 nodes and 512 edges. The CLI incremental log line was not captured, so this is a wall-clock observation only.

Query latency after one discarded warmup, five repeats, one question, process startup included:

| Configuration | Median | p95 |
|---|---:|---:|
| Original Graphify | 0.71 s | 0.72 s |
| Synapse default | 0.87 s | 1.03 s |
| Synapse combined flags | 1.22 s | 1.23 s |

Synapse is slower on this query. A dollar break-even against indexing time is not computed, because there is no API cost to compare.

## Reliability probes

These are CLI and parser checks, not agent reliability.

| Probe | Result |
|---|---|
| Missing graph path | exit 1, error text names the missing file |
| Truncated JSON | exit 1, JSON parse error |
| Stale graph | extract succeeded; the new function name was absent from the next query. The query text was that new name, so a missing `alpha` hit does not show that the old symbol was dropped |
| Validation levels | Python `ast`, JavaScript `tree-sitter`, Go `tree-sitter`, unknown language `unsupported` |
| Interrupted extract | process killed, exit 1, `graph.json` was not written |
| Four concurrent queries | all exit 0 |
| Two repeated default queries | same node labels, 8 nodes |

## Feature impact on the measurements that exist

Community-aware retrieval is the best retrieval configuration in the budget sweep and the one with the smallest local context estimate at budget 2,000. Weighted default retrieval is next. PageRank alone is ahead of upstream on Recall@10 and behind community-aware and the weighted default, and it is stable across budgets. The combined flags are not a stable ranking on this graph. Learned weights have no evidence here. None of these statements is an agent-success or dollar-cost result.

## Reproduce

```text
python -m pytest tests/test_agent_efficiency.py -q
python tools/eval/run_agent_efficiency.py
```

Set `GRAPHIFY_UPSTREAM` if the upstream checkout is not in the temp directory. The frozen graph must already exist at `tools/eval/frozen_corpus/graphify-out/graph.json`. To run the blocked agent batch later, set a provider key and connect `run_scripted_agent` to that provider. The current runner refuses to mark the batch blocked if a key is present, and it does not call the provider itself.

## Artifact index

| File | Contents |
|---|---|
| `tools/eval/results/agent_efficiency/manifest.json` | Experiment status codes |
| `tools/eval/results/agent_efficiency/environment.json` | Commit, platform, provider presence, configuration text |
| `tools/eval/results/agent_efficiency/agent_tasks.jsonl` | 80 blocked agent rows |
| `tools/eval/results/agent_efficiency/agent_summary.json` | Null aggregate rates |
| `tools/eval/results/agent_efficiency/retrieval_budget_tasks.jsonl` | Per-question retrieval rows |
| `tools/eval/results/agent_efficiency/retrieval_budget_summary.json` | Aggregate retrieval and local token estimates |
| `tools/eval/results/agent_efficiency/retrieval_budget_summary.csv` | Same aggregate, flat |
| `tools/eval/results/agent_efficiency/lifecycle.json` | Index, RSS, incremental wall clock, query latency |
| `tools/eval/results/agent_efficiency/reliability.json` | Probe results |
| `tools/eval/results/agent_efficiency/naive_corpus_estimate.json` | 57,366 local tokens for seven files |
| `tools/eval/results/agent_efficiency/ablation_status.json` | Agent ablations blocked |
| `tools/eval/results/agent_efficiency/correctness_status.json` | Agent correctness blocked |
