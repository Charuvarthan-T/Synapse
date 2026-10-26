# Synapse: what's left to finish

Status on 2026-10-26: the software is complete. Synapse for VS Code V4 (0.4.0) is
released, and every extension test suite passes on Windows, macOS and Linux. What
remains is evaluation evidence (A) and updating the project material (B).

Status column: **Can start now** means nothing blocks the task (no key, person or
other task needed); it is not done yet. **Blocked** names what is missing. **After …**
means it waits for other tasks.

## A. Evaluation

| # | Task | Status | Needs |
|---|---|---|---|
| A1 | [ ] Fresh run of original Graphify on the frozen seven-file corpus (`tools/eval/run_graphify_comparison.py`) | Can start now | Upstream checkout at `%TEMP%/graphify-upstream` |
| A2 | [ ] Re-run the grep baseline on the same 20 questions | Can start now | Nothing |
| A3 | [ ] Graph-only vs graph+code benchmark for Ask: fixed question set, answer scoring, token and latency cost | Can start now | Claude Code (uses the Claude plan's usage limits) |
| A4 | [ ] CrossCodeEval: the original-Graphify condition is missing 28 of the 47 tasks (Groq's daily cap ran out). Run them so all four conditions have n=47, or widen to about 100–150 tasks and re-run all four conditions on one task set | Blocked | Groq or OpenAI API key |
| A5 | [ ] Agent-efficiency run for the full Synapse configuration | Blocked | Groq API key (daily token cap: about one configuration per day) |
| A6 | [ ] Record all results in `docs/eval/FINAL_BENCHMARK_SUMMARY.md` and the accuracy report, and state that the 20 retrieval labels were written by the team with no second annotator | After A1–A5 | |

## B. Project material

| # | Task | Status |
|---|---|---|
| B1 | [ ] Update `docs/FINAL_PROJECT_COMPLETION_REPORT.md`: V3 and V4 features, the fixes, current test numbers, cleared blockers, final status | Can start now |
| B2 | [ ] Update the viva guides (`docs/VIVA_PREPARATION_GUIDE.md`, `docs/eval/SYNAPSE_PROJECT_VIVA_GUIDE.pdf`) for V3 and V4 | Can start now |
| B3 | [ ] Update the user and developer PDF guide for V3 and V4 and commit it to `docs/` (it is not in the repository yet) | Can start now |
| B4 | [ ] Update the engine and extension architecture diagrams (code context, named communities, learned weights, PageRank, multigraph, multi-root) and commit the draw.io files to `docs/` | Can start now |
| B5 | [ ] Update the review presentation content with the V3 and V4 features and the new evaluation results | After A |
| B6 | [ ] Final project report in the Amrita AIE thesis template (`Report/`), if the department requires it | Needs requirements and deadline |

## C. Waiting on decisions

- [ ] Groq API key for A4 and A5
- [ ] Whether B6 is required, and its deadline

## Dropped

- Independent re-labelling of the 20 retrieval questions (decided 2026-10-26). The
  labels stay team-written; the reports state this limitation (A6).

## Done

- Engine: weighted retrieval, community-aware retrieval, semantic graph, bidirectional
  reasoning, pre-execution checks, learned weights, multigraph, non-Python structural
  checks, Personalized PageRank, multi-root workspaces, source-code context for Ask,
  named communities.
- Extension V3 (0.3.1): code context on by default (`synapse.ai.includeCode`),
  "Code the AI read" view, runs under Windows Smart App Control.
- Extension V4 (0.4.0): named communities. Content-based names on every build
  (`app/auth.py · login`), readable AI titles on request ("Authentication and
  Sessions"), shown in search, Explain, the sidebar and the graph map; names also
  feed community-aware retrieval.
- Fixed: Enrich relations lost on Build Graph (main CI green again).
- CI runs every extension suite on Windows, macOS and Linux before each release.
- Live Ask with Claude Code: graph+code gave concrete, correct answers where
  graph-only answers said the context was insufficient (3 questions).
