# CrossCodeEval on DeepSeek Flash: results (to-do A4)

Two runs on the same 147 tasks:

- **Run 1** (the A4 run, below): the four runbook conditions at DeepSeek's default
  temperature. Synapse tied original Graphify and BM25.
- **Run 2** ([further down](#run-2-hybrid-retrieval-and-code-bodies)): after diagnosing
  why, Synapse gained hybrid retrieval (`graphify query --hybrid`). All conditions
  plus four Synapse variants were re-run at temperature 0. **Hybrid Synapse: 23.1%
  exact match vs 17.7% for plain Synapse and 15.6% for BM25.** Use run 2 for the
  report's main table.

## Run 1

Run on 2026-10-26, finished 12:23 UTC (off-peak), following
[`DEEPSEEK_EVAL_RUNBOOK.md`](DEEPSEEK_EVAL_RUNBOOK.md) section A4.
Raw data: [`tools/eval/crosscodeeval_results_deepseek.json`](../../tools/eval/crosscodeeval_results_deepseek.json)
(summary, environment, every task) and its `.progress.jsonl`.

### Setup

| | |
|---|---|
| Benchmark | CrossCodeEval, Python (`ZHENGRAN/cross_code_eval_python`, 2,665 tasks), single-line completion |
| Model | `deepseek-flash` (DeepSeek-V4.1-Flash), non-thinking mode, max 300 output tokens; every response reported `deepseek-flash` |
| Conditions | no context, BM25 (the benchmark's baseline), original Graphify (release 0.9.88, `a9860f7`), Synapse (`2815872`, `--community-aware`, as the VS Code extension's Search uses) |
| Tasks | 6 repositories, up to 25 tasks each: **147 tasks × 4 conditions = 588 calls**, all on the same tasks |
| Errors | 0 tasks with errors; nothing excluded |
| Metrics | exact match (EM) and edit similarity (ES) of the predicted line against the reference |
| Tokens / cost | 501,563 input + 10,040 output tokens; **≈ $0.08** at off-peak prices (≈ $0.16 at peak). Spending limit was $1.40; not reached |

Repositories (each cloned at the commit the dataset was built from; graphs built
with `graphify extract --code-only`, local, no AI):

| Repository | Tasks | Graph |
|---|---|---|
| nccgroup-libslub-7732a54 | 23 | 422 nodes, 638 edges |
| vladkens-twscrape-4f2ee7f | 24 | 263 nodes, 706 edges |
| ortursucceeh-Project-PhotoShare-1942d61 | 25 | 675 nodes, 1,287 edges |
| hq0709-Depth-NeuS-49d93d4 | 25 | 359 nodes, 667 edges |
| Quantomatic-zxlive-c7b5c28 | 25 | 456 nodes, 1,042 edges |
| briandelmsft-STAT-Function-f91d421 | 25 | 225 nodes, 503 edges |

The first two are the repositories from the September runs; the other four are the
largest by task count. Two of the four largest could not be used and were replaced
by the next largest, as the runbook says: `crowdgames-sturgeon-pub-a235e6d`
(69 tasks; commit `a235e6d` no longer exists in the GitHub repository) and
`graphcore-PopTransformer-1314598` (40 tasks; repository no longer on GitHub).
libslub and twscrape have fewer than 25 tasks, so the total is 147, not 150.

### Results (n = 147)

| Condition | Exact match | Edit similarity | Context found |
|---|---|---|---|
| No context | 11.6% (17) | 0.578 | — |
| BM25 (baseline) | **17.0%** (25) | **0.649** | 100% |
| Original Graphify | 15.6% (23) | 0.635 | 99.3% |
| **Synapse** | 16.3% (24) | 0.638 | 99.3% |

95% bootstrap confidence intervals (5,000 resamples of the 147 tasks):

| Condition | Exact match | Edit similarity |
|---|---|---|
| No context | 6.8–17.7% | 0.542–0.621 |
| BM25 | 11.6–24.5% | 0.603–0.688 |
| Original Graphify | 10.2–21.1% | 0.596–0.681 |
| Synapse | 10.9–21.8% | 0.601–0.675 |

Paired comparisons (same task, same model; "wins/losses" = tasks where the first
condition scored higher/lower):

| Comparison | EM wins / losses | ES wins / losses | ES difference, 95% CI |
|---|---|---|---|
| Synapse vs no context | 14 / 7 | 70 / 48 | **+1.3 to +9.9 points** |
| Synapse vs original Graphify | 10 / 9 | 64 / 55 | −3.5 to +4.7 points |
| Synapse vs BM25 | 12 / 13 | 62 / 59 | −5.7 to +3.5 points |

Per repository (EM % / ES × 100):

| Repository | No context | BM25 | Original Graphify | Synapse |
|---|---|---|---|---|
| libslub (23) | 4.3 / 55.3 | 13.0 / 63.4 | 4.3 / 60.4 | 0.0 / 59.5 |
| twscrape (24) | 16.7 / 59.0 | 25.0 / 71.4 | 33.3 / 66.7 | 33.3 / 72.1 |
| PhotoShare (25) | 24.0 / 76.9 | 16.0 / 77.0 | 16.0 / 72.6 | 24.0 / 76.5 |
| Depth-NeuS (25) | 0.0 / 43.8 | 8.0 / 49.0 | 12.0 / 56.0 | 8.0 / 54.0 |
| zxlive (25) | 12.0 / 58.8 | 16.0 / 61.0 | 20.0 / 67.2 | 20.0 / 63.2 |
| STAT-Function (25) | 12.0 / 52.9 | 24.0 / 67.7 | 8.0 / 58.3 | 12.0 / 57.5 |

### What run 1 shows

1. **Cross-file context helps.** All three retrieval conditions beat no context on
   both metrics (EM +4.0 to +5.4 points, ES +0.057 to +0.071). For Synapse the ES gain
   over no context is significant (CI excludes zero); EM wins outnumber losses 2:1.
2. **Synapse ≈ original Graphify ≈ BM25 on this benchmark.** The differences are one
   or two tasks out of 147 and well inside the confidence intervals. Synapse is
   ahead of original Graphify by one exact match and 0.003 ES; BM25 is ahead of
   Synapse by one exact match and 0.011 ES. None of these differences is significant.
3. **Graph retrieval is strongest where cross-file structure matters most**
   (twscrape, Depth-NeuS, zxlive), and weakest where the next line depends on
   in-file or textual patterns (libslub, STAT-Function), where BM25's text matching
   does better.
4. **The September result does not reproduce on this model.** The September Groq
   run (`openai/gpt-oss-20b`, libslub + twscrape, n = 47) had graph context at 25.5%
   EM vs BM25 12.8%. On the same 47 tasks with DeepSeek Flash: original Graphify
   19.1%, Synapse 17.0%, BM25 19.1%, no context 10.6%. Keep the September numbers as
   historical (different model, smaller sample); don't put them in the same table.

### Why Synapse did not beat original Graphify in run 1

Measured locally on the 147 tasks (no model calls):

- The model nearly always needs the answer's identifier in its context (Synapse:
  20/76 exact matches when it was there, 4/71 when not), so what matters is
  whether retrieval puts that symbol in the **top 5**.
- Synapse's earlier gain was in *recall* (Recall@10 0.40 → 0.70 on the
  20-question benchmark) with MRR unchanged (0.260 → 0.265): it found more
  relevant symbols, but did not rank the best one higher.
