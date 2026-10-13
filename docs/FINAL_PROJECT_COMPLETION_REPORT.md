# Synapse final project completion report

Final engine commit is recorded in section F after the commits are pushed. Benchmark numbers below were produced by `python tools/eval/final_retrieval.py` on the working tree that contains all five additional features.

## A. Implementation status

| Feature | Path | Production integration | Tests | Status |
|---|---|---|---|---|
| Weighted retrieval | `graphify/weighted_retrieval.py` | `graphify query` default | `tests/test_weighted_retrieval.py` | Already present. PageRank was not this feature. |
| Community retrieval | `graphify/community_retrieval.py` | `--community-aware`; on by default for `graphify reason` | `tests/test_community_retrieval.py` | Already present. Leiden needs `graspologic` on Python < 3.13. |
| Semantic graph | `graphify/semantic_graph.py` | `graphify semantic-graph` | `tests/test_semantic_graph.py` | Already present. Now writes parallel edges on a multigraph. |
| Bidirectional reasoning | `graphify/bidirectional_reasoner.py` | `graphify reason`, extension Ask | `tests/test_bidirectional_reasoner.py` | Already present. Loads a multigraph when the file has one. |
| Python pre-execution checks | `graphify/preexec_validate.py` | `graphify preexec-check`, extension Verify | `tests/test_preexec_validate.py` | Already present. |
| Learned edge weights | `graphify/learned_weights.py` | `graphify learn-weights`, `graphify query --learned-weights`, MCP `learned_weights` | `tests/test_completion_features.py` | Implemented. Fixed table remains the fallback. |
| Multigraph | `graphify/multigraph_store.py` | `graphify semantic-graph`, `graphify migrate-multigraph`, query/reason/preexec load | same | Implemented. Simple graphs stay simple. |
| Non-Python structural validation | `graphify/lang_structural.py` | `validate_code` / `preexec-check` | same | Tree-sitter where the grammar is installed. Unknown stays unknown. |
| Multi-root workspaces | `vscode-extension/src/workspaceRoots.ts`, `extension.ts` | commands, watchers, save updates, LM tools | `vscode-extension/test/unit/workspaceRoots.test.ts` | Implemented. Several folders and no active file prompt instead of using folder 0. |
| Personalized PageRank | `graphify/pagerank_retrieval.py` | `graphify query --ppr`, MCP `retrieval: ppr`, setting `synapse.retrieval` | same | Implemented. Weighted traversal stays the default. |

## B. Complete testing results

Environment: Windows, Python 3.13.5, pytest 8.4.2, editable install `graphifyy` 0.9.27, Node 22.18.0. Ruff 0.15.12 on the edited engine files: all checks passed. Pyright is not installed. `graspologic` cannot be installed on Python 3.13. `OPENAI_API_KEY` and `GROQ_API_KEY` are unset.

| Command | Result |
|---|---|
| `python -m pytest tests/test_completion_features.py tests/test_weighted_retrieval.py tests/test_community_retrieval.py tests/test_semantic_graph.py tests/test_bidirectional_reasoner.py tests/test_preexec_validate.py tests/test_query_cli.py -q` | 111 passed |
| `npm run test:unit` in `vscode-extension` | 55 passed, 1 pending |
| `npx tsc -p vscode-extension --pretty false` | exit 0 |
| `python -m pytest tests -q -p no:cacheprovider --tb=no` | 3,837 collected: 3,629 passed, 59 failed, 149 skipped, exit 1, 242.40 s |

The 59 failures are outside the new feature tests. The largest groups are `tests/test_detect.py` (12), `tests/test_languages.py` (9), `tests/test_security.py` (6), `tests/test_hooks.py` (5), and `tests/test_image_vision.py` (5). `tests/test_watch.py` still fails on Windows path separators (`sub\\b.py` versus `sub/b.py`) and on `WinError 32` while deleting a temp directory. `tests/test_query_cli.py` passed after graph loading stopped forcing every file to be a directed graph.

VS Code integration (`npm run test:integration`) and `vsce package` were not completed in this session. `package.json` references `media/icon.png`, and that file is not in the tree (`media/synapse-icon.svg` is). Live Copilot, Claude, and Codex calls were not made.

## C. Final benchmark results

Command: `python tools/eval/final_retrieval.py`. Corpus: the seven files named in `docs/eval/SYNAPSE_VS_GRAPHIFY.md`, copied at run time to `tools/eval/frozen_corpus` and extracted with `graphify extract --code-only --no-cluster`. This run produced 260 nodes and 536 edges. Scoring uses the unchanged 20-question `tools/eval/ground_truth.json` and exact node ids. No benchmark label was written into `learned_weights.json`. There is no separate held-out question split; the same 20 questions are the only retrieval set.

