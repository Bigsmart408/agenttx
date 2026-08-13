# Step 26 — End-to-end autonomous recovery token comparison

## Research question

Step 24 answers an attribution question: how many model tokens are spent only
because a coarse recovery policy discarded valid documents? It deliberately
excludes common diagnosis and validation work. The remaining user-facing
question is broader: **when the same autonomous agent receives the recovered
workspace, how many API tokens does the complete recovery loop consume under
each rollback granularity?**

Step 26 measures that second quantity. It charges every prompt and completion
token after the recovery policy runs, including tool schemas and results,
diagnosis, planning, validation, and regenerated content. Tokens spent before
the injected failure remain sunk cost and are not counted as savings.

## Controlled comparison

Every sample uses the Step 24 five-step fault DAG and varies each valid document
across 12, 24, and 48 numbered entries. The model, task prompt, tool schema,
maximum turns, commit boundary, validators, and initial workspace are identical
for all policies. Only the rollback target set changes.

| mode | rollback target | valid documents retained | expected regeneration |
|---|---|---:|---:|
| `causal` | fault root plus invalid descendants | 2 | 0 |
| `temporal_checkpoint` | fault root and every later step | 1 | 1 |
| `whole_branch_abort` | complete speculative branch | 0 | 2 |

The checkpoint and branch modes are controlled recovery-granularity emulations
on the same AgentTX overlay, not artifact-native executions of external
systems. This keeps the LLM comparison paired while avoiding claims about
unavailable implementations.

## Autonomous recovery protocol

After applying a policy, `LLMToolAgent` receives one common prompt. It must run
the repository tests, inspect the failures, recreate only invalid documents
through `write_file`, leave valid documents untouched, ensure the derived
artifact is absent, rerun validation as needed, and finish without committing.
The benchmark then validates the speculative state and performs the common
commit boundary itself.

A sample succeeds only when all of the following hold:

- the rollback target set exactly matches the selected policy;
- the agent regenerates exactly the documents lost by that policy;
- no additional rollback tool is called by the agent;
- the host workspace stays unchanged before the benchmark commit;
- both document contracts, the repaired pipeline, artifact removal, and tests
  are correct after commit.

## Metrics

Per sample, the benchmark records prompt, completion, and total API tokens;
model and tool calls; recovery-ledger steps; regenerated documents; policy
runtime; autonomous recovery wall time; success; and pre-commit host leakage.
Aggregates report mean, p50, and p95 for count and latency metrics. For each
coarse policy, AgentTX token savings are computed as:

```text
coarse policy full recovery-loop tokens - causal full recovery-loop tokens
```

The percentage uses the coarse policy as denominator. Prompt, completion, and
total savings are reported separately so lower output regeneration cannot hide
higher diagnostic context, or vice versa.

## Step 24 versus Step 26

| property | Step 24 replay attribution | Step 26 end-to-end comparison |
|---|---|---|
| Model scope | only known lost-document regeneration | complete autonomous recovery loop |
| Common diagnosis/validation tokens | excluded | included |
| Primary claim | causal retention avoids replay work | causal retention reduces total post-policy API usage |
| Main confounder | document generation variability | planning/tool-use/network variability |
| Interpretation | mechanism attribution | user-facing recovery cost |

The two experiments are complementary. Step 24 remains the cleaner causal
attribution result, while Step 26 tests whether that saving survives the noise
and common cost of an autonomous agent loop.

## Reproduction and artifacts

```bash
cd /home/pengpeng/agenttx
source ~/.agenttx_llm.env
PYTHONPATH=src:. python3 experiments/scripts/bench_token_end_to_end.py \
  --document-lines 12 24 48 --repeats 3 --max-turns 20
python3 motivation/plot_token_end_to_end.py
```

A credentialed run writes:

- `experiments/results/token_end_to_end.csv` — aggregated mean/p50/p95 data;
- `experiments/results/token_end_to_end_raw.csv` — per-sample data;
- `experiments/results/token_end_to_end.json` — complete machine-readable bundle;
- `experiments/results/token_end_to_end.md` — generated summary table;
- `motivation/FIG-Token-End-to-End.{pdf,png}` — paper-facing four-panel figure.

Before a credentialed run, execute the fail-closed substrate check:

```bash
PYTHONPATH=src:. python3 experiments/scripts/bench_token_end_to_end.py --preflight-only
```

The benchmark returns non-zero when `try`, `strace`, or a real overlay smoke
test is unavailable, and also when API credentials are absent. On the current
x86 Docker-overlay host, the check is intentionally root-only: run the command
through `sudo` while preserving the AgentTX conda `PATH`. The project bootstrap
reapplies the root/Ubuntu-5.4 `try` compatibility patch after a fresh clone.
Use `--allow-skip` only for an explicitly optional CI job; it produces no
results.

## Current execution status

The benchmark, plotting script, notebook, documentation, and structural tests
are implemented. Root preflight and the full 79-test suite pass on x86 after
the root-compatible `try` fix. A numeric sweep still requires API credentials;
no placeholder CSV, JSON, table, or figure is committed.
