# Running the DeepSeek evaluation

Step-by-step instructions for to-do items **A4** (CrossCodeEval) and **A5**
(agent efficiency) in [`docs/PROJECT_TODO.md`](../PROJECT_TODO.md). Follow them in
order. Everything runs on your own machine; only the model calls go to DeepSeek.

**Model:** `deepseek-flash` (DeepSeek-V4.1-Flash), **non-thinking mode**, at most
300 output tokens per completion (one line of code). The scripts set all of this.
Every condition is re-run on this one model, so the September Groq numbers stay
in the reports as historical results; don't mix the two in one table.

**Budget: $2, and that is the hard limit** (the account is prepaid and holds only
$2; nothing more will be added). Expected cost is about $0.45 for everything
off-peak and about $0.90 at peak (CrossCodeEval ~$0.30 / $0.60, agent runs up to
~$0.15 / $0.30). To stay safe:

| Step | Spending limit | How |
|---|---|---|
| A4 smoke test | $0.10 | `--max-cost 0.10` (already in the command) |
| A4 full run | $1.40 | `--max-cost 1.40` (already in the command) |
| A5 (pilot + full) | the rest, ~$0.50 | Check the balance on DeepSeek's **Usage** page first; start A5 only if at least $0.40 is left |

`--max-cost` counts spend at the **peak** price, so off-peak it stops well before
the money is actually spent. The count carries over when a run is resumed. If a run
stops with `STOPPED: spending limit reached`, the budget for that step is used up:
don't raise the limit; tell the project owner how far it got.

**Run off-peak** (half price): DeepSeek's peak hours are 01:00–04:00 and
06:00–10:00 UTC, Monday–Friday, which is **6:30–9:30 and 11:30–15:30 IST**.
Evenings, nights and weekends are off-peak.

Commands are for **PowerShell on Windows**, run from the repository root.

---

## 0. One-time setup

