# Agent efficiency and cost

Status: **PARTIALLY COMPLETE**. A Groq on-demand run recorded provider token counts for part of the agent benchmark. The `len(text) // 4` figures further down are text-size estimates from the earlier run. They are not token consumption.

## Provider-reported Groq run

Model: `openai/gpt-oss-20b`. This key's model list did not include `llama-3.1-8b-instant` or `llama-3.3-70b-versatile`. A probe completion returned provider usage before the tasks: 78 input tokens, 8 output tokens, 86 total, 6 reasoning tokens, and null cached tokens. The probe's rate-limit headers were 8,000 tokens and 1,000 requests. The full run then hit the on-demand daily cap of 200,000 tokens (199,060 used; the next request asked for 1,452). Billing was not enabled. Completed calls are stored as 0 USD because they ran on that free cap. The paid price table was not applied. Cached tokens were absent and stay null.

Synapse commit `b03079546644f8a55c7de0b305014cdb8ead82f7`. Upstream Graphify `a9860f70dc3cc2e9c2d8cb923484cfa0592b9067`. Same system prompt, temperature 0, at most 3 model steps, completion cap 320, graph query budget 800, tool text capped at 6,000 characters. The shared instruction said to call `query_graph` once when that tool existed. A graph query is not itself a model token. Its text counts only when a later model call receives it.

A nonempty answer is not a passing test. These questions have no executable oracle. Citation means the answer contains a ground-truth node label. Ten no-graph tasks failed with HTTP 400 because the model called a tool named `search`, which was not offered. Other failures are mostly `step_limit`.

| Configuration | Attempted | Completed | Blocked | Nonempty answers | Citations | Mean input | Mean output | Provider tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| No graph | 20 | 20 | 0 | 3 | 3 | 1,519.8 | 205.7 | 34,510 |
| Original Graphify | 20 | 20 | 0 | 6 | 6 | 3,551.6 | 296.2 | 76,956 |
| Synapse default | 20 | 11 | 9 | 2 | 2 | 3,626.4 | 274.1 | 42,905 |
| Synapse full | 20 | 0 | 20 | 0 | 0 |  |  |  |

Means use completed tasks with complete provider usage. One Synapse-default task hit the daily cap after earlier calls in that same task had reported 311 input tokens and 64 output tokens. Those tokens are in the call log and are outside the 42,905, because the task outcome is blocked. Synapse full sent no benchmark request.

| Comparison | Paired questions | Upstream tokens | Other tokens | Total difference | Citations |
|---|---:|---:|---:|---:|---|
| Synapse default vs upstream, completed | 11 | 44,108 | 42,905 | 2.7% fewer for Synapse | 2 vs 2 |
| Synapse default vs upstream, nonempty answers | 1 | 4,837 | 4,670 | 3.5% fewer for Synapse | 1 vs 1 |
| Synapse full vs upstream | 0 |  |  |  | blocked |
| No graph vs upstream, completed | 20 | 76,956 | 34,510 | 55.2% fewer for no graph | 3 vs 6 |

On those 11 questions Synapse default used 1,080 fewer input tokens (2.6%) and 123 fewer output tokens (3.9%). The single shared nonempty answer is "how is a JSON file written to disk atomically". One question is not a general reduction, and no significance test is claimed. The no-graph arm spent fewer tokens and cited fewer labels. Original Graphify issued 22 graph queries. Synapse default issued 12 before the cap. Synapse full issued none.

A one-question pilot with a 180-token completion cap is stored separately in `tools/eval/results/agent_efficiency/groq/pilot_graph_instruction/`. Upstream used 6,131 tokens and returned an empty answer. Synapse default used 4,482 and cited `validate_url()`. Synapse full used 4,451 and cited it too. An earlier automatic-tool pilot never called `query_graph`; it is in `pilot_tool_choice_auto/`. Neither pilot is mixed into the table.

Artifacts: `tools/eval/results/agent_efficiency/groq/full_graph_instruction/`. Reproduce with `GROQ_API_KEY` set and `python tools/eval/run_groq_token_benchmark.py --all`. At this prompt size the 200,000-token daily cap does not cover all four arms.

## Earlier run without a provider

The sections below describe the run that did not call a model. `GROQ_API_KEY` was unset then. Those token figures are local character estimates.

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
