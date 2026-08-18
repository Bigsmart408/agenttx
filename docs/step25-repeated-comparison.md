# Step 25 — repeated baseline comparison

The original comparison matrix uses three runtime samples to keep the main
paper table short.  This companion experiment repeats every supported baseline
fifty times on fresh workspaces and reports mean, standard deviation, p50, p95,
and p99.  It also repeats the recovery trajectory fifty times and reports the rate at
which each policy satisfies the causal-retention predicate.

## Current canonical x86 result

The canonical result used by the paper is the 2026-08-18 x86 run on
`pengpeng-ubuntu-01-1` (Linux 5.4.0-216-generic, x86_64).  It uses a fixed
ten-write trajectory and 50 fresh workspaces per mode.  Full AgentTX uses
`strace` explicitly and reports 58.757 ms/step mean, with p50/p95/p99 of
57.738/63.511/68.169 ms and 50/50 causal-correct recoveries.  The complete
table and host record are in `docs/x86-comparison-20260818.md`; the raw files
are `experiments/results/comparison_repeats.{csv,json,md}`.  The older tiao2
table below is retained as historical context and is not used in the paper.

## Reproduction

```bash
cd /home/pengpeng/bfq/agenttx
sudo env TRY_SKIP_USERNS=1 \
  PATH=/home/pengpeng/miniforge3/envs/kitemguard311/bin:$PATH \
  PYTHONPATH=$PWD/src:$PWD \
  /home/pengpeng/miniforge3/envs/kitemguard311/bin/python \
  experiments/scripts/bench_comparison_repeats.py --repeats 50 --n 10
```

The new script does not overwrite `comparison_matrix.*`; it writes
`comparison_repeats.{csv,json,md}`.

The presentation notebook is `motivation/plot_comparison_repeats.ipynb`. It
follows the existing FAST/USENIX plotting conventions and writes four separate
bar charts: `FIG-Comparison-Mean`, `FIG-Comparison-P50`, `FIG-Comparison-P95`,
and `FIG-Comparison-P99` in both PNG and PDF formats.

## Latest tiao2 result

| mode | mean ms/step | p50 | p95 | p99 | causal-correct rate |
|---|---:|---:|---:|---:|---:|
| bare | 1.632 | 1.619 | 1.668 | 2.003 | 0% |
| per-call try | 245.365 | 245.742 | 250.565 | 252.112 | 0% |
| session try | 24.000 | 23.982 | 24.537 | 25.284 | 0% |
| shared try | 237.180 | 235.877 | 242.729 | 244.667 | 0% |
| shared checkpoint | 37.196 | 37.078 | 37.680 | 39.528 | 0% |
| bubblewrap | 0.850 | 0.844 | 0.871 | 0.975 | 0% |
| AgentTX, tracing off | 40.547 | 40.453 | 40.923 | 42.880 | 0% |
| AgentTX, full | 49.689 | 49.599 | 50.900 | 51.146 | 100% |

The p95/p99 values show that the ordering is not a one-shot artifact.  The
runtime variance is small relative to the gap between per-call isolation and
AgentTX; the causal result is stable across all fifty fresh workspaces.

## Agent identity and model scope

The deterministic workload is a scripted tool trajectory, not an LLM.  The
real-agent path uses AgentTX's `LLMToolAgent`: an OpenAI-compatible
`chat.completions` client with the fixed AgentTX tool schema (`write_file`,
`read_file`, `run_shell`, `inspect_ledger`, `rollback_causal`, and `finish`).
The prior successful live-agent/token runs used `deepseek-v4-flash`; the class can
also select `AGENTTX_MODEL`/`OPENAI_MODEL` and an OpenAI-compatible base URL.

For a paper claim about causal recovery, one model is enough for the systems
invariant only if the model is treated as a workload generator and the
invariant is checked independently.  For a stronger agent-level claim, add a
small model matrix (for example, DeepSeek, GPT-4o-mini, and one open-weight
OpenAI-compatible model), with three fresh tasks per model.  Keep the task,
tool schema, max turns, and recovery predicate fixed; report model name,
success rate, causal-target accuracy, host-leak rate, wall p50/p95, and tokens.
Do not pool models into one mean, because model planning variance is a separate
factor from runtime overhead.