- Graph seeding matched the query against symbol *names* only. Original Graphify
  and Synapse returned different snippets (2.2 of 5 shared on average) but the
  answer identifier was in the context about equally often (82 vs 76 tasks),
  while BM25 over code text got 86.

The fix tested in run 2 is to also match the query against each symbol's *code*.

## Run 2: hybrid retrieval and code bodies

### What changed in Synapse

- **`graphify query --hybrid`** (new, `graphify/lexical_retrieval.py`): BM25 over
  each symbol's source range (exact for Python via `ast`, next-symbol boundaries
  elsewhere), fused with the graph ranking by reciprocal rank fusion (k = 60); the
  top 15 code matches join the result. Off by default.
- **`--ppr` without scipy**: NetworkX's PageRank needs scipy, which is not a
  dependency, so `--ppr` crashed on a clean install. A pure-Python power iteration
  now takes over (matches NetworkX to 1e-17 on a 60-node test graph).

### Setup

Same 147 tasks, repositories, graphs and model as run 1, but **temperature 0** (run 1
used the provider default), so every condition was re-run and differences come from
the context alone. 8 conditions × 147 tasks = 1,176 calls, 0 errors; 1,127,596 input
+ 18,719 output tokens, **≈ $0.18** off-peak. Input tokens are now recorded per
condition. Raw data: `tools/eval/crosscodeeval_results_deepseek_v2.json`.

