# Accuracy Evaluation

This report documents two accuracy metrics measured for the project review:
a **Retrieval Accuracy Harness** (does graphify's graph traversal find the
right code for a question, compared to a naive keyword baseline) and a
**CrossCodeEval completion accuracy** benchmark (does cross-file context
retrieved by graphify actually improve an LLM's code-completion accuracy,
compared to no context and to the standard BM25 baseline).

Both evals used **Groq's free-tier API** (OpenAI-compatible endpoint,
`https://api.groq.com/openai/v1`) — total cost: **$0**.

## 1. Retrieval Accuracy Harness

**Corpus:** a 7-file, ~5,700-line subset of `graphify/` itself
(`build.py`, `serve.py`, `security.py`, `export.py`, `benchmark.py`,
`paths.py`, `__main__.py`), extracted with `graphify extract --code-only`
(pure AST parsing, no LLM calls) into a 257-node / 448-edge graph.

**Method:** 20 hand-written natural-language questions, each with a known
correct target function (`tools/eval/ground_truth.json`). For each question:

- Run `graphify query "<question>"` (BFS traversal, no LLM) and take the
  ordered list of retrieved nodes.
- Run a naive keyword-overlap baseline: grep every `def`/`class` line in the
  corpus, score by how many non-stopword question terms appear in its name or
  the following few lines, rank by score.
- Score both against the ground-truth target with **Precision@10**,
  **Recall@10**, and **MRR** (mean reciprocal rank).

**Results** (`tools/eval/retrieval_results.json`):

| Method | Precision@10 | Recall@10 | MRR |
|---|---|---|---|
| graphify query (BFS) | 0.055 | 0.50 | 0.258 |
| naive grep baseline | 0.083 | 0.75 | 0.318 |

**Finding:** on this small, single-repo sample, the naive grep baseline
slightly *outperformed* graphify's graph query. Inspecting the misses shows
why: graphify's query starts from keyword-overlap matches against node
*labels*, so it fails when the question uses different words than the
function name — e.g. "duplicate edges" vs. `dedupe_edges()`, "breadth first
search" vs. `_bfs()`, "atomically" vs. `write_json_atomic`. The grep baseline
has the same weakness but partially compensates by also matching terms
against the few lines *after* each definition (docstrings), catching some
cases graphify's pure label-matching missed. Graphify's BFS also intentionally
returns a *broad* neighborhood (imports, calls, file nodes) rather than a
tight top-k list, which lowers precision-at-k by design — it's built to hand
an LLM enough surrounding context to answer, not to be a precision-ranked
search engine on its own.

**Takeaway:** graphify's structural retrieval is not yet better than naive
keyword search at pinpointing a single right answer on a small, single-repo
graph. Its actual advantage (tested next) is in retrieving *useful
supporting* context across files for a downstream LLM, where broader recall
matters more than precision-at-k.

## 2. CrossCodeEval Completion Accuracy

**Dataset:** [CrossCodeEval](https://huggingface.co/ZHENGRAN/cross_code_eval_python)
(Python), a public single-line code-completion benchmark built from real
GitHub repositories, each task requiring cross-file context to complete
correctly.

**Repos used:** two tasks' source repos were cloned at the **exact commit**
the dataset was built from, and verified byte-for-byte against the dataset's
`prompt` field before use:

- [`nccgroup/libslub`](https://github.com/nccgroup/libslub) @ `7732a54` — 23 tasks
- [`vladkens/twscrape`](https://github.com/vladkens/twscrape) @ `4f2ee7f` — 24 tasks

For each repo, `graphify extract --code-only` built a real AST-only graph
(no LLM cost) of that exact commit.

**Method:** for each of the 47 tasks, three completions were generated
(model: `qwen/qwen3.8-27b` via Groq) and scored against the gold line with
**Exact Match** and **Edit Similarity** (`difflib` ratio):

1. **no_context** — only the in-file prefix up to the cursor
2. **bm25_context** — the dataset's own precomputed BM25 cross-file chunks
   (the standard CrossCodeEval baseline)
3. **graphify_context** — cross-file code snippets retrieved by running
   `graphify query` against the real repo graph, using identifiers from the
   last few lines before the cursor as the query

**Results** (`tools/eval/crosscodeeval_results.json`, n=47):

| Condition | Exact Match | Edit Similarity |
|---|---|---|
| No context | 4.3% | 0.526 |
| BM25 context (standard baseline) | 12.8% | 0.607 |
| **graphify context** | **25.5%** | **0.687** |

**Finding:** graphify-retrieved context roughly **doubled exact-match
accuracy over the standard BM25 baseline** (25.5% vs 12.8%) and **6x'd it
over no context** (25.5% vs 4.3%), with the same pattern in edit similarity.
Graphify's graph-based retrieval (using real call/import edges, not just
term-frequency chunk matching) surfaced more directly relevant cross-file
code for the completion point than BM25 did on this sample.

## Limitations

- **Small samples.** 20 questions (retrieval) and 47 tasks (CrossCodeEval)
  from 2 repos — enough for a directional result, not a publishable claim.
  No repeated runs / variance estimate (single sampling temperature run each).
- **Free-tier models.** Groq's `qwen3.8-27b` and `gpt-oss-20b` are smaller
  open models, not frontier-tier. `gpt-oss-20b` also had an intermittent
  provider-side output-parsing failure on Groq (empty/400 responses on some
  reasoning-heavy generations) — it was used for graph extraction and
  community labeling (where retries/fallbacks already exist) but swapped for
  `qwen3.8-27b` for the completion benchmark once this was found.
- **Community labeling quality:** both Groq models tested for community
  naming echoed the community's dominant filename (e.g. "build.py") rather
  than synthesizing an abstract theme — a real, if minor, quality gap on the
  free-tier models tried.
- **Retrieval harness ground truth is hand-labeled and self-authored** (by
  reading the corpus), which is faster to build than an independently
  audited set but more subjective.
- **Groq's free tier** was used throughout — cost was $0, but production use
  would need a paid tier or a different backend for reliability/throughput.

## Reproducing

```bash
export OPENAI_API_KEY=<groq-key>
export OPENAI_BASE_URL=https://api.groq.com/openai/v1

# Retrieval accuracy harness (needs a graphify-out/graph.json built with
# `graphify extract --code-only` over the corpus you want to test)
uv run python tools/eval/retrieval_accuracy.py \
    --graph <path/to/graphify-out/graph.json> --corpus <path/to/corpus>

# CrossCodeEval completion accuracy (needs the parquet dataset and the
# task repos cloned at the commits given in `metadata.repository`)
uv run --with pandas --with pyarrow python tools/eval/crosscodeeval.py \
    --parquet <path/to/python.parquet> --repos-dir <path/to/repos> \
    --repo-map nccgroup-libslub-7732a54=libslub vladkens-twscrape-4f2ee7f=twscrape \
    --n-per-repo 24 --model qwen/qwen3.8-27b
```
