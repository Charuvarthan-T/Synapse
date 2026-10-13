# Graphify versus Synapse

Retrieval numbers in this report come from `tools/eval/results/retrieval_summary.json`, produced by `python tools/eval/run_graphify_comparison.py` on 13 October 2026. The query code under test is `319511ece4d797337eaf1a0cf499dd86a2a6babe`. Later commits add the pending-path fix, the extension icon, and these artifacts. They do not change query ranking.

## 7.1 Executive summary

Synapse's community-aware retrieval finds more of the labelled nodes than unmodified upstream Graphify on this 20-question set: Recall@10 0.70 versus 0.40, Precision@10 0.075 versus 0.045. Weighted traversal is close behind (Recall@10 0.65). Personalized PageRank is better than upstream on recall (0.55) and worse than Synapse's own weighted and community walks. Turning PageRank, learned weights, and community mode on together follows PageRank, not the community result, because `--ppr` replaces the neighborhood ranking.

Learned weights do not change this benchmark. No real user feedback was available, and the labelled questions were not used as training data. CrossCodeEval was not run: there is no model API key. The engine suite is not green.

## 7.2 Systems compared

| System | Identity | What ran |
|---|---|---|
| Original Graphify | https://github.com/Graphify-Labs/graphify.git at `a9860f70dc3cc2e9c2d8cb923484cfa0592b9067` (release 0.9.88) | `graphify query` with no Synapse flags, via `PYTHONPATH`, from that checkout |
| Synapse baseline | this repo, weighted traversal, fixed weights | `graphify query` with default weights and no `--ppr`, `--learned-weights`, or `--community-aware` |
| Synapse unweighted | same tree | `--unweighted` |
| Synapse community | same tree | `--community-aware` (Louvain; Leiden/`graspologic` is not installed on Python 3.13) |
| Synapse PageRank | same tree | `--ppr` (alpha 0.85, tol 1e-6, max 100 iterations) |
| Synapse learned | same tree | `--learned-weights` with no `learned_weights.json` |
| Synapse combined flags | same tree | `--ppr --learned-weights --community-aware` |

Both engines queried the same frozen graph: 260 nodes, 536 edges, built by Synapse from seven `graphify/` files. This is the same graph, not two independent extractions. Upstream extraction was not repeated.

## 7.3 Experimental methodology

- Questions: the existing 20-item `tools/eval/ground_truth.json`. Binary node ids. Not re-annotated by a second person.
- No held-out split. The same 20 questions are the whole set.
- Scoring: exact node id after mapping a returned label through the graph. Precision and recall at 1, 5, and 10, plus MRR.
- NDCG and MAP were not computed. The labels are not graded.
- Budget: 2000 tokens. One new process per question, so latency includes startup.
- Host: Windows, Python 3.13.5. No OpenAI or Groq key.
- `GRAPHIFY_NO_AUTO_REFRESH=1` so the upstream CLI would not rewrite the installed skill during the batch. One earlier smoke command did refresh a skill copy under the user profile before that variable was set.

## 7.4 Overall benchmark comparison

| Benchmark | n | Original Graphify | Synapse | Absolute difference | Status |
|---|---:|---|---|---|---|
| Retrieval Recall@10, community-aware | 20 | 0.400 | 0.700 | +0.300 | COMPLETE |
| Retrieval Recall@10, weighted baseline | 20 | 0.400 | 0.650 | +0.250 | COMPLETE |
| Retrieval Recall@10, PageRank | 20 | 0.400 | 0.550 | +0.150 | COMPLETE |
| Retrieval MRR, weighted baseline | 20 | 0.260 | 0.265 | +0.005 | COMPLETE |
| Code validation | 7 labelled snippets | no equivalent validator | 7/7 met the conservative labels; 0 false contradictions | capability difference | COMPLETE |
| Multigraph round trip | 1 pair | simple graph keeps one edge | `calls` and `handles` both survive reload | capability difference | COMPLETE |
| Learned-weight update | 6 dev records + 1 held-out record | fixed table | imports importance 0.40 to 0.657; held-out record excluded | not a retrieval gain | COMPLETE |
| Graph evidence checks | 4 claims | no equivalent | 4/4 | capability difference | COMPLETE |
| CrossCodeEval 47-task | 47 | historical partial file only | not rerun |  | BLOCKED |
| Extension GUI flows |  | no extension | VSIX installed; Ask/Search/Verify/Enrich/Map not clicked |  | PARTIAL |
| Agent token, cost, and task success | 20 questions × 4 configurations |  |  |  | BLOCKED |
| Retrieval context size, local estimate | 20 questions × 5 configurations × 4 budgets | see `docs/eval/AGENT_EFFICIENCY_REPORT.md` | community-aware context is smaller than upstream at equal Recall@10 | not an API cost | COMPLETE |
| Indexing wall clock and RSS | 1, 7, and 25 files | measured | measured |  | COMPLETE |
| NDCG / MAP |  |  |  |  | NOT RUN |