New conditions (`--conditions all`):

| Condition | Context |
|---|---|
| Synapse hybrid | `--community-aware --hybrid`, 5 snippets × 8 lines (same format as Synapse) |
| Synapse + code | Synapse ranking, whole symbol bodies (`graphify.code_context`, as the extension's Ask sends them), capped at 2,600 chars |
| Synapse hybrid + code | hybrid ranking, whole symbol bodies, same cap |
| BM25 + Synapse | 3 BM25 chunks + 3 Synapse snippets |

### Results (n = 147, temperature 0)

| Condition | Exact match | 95% CI | Edit similarity | Input tokens / call |
|---|---|---|---|---|
| No context | 8.8% (13) | 4.1–13.6 | 0.589 | 450 |
| BM25 (baseline) | 15.6% (23) | 10.9–22.4 | 0.647 | 1,141 |
| Original Graphify | 19.0% (28) | 13.6–23.8 | 0.665 | 910 |
| Synapse | 17.7% (26) | 11.6–24.5 | 0.650 | 911 |
| **Synapse hybrid** | **23.1% (34)** | 17.0–29.9 | 0.671 | **949** |
| Synapse + code | 19.7% (29) | 13.6–25.9 | 0.626 | 1,064 |
| Synapse hybrid + code | 18.4% (27) | 12.9–24.5 | 0.652 | 1,089 |
| **BM25 + Synapse** | **23.8% (35)** | 18.4–32.0 | **0.676** | 1,157 |

Paired comparisons (same tasks; EM wins/losses, exact McNemar p, bootstrap 95% CI
of the EM difference in points):

| Comparison | EM wins / losses | p | EM difference |
|---|---|---|---|
| Synapse hybrid vs Synapse | 12 / 4 | 0.077 | +0.7 to +12.9 |
| Synapse hybrid vs original Graphify | 14 / 8 | 0.29 | −1.4 to +11.6 |
| Synapse hybrid vs BM25 | 19 / 8 | 0.052 | 0.0 to +14.3 |
| Synapse hybrid vs no context | 24 / 3 | < 0.001 | +8.2 to +21.1 |
| BM25 + Synapse vs BM25 | 15 / 3 | **0.008** | +2.0 to +13.6 |
| BM25 + Synapse vs Synapse | 15 / 6 | 0.078 | +1.4 to +12.9 |
| Synapse + code vs Synapse | 9 / 6 | 0.61 | −2.7 to +8.2 |
| Synapse hybrid + code vs Synapse hybrid | 4 / 11 | 0.12 | −9.5 to −1.4 |
| Synapse vs original Graphify | 8 / 10 | 0.82 | −6.1 to +4.8 |

Per repository (EM % / ES × 100):

| Repository | No ctx | BM25 | Orig. Graphify | Synapse | **Hybrid** | + code | Hybrid + code | BM25 + Syn. |
|---|---|---|---|---|---|---|---|---|
| libslub (23) | 4.3/57.0 | 8.7/60.4 | 13.0/67.7 | 13.0/64.0 | **26.1/73.1** | 8.7/58.1 | 13.0/66.2 | 13.0/62.0 |
| twscrape (24) | 20.8/63.7 | 20.8/66.6 | 33.3/71.9 | 29.2/67.3 | 33.3/72.2 | 37.5/68.8 | 29.2/64.2 | 29.2/66.3 |
| PhotoShare (25) | 16.0/78.3 | 20.0/77.6 | 16.0/75.5 | 20.0/71.6 | 28.0/73.8 | 24.0/75.1 | 24.0/74.7 | **40.0/81.4** |
| Depth-NeuS (25) | 0.0/45.4 | 4.0/53.8 | 24.0/60.3 | 12.0/58.0 | 20.0/65.5 | 12.0/52.2 | 8.0/57.8 | 4.0/58.5 |
| zxlive (25) | 4.0/54.7 | 16.0/63.8 | 20.0/65.7 | 24.0/71.9 | 20.0/67.9 | 28.0/71.3 | 24.0/72.4 | 24.0/68.1 |
| STAT-Function (25) | 8.0/54.5 | 24.0/65.9 | 8.0/58.0 | 8.0/57.1 | 12.0/51.0 | 8.0/50.2 | 12.0/56.0 | **32.0/69.1** |

### What run 2 shows

1. **Hybrid retrieval is the improvement that works.** Synapse hybrid has the best
   exact match of any single retriever: 23.1% vs 17.7% for Synapse (+5.4 points),
   19.0% for original Graphify and 15.6% for BM25, for only ~40 more input tokens
   than Synapse and **17% fewer tokens than BM25**. It beats plain Synapse 12 tasks to
   4. The gains are consistent in direction but not yet significant at p < 0.05 on
   147 tasks (p = 0.05–0.08 against Synapse and BM25), so report them as a clear
   improvement with that caveat.
2. **Graph + lexical beats either alone.** BM25 + Synapse (23.8%) is the best
   condition overall and is the one result that is **significant** against BM25
   (15 wins, 3 losses, p = 0.008), at the same token cost as BM25.
3. **Whole code bodies did not help single-line completion** at a fixed budget.
   Bodies mean fewer symbols in the same 2,600 characters, and the next line usually
   needs the right *name*, not the body. (Bodies still serve Ask, which explains
   code rather than completing it.) Hybrid + code was worse than hybrid alone.
4. **Plain Synapse vs original Graphify is still a tie** (8 vs 10 wins, p = 0.82):
   community-aware expansion does not help this task; hybrid ranking does.
5. **Run-to-run noise is about ±2 tasks per condition.** Between run 1 (default
   temperature) and run 2 (temperature 0) the same unchanged conditions moved by
   1–5 tasks (e.g. BM25 25 → 23, original Graphify 23 → 28), which is why paired
   comparisons on one run are reported.

### Same change on the 20-question retrieval benchmark

`--hybrid` re-scored on the frozen seven-file corpus (rebuilt from `319511e`, the
commit in `FINAL_BENCHMARK_SUMMARY.md`; 259 nodes, 536 edges; team-written labels):

| Mode | P@10 | R@10 | MRR |
|---|---|---|---|
| Weighted (recorded 0.070 / 0.65 / 0.265) | 0.070 | 0.65 | 0.266 |
| Community-aware (recorded 0.075 / 0.70 / 0.265) | 0.070 | 0.65 | 0.266 |
| PageRank, now without scipy (recorded 0.060 / 0.55 / 0.250) | 0.060 | 0.55 | 0.250 |
| **Weighted + hybrid** | **0.080** | **0.75** | **0.564** |
| Community-aware + hybrid | 0.080 | 0.75 | 0.546 |

Hybrid ranking **more than doubles MRR** (0.266 → 0.564) and raises Recall@10 to 0.75
(original Graphify: 0.40 / MRR 0.260). The weighted and PageRank rows reproduce the
recorded numbers; community-aware recall reproduced at 0.65 rather than the recorded
0.70, likely because of engine changes since that run (e.g. named communities in V4).

## Limitations to state in the report

- 147 tasks: one task is 0.68 EM points. Several run-2 gains are suggestive
  (p ≈ 0.05–0.08), not significant; only BM25 + Synapse vs BM25 is (p = 0.008). No
  correction for multiple comparisons.
- One model; one run per setting (run 1 at default temperature, run 2 at 0).
- The hybrid design was chosen after looking at these 147 tasks (a free local
  analysis of which retrieval variant put the answer identifier in context), so run 2
  is not a fully held-out test. The 20-question benchmark, which played no part in
  the design, shows the same direction (MRR 0.266 → 0.564).
- Single-line completion does not exercise Synapse's Ask features (claim validation,
  semantic enrichment).
- Two planned repositories were replaced because they are no longer available.

## Reproduce

Run 2:

```powershell
cceval run --parquet cceval_python.parquet --repos-dir "$env:TEMP\cceval-repos" --repos @REPOS --n-per-repo 25 --conditions all --temperature 0 --max-cost 0.60 --out tools/eval/crosscodeeval_results_deepseek_v2.json
```

Run 1: runbook section A4, with these repository ids. On this machine the project pins
NumPy 1.26, so pyarrow had to be pinned: `--with "pyarrow<22"` in place of
`--with pyarrow` in the `cceval` shortcut (and the dataset was downloaded with
`uv run --no-project --with datasets --with pyarrow --with "numpy>=2"`).