1. You need Git, [uv](https://docs.astral.sh/uv/) and Python 3.10+.
2. Clone and enter the repository:

   ```powershell
   git clone https://github.com/Charuvarthan-T/Synapse.git
   cd Synapse
   uv sync
   ```

3. **API key.** Get the DeepSeek key from the project owner privately (not in a
   chat, issue or commit). Set it for your user, then **open a new PowerShell
   window** so it takes effect:

   ```powershell
   setx DEEPSEEK_API_KEY "the-key"
   ```

   Never paste the key into a file in this repository.

4. **Original Graphify** (the baseline engine, release 0.9.88, the same commit as
   the earlier comparison):

   ```powershell
   git clone https://github.com/Graphify-Labs/graphify.git "$env:TEMP\graphify-upstream"
   git -C "$env:TEMP\graphify-upstream" checkout a9860f70dc3cc2e9c2d8cb923484cfa0592b9067
   ```

   The scripts look for it in `%TEMP%\graphify-upstream`. If you put it elsewhere,
   set `$env:GRAPHIFY_UPSTREAM` to that folder.

5. A shortcut used below (run it in each new PowerShell window):

   ```powershell
   function cceval { uv run --with pandas --with pyarrow --with openai python tools/eval/crosscodeeval.py @args }
   ```

---

## A4. CrossCodeEval: 6 repositories × 25 tasks × 4 conditions

The four conditions are no context, BM25 (the benchmark's baseline), original
Graphify, and Synapse. Each task's answer line is scored by exact match and edit
similarity.

### 1. Download the dataset (CrossCodeEval Python)

```powershell
uv run --with datasets --with pyarrow python -c "from datasets import load_dataset; ds = load_dataset('ZHENGRAN/cross_code_eval_python'); split = list(ds)[0]; ds[split].to_parquet('cceval_python.parquet'); print(split, len(ds[split]))"
```

This writes `cceval_python.parquet` in the repository root. Don't commit it.

### 2. Choose the repositories

```powershell
cceval list-repos --parquet cceval_python.parquet --top 15
```

Use these six:

- the two from the earlier runs: `nccgroup-libslub-7732a54` and `vladkens-twscrape-4f2ee7f`
- the **four repositories with the most tasks** in the list, skipping those two

Write the six ids down. You'll paste them into the next commands as `REPOS`:

```powershell
$REPOS = @("nccgroup-libslub-7732a54", "vladkens-twscrape-4f2ee7f", "<repo 3>", "<repo 4>", "<repo 5>", "<repo 6>")
```

### 3. Clone the repositories and build their graphs (local, free)

```powershell
cceval prepare --repos-dir "$env:TEMP\cceval-repos" --repos @REPOS
```

This clones each repository at the exact commit the dataset was built from and
builds its graph. It can take several minutes per repository. If a repository
fails to clone or build, replace it with the next largest one from the list and
run the command again (finished repositories are skipped).

### 4. Smoke test (a few cents): 1 task per repository

```powershell
cceval run --parquet cceval_python.parquet --repos-dir "$env:TEMP\cceval-repos" --repos @REPOS --n-per-repo 1 --max-cost 0.10 --out tools/eval/cceval_smoke.json
```

Check the output:

- every line shows `no=… bm25=… original_graphify=… synapse=…` with 0 or 1,
  **not `ERR`**;
- in the summary, `n_tasks_with_errors` is `0` and `models_served` names a
  DeepSeek Flash model;
- `original_graphify_context` and `synapse_context` have a
  `context_found_rate` above 0.

If you see `ERR`, open `tools/eval/cceval_smoke.json.progress.jsonl`; the `error`
field says why (wrong or missing key, no balance, network). Fix it and run again.
When it's clean, delete the smoke files:

```powershell
Remove-Item tools/eval/cceval_smoke.json, tools/eval/cceval_smoke.json.progress.jsonl
```

### 5. The full run (off-peak)

```powershell
cceval run --parquet cceval_python.parquet --repos-dir "$env:TEMP\cceval-repos" --repos @REPOS --n-per-repo 25 --max-cost 1.40 --out tools/eval/crosscodeeval_results_deepseek.json
```

- About 150 tasks × 4 calls = 600 calls; roughly 20–40 minutes.
- **If it stops** (closed laptop, network error), run the same command again. It
  resumes from `crosscodeeval_results_deepseek.json.progress.jsonl`.
- Tasks where any condition still fails after retries are left out of **every**
  condition, so all four are scored on the same tasks. The summary reports how
  many (`n_tasks_with_errors`); if it's more than a handful, re-run later.

**Result:** `tools/eval/crosscodeeval_results_deepseek.json`: the summary, the
environment (model, commits, token usage, estimated cost) and every task.

---

## A5. Agent efficiency: 4 setups × 20 questions

The four setups are no graph, original Graphify, Synapse default, and Synapse full.

**Before you start:** open DeepSeek's **Usage** page and check the remaining balance.
Continue only if at least **$0.40** is left (A5 costs about $0.15 off-peak, $0.30
at peak). Run it off-peak.

1. Build the frozen corpus and its graph (local, free; also re-runs the retrieval
   benchmark):

   ```powershell
   uv run python tools/eval/final_retrieval.py
   ```

2. Pilot: 1 question per setup.

   ```powershell
   $env:SYNAPSE_EVAL_PROVIDER = "deepseek"
   uv run python tools/eval/run_groq_token_benchmark.py
   ```

   Check that `tools/eval/results/agent_efficiency/deepseek/pilot_graph_instruction/summary.json`
   has no `stop_reason` and that each setup has a completed task.

3. Full run: all 20 questions (off-peak).

   ```powershell
   $env:SYNAPSE_EVAL_PROVIDER = "deepseek"
   uv run python tools/eval/run_groq_token_benchmark.py --all
   ```

**Result:** `tools/eval/results/agent_efficiency/deepseek/full_graph_instruction/`
(`summary.json`, `environment.json`, `tasks.jsonl`, `calls.jsonl`).

Without `SYNAPSE_EVAL_PROVIDER` the script uses Groq as before, so the September
Groq results are untouched.

---

## When you're done

1. Check the spend on DeepSeek's **Usage** page and note it.
2. Commit only the result files, on a new branch:

   ```powershell
   git checkout -b eval/deepseek-results
   git add tools/eval/crosscodeeval_results_deepseek.json tools/eval/crosscodeeval_results_deepseek.json.progress.jsonl tools/eval/results/agent_efficiency/deepseek
   git commit -m "eval: CrossCodeEval and agent-efficiency results on DeepSeek Flash"
   git push -u origin eval/deepseek-results
   ```

3. Tell the project owner. Writing the results into the reports is to-do item A6.

Don't commit `cceval_python.parquet`, the cloned repositories, or anything
containing the API key.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `DEEPSEEK_API_KEY is not set` | Run `setx` again and open a **new** PowerShell window. |
| Errors mentioning balance or HTTP 402 | The $2 is used up. Stop and tell the project owner; don't top up. The finished tasks are saved. |
| `STOPPED: spending limit reached` | That step's budget is used up. Don't raise `--max-cost`; tell the project owner how far it got. |
| `Original Graphify checkout missing` | Do step 0.4, or set `$env:GRAPHIFY_UPSTREAM`. |
| `No graph for [...]` | Run the `prepare` command (step A4.3) for those repositories. |
| `context_found_rate` is 0 for a graph condition | The graph for that repository is empty or broken: `cceval prepare ... --rebuild`. |
| `cceval` is not recognized | Define the shortcut again (step 0.5); it lasts one window. |