## 7.5 Retrieval comparison

| Configuration | P@1 | P@5 | P@10 | R@1 | R@5 | R@10 | MRR | Median latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Original Graphify | 0.100 | 0.080 | 0.045 | 0.100 | 0.350 | 0.400 | 0.260 | 0.66 s |
| Synapse unweighted | 0.100 | 0.080 | 0.050 | 0.100 | 0.350 | 0.450 | 0.249 | 0.81 s |
| Synapse weighted | 0.100 | 0.090 | 0.070 | 0.100 | 0.400 | 0.650 | 0.265 | 1.03 s |
| Synapse community-aware | 0.100 | 0.080 | 0.075 | 0.100 | 0.350 | 0.700 | 0.265 | 0.94 s |
| Synapse learned, no file | 0.100 | 0.090 | 0.070 | 0.100 | 0.400 | 0.650 | 0.265 | 0.89 s |
| Synapse PageRank | 0.100 | 0.090 | 0.060 | 0.100 | 0.400 | 0.550 | 0.250 | 1.54 s |
| All three new flags | 0.100 | 0.090 | 0.060 | 0.100 | 0.400 | 0.550 | 0.252 | 1.28 s |

Per-query rows are in `tools/eval/results/retrieval_task_results.json`.

## 7.6 CrossCodeEval comparison

Not run. `OPENAI_API_KEY` and `GROQ_API_KEY` are unset, so no completions were requested. The September files under `tools/eval/crosscodeeval_results*.json` stay historical. The 19-task table and the 47-task table must not be merged. Status: BLOCKED.

## 7.7 Feature ablation results

On retrieval, community-aware mode is the best Synapse configuration (Recall@10 0.70, Precision@10 0.075). Weighted fixed weights are next (0.65 / 0.070). Learned weights match that baseline because the weight file is absent. PageRank is worse than weighted traversal on this set (Recall@10 0.55, MRR 0.250 versus 0.265).

The combined flag run matches PageRank rather than community mode. `graphify query --ppr` replaces the BFS neighborhood ranking with Personalized PageRank, so the community walk's ordering is not what fills the top 10.

Validation, multigraph storage, and graph-evidence checks are capability gains. Original Graphify has no matching score.

## 7.8 Reliability and testing

`python -m pytest tests -q -p no:cacheprovider --tb=no` after the pending-path fix: **3,624 passed, 64 failed, 149 skipped, exit 1, 277.23 s**.

`tests/test_watch.py::test_queue_and_drain_pending_round_trip` failed before the fix because Windows wrote `sub\b.py`. It passes now. Six `tests/test_cli_export.py` query/explain tests failed inside the full run and passed when rerun alone (6 passed).

The other failures are grouped in `tools/eval/results/test_failure_analysis.json`: Windows symlink or file-lock errors, DNS failure for `example.com` (`getaddrinfo` errno 11002), DreamMaker extractor mismatches, and install/hook/Obsidian cases. Tests were not deleted.

Extension: `npx tsc -p vscode-extension` succeeded. `npx vsce package` wrote `vscode-extension/synapse-0.2.0.vsix` (1.07 MB, gitignored). `code --install-extension` reported that the VSIX was installed on VS Code 1.140.0. Unit tests were 55 passed and 1 pending. Ask, Search, Verify, Enrich, and Graph Map were not clicked. Live model providers were not called.

Ruff was clean on the earlier feature edit. Pyright is not installed.

## 7.9 Performance and resource use