| Arm | Precision@10 | Recall@10 | MRR | Median query latency |
|---|---:|---:|---:|---:|
| Unweighted traversal (Graphify-style walk on this graph) | 0.050 | 0.45 | 0.249 | 1.10 s |
| Weighted traversal | 0.070 | 0.65 | 0.265 | 1.14 s |
| Community-aware | 0.075 | 0.70 | 0.265 | 1.07 s |
| Learned weights, no feedback file | 0.070 | 0.65 | 0.265 | 1.08 s |
| Personalized PageRank | 0.060 | 0.55 | 0.250 | 1.52 s |
| PPR + learned weights + community | 0.055 | 0.50 | 0.249 | 1.55 s |

Latency is one new Python process per question, so it includes startup. It is not an in-process server measurement.

Historical figures in `docs/eval/SYNAPSE_VS_GRAPHIFY.md` (Precision@10 0.075 / Recall@10 0.70 / MRR 0.271 for the earlier Synapse arm) were not reproduced as a fresh Original Graphify worktree run. The unweighted arm above is the same-graph baseline, not commit `5db7e4e`.

CrossCodeEval, including the missing 47-task Original Graphify arm, was not rerun. No Groq or OpenAI key is configured. The 19-task and 47-task tables in the September report stay historical and are not mixed with the table above.

Ablations on this retrieval set:

- Learned weights do nothing until a feedback log exists. With no log they match fixed weights.
- Community-aware retrieval is the strongest arm on this set: Precision@10 0.075, Recall@10 0.70, against weighted traversal at 0.070 and 0.65.
- Personalized PageRank is below weighted traversal on all three metrics (0.060 / 0.55 / 0.250) and is slower. Combining it with the other flags follows PageRank, not the community arm.
- Multigraph support is not visible in these scores. The corpus has no semantic edges.
- Language validation is covered by `tests/test_completion_features.py`, not by retrieval Precision@10.
- Multi-root support is covered by `workspaceRoots.test.ts`. It is not a retrieval metric.

## D. Research conclusions

Weighted and community-aware retrieval beat the unweighted walk on this 20-question rerun. Community-aware has the best precision and recall of the arms that were run. Personalized PageRank does not improve those metrics here; it is a selectable strategy with a measured cost. Learned weights are implemented and tested, and they do not move this benchmark without user feedback that is kept off the labelled questions. The multigraph preserves a structural edge and a semantic edge between the same nodes; this corpus does not show a retrieval change from that. Non-Python validation is structural for languages whose Tree-sitter grammar imports, and it refuses to treat a missing symbol or a missing parser as a hallucination. Multi-root support stops the extension from ignoring every folder after the first.

The earlier claim that Synapse retrieval is universally more accurate than both grep and Original Graphify is not restated. On this rerun, community-aware retrieval reaches Recall@10 0.70 and weighted retrieval reaches 0.65, against 0.45 unweighted. The September grep baseline (Recall@10 0.75) was not rerun.

## E. Reproduction

```text
python -m pip install -e . --no-deps
python -m pytest tests/test_completion_features.py -q
python -m pytest tests -q
cd vscode-extension && npm ci && npx tsc -p . && npm run test:unit
python tools/eval/final_retrieval.py
```

Learned weights, when you have real feedback:

```text
python -m graphify learn-weights --log feedback.jsonl --graph graphify-out/graph.json
python -m graphify query "question" --learned-weights
```

Feedback records need `useful` and `relations`. `source: benchmark` and `held_out: true` are ignored.

## F. Release and Git status

Branch `main`, remote `https://github.com/Charuvarthan-T/Synapse.git`.

| Commit | Message |
|---|---|
| `471201a` | feat: add learned weights, multigraph edges, structural checks, and PageRank |
| `f037f0b` | feat: support multi-root VS Code workspaces |
| `dc03fa6` | docs: record the integrated retrieval rerun and completion report |

The retrieval JSON was generated from the tree that became `471201a` and `f037f0b`. `tools/eval/frozen_corpus/` is a local copy created by the benchmark script and is not committed. Push status is recorded after `git push origin main`.

## G. Outstanding blockers

- No API key for a fresh 47-task or 19-task CrossCodeEval, so those arms stay at the September partial files.
- Original Graphify was not checked out as a separate worktree in this run.
- 59 engine tests still fail for Windows paths, optional parsers, network, or image dependencies. They were not deleted. The full command result is 3,629 passed, 59 failed, 149 skipped.
- Leiden is unavailable on Python 3.13.
- VS Code integration, VSIX install, and live model providers were not executed. `media/icon.png` is missing.
- The 20 retrieval labels were not independently re-annotated.
- Query latency includes process startup.

## H. Final decision

**PARTIALLY COMPLETE.** The five additional features are implemented, wired into the CLI, server, or extension, and covered by automated tests. Retrieval was rerun on the integrated tree. The full engine suite, CrossCodeEval, packaging, and a clean-room second checkout are not all green.