CLI median latency on the 20 questions is 0.66 s for upstream Graphify and 0.94–1.54 s for Synapse modes. Each query is a new process.

In-process, on a 32-node graph, weighted query median time was about 0.5 ms and one PageRank call was 0.39 s (`tools/eval/results/performance_results.json`). That PageRank sample includes library startup and is not a claim about large graphs. Memory was not measured.

## 7.10 Limitations and threats to validity

- Twenty questions, one seven-file corpus, team-authored labels, no second annotator, no held-out split.
- Both engines searched a graph extracted by Synapse, so this does not measure extraction differences.
- Upstream Graphify here is 0.9.88, which is newer than the September worktree commit `5db7e4e` used in the old report.
- Community detection is Louvain, not Leiden.
- Learned weights have no real query log.
- The bad-method validation example returned UNKNOWN, not CONTRADICTED. The label only required "not supported."
- CrossCodeEval and the extension GUI are incomplete.
- Six full-suite failures did not reproduce alone.
- Agent token, cost, tool-use, and answer-correctness experiments did not run. No provider key was available, and the CrossCodeEval parquet is not in the tree.
- The combined PageRank configuration's rendered node order changed across repeated launches. Its budget-sweep Recall@10 values are not a stable budget curve.

## 7.11 Conclusion

Synapse's weighted and community-aware retrieval outperform unmodified Graphify 0.9.88 on Recall@10 and Precision@10 for these 20 questions. Community-aware is the best measured retrieval configuration. PageRank is a real, selectable algorithm and, on this set, it is worse than weighted traversal and only partly better than upstream. Learned weights, the multigraph, language checks, and multi-root workspaces do not explain the retrieval gain. The first two are demonstrated as mechanisms. The retrieval gain tracks relationship weights and community-aware search.

Synapse is not uniformly better. Unweighted Synapse has a slightly lower MRR than upstream (0.249 versus 0.260). PageRank has a lower MRR than upstream as well (0.250). There is no evidence here about code-completion accuracy.

## 7.12 Agent efficiency and cost

Status: **PARTIALLY COMPLETE**. No model request was sent. `OPENAI_API_KEY`, `GROQ_API_KEY`, and `ANTHROPIC_API_KEY` were unset, and the CrossCodeEval parquet is not in the repository. The 80 agent rows in `tools/eval/results/agent_efficiency/agent_tasks.jsonl` have null token counts, null cost, and null success. Those nulls are not zeros.

What did run is graph infrastructure, on commit `f928959a5b0be581f6749f53d5d3bfbccac1e57f`, against upstream Graphify `a9860f70dc3cc2e9c2d8cb923484cfa0592b9067`. The retrieval sweep queried the existing frozen Synapse graph. It is not an agent result and it is not a provider token count. Context size is `len(query stdout) // 4`.

At a 2,000-token query budget, community-aware Synapse again led retrieval (Recall@10 0.700, Precision@10 0.075) and its mean local context estimate was 1,552 tokens, against 2,726 for upstream Graphify at Recall@10 0.400. The seven source files, concatenated, estimate to 57,366 tokens by the same character rule. Raising the budget to 4,000, 8,000, and 16,000 did not change Recall@10 for upstream, weighted Synapse, community-aware Synapse, or PageRank alone. The combined `--community-aware --learned-weights --ppr` run did change (Recall@10 0.600, 0.450, 0.550, 0.500). A separate check of that same command at a fixed 2,000 budget also changed the fifth node across three launches, while the community header stayed `Communities=[2, 1]`. That movement is run-to-run instability, not a demonstrated budget effect. No learned-weight file was present.

Code-only indexing of the seven frozen files took 1.88 s for Synapse (259 nodes, 512 edges, peak RSS 98,435,072 bytes) and 2.58 s for upstream (341 nodes, 629 edges, peak RSS 26,533,888 bytes). These are separate extractions. Upstream was not indexed on the 25-file sample. A dollar break-even is not computed. After one discarded warmup, five queries of one question took median 0.71 s upstream, 0.87 s for default Synapse, and 1.22 s for the combined flags. Each query is a new process.

Details, reproduction commands, and the blocked list are in `docs/eval/AGENT_EFFICIENCY_REPORT.md`.
