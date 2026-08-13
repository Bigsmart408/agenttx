# AgentTX motivation experiments

This directory turns the iterative AgentTX optimization work into a reproducible
paper-motivation section. The central observation is that a long coding-agent
trajectory multiplies per-tool overhead: syscall tracing, temporary command
scripts, blob-store maintenance, shell parsing, try namespace setup, and full
upperdir traversal.

For a Chinese, paper-oriented explanation of terminology, baseline semantics,
experimental design, results, and claim boundaries, start with
`docs/experiments-explained.md`.

## Reproduce the current comparison

The runtime comparison reuses the deterministic 64-call workload and current
baseline implementations:

```bash
PYTHONPATH=src:. python3 motivation/bench_optimization_comparison.py \
  --length 64 --repeats 2
```

It writes `experiments/results/motivation_runtime_comparison.{csv,json,md}` for:
bare execution, per-call try, shared try, shared checkpoint, AgentTX without
read tracing, and full AgentTX.

## Summarize all optimization iterations

The history summarizer consumes the recorded before/after measurements and joins
the deterministic and real-agent robustness bundles:

```bash
PYTHONPATH=src:. python3 motivation/summarize_optimization_history.py
```

It writes `experiments/results/motivation_optimization_history.{csv,json,md}`.
The source pre-images remain under
`src/agenttx/optimization_history/iteration_*` so each optimization is auditable.

## Notebook views

The notebooks use compact FAST/USENIX multi-panel line plots,
Roman-compatible fonts, and 300-dpi PDF export, but consume AgentTX-specific
result artifacts:

- `plot.ipynb` combines the before/after optimization chain with the current
  bare/per-call/shared/AgentTX baselines.
- `plot_tail.ipynb` shows deterministic p50/p95 tails alongside repeated
  real-agent task latency and success.
- `plot_scaling.ipynb` and `plot_tail_scaling.ipynb` report length scaling.
- `plot_causal_retention.ipynb` quantifies the central recovery claim: useful
  work retained, invalid descendants removed, joint recovery utility, and
  rollback p95 across controlled effect DAGs.
- `plot_token_recovery.ipynb` connects retained work to real DeepSeek replay
  tokens, regenerated documents, and p95 recovery latency.
- `plot_token_end_to_end.ipynb` compares complete autonomous recovery prompt,
  completion, and total API tokens plus recovery p95 across the same policies.
- `plot_real_agent_recovery.ipynb` exposes the live agent's recovery decision,
  trajectory variability, rollback targets, and semantic success conditions.
- `plot_bpf_trace.ipynb` compares the eBPF tracer against strace and no
  tracing: per-step mean/p50/p95, the incremental tracing tax, and verified
  READ/NEGATIVE capture fidelity.
- `plot_robustness.ipynb` covers p50/p95 latency, worker-crash fallback,
  long-session resume, and concurrent-agent isolation without combining their
  heterogeneous latencies into one score.
- `report.ipynb` is a short narrative notebook that tabulates the same
  measurements for paper drafting.

From the repository root, run them with a notebook environment that has
`jupyter`, `pandas`, `numpy`, and `matplotlib` installed:

```bash
jupyter nbconvert --to notebook --execute motivation/plot.ipynb \
  --output motivation/plot.executed.ipynb
jupyter nbconvert --to notebook --execute motivation/plot_tail.ipynb \
  --output motivation/plot_tail.executed.ipynb
jupyter nbconvert --to notebook --execute motivation/plot_causal_retention.ipynb \
  --output motivation/plot_causal_retention.executed.ipynb
jupyter nbconvert --to notebook --execute motivation/plot_token_recovery.ipynb \
  --output motivation/plot_token_recovery.executed.ipynb
jupyter nbconvert --to notebook --execute motivation/plot_token_end_to_end.ipynb \
  --output motivation/plot_token_end_to_end.executed.ipynb
jupyter nbconvert --to notebook --execute motivation/plot_real_agent_recovery.ipynb \
  --output motivation/plot_real_agent_recovery.executed.ipynb
jupyter nbconvert --to notebook --execute motivation/plot_bpf_trace.ipynb \
  --output motivation/plot_bpf_trace.executed.ipynb
jupyter nbconvert --to notebook --execute motivation/plot_robustness.ipynb \
  --output motivation/plot_robustness.executed.ipynb
jupyter nbconvert --to notebook --execute motivation/report.ipynb \
  --output motivation/report.executed.ipynb
```

The causal-retention notebook writes `motivation/FIG-Causal-Retention.{pdf,png}`;
the evaluation notebooks write `FIG-Token-Recovery.{pdf,png}`,
`FIG-Token-End-to-End.{pdf,png}`, `FIG-Real-Agent-Recovery.{pdf,png}`,
`FIG-Bpf-Trace.{pdf,png}`, and
`FIG-Robustness.{pdf,png}`;
the report notebook is intended for interactive inspection and does not
duplicate the source result files.

## eBPF dependency-tracing overhead

The eBPF backend replaces strace for workspace read/negative-lookup capture
by attaching syscall tracepoints (with an optional `vfs_open` kprobe for
symlink-alias resolution on bpftrace >= 0.10). It requires root and bpftrace;
the runtime falls back to strace automatically in `auto` mode. The benchmark
compares no tracing, strace, and eBPF on a read-heavy step and verifies that
every traced step captured both the READ and the NEGATIVE effect:

```bash
PYTHONPATH=src:. python3 experiments/scripts/bench_bpf_trace.py \
  --steps 20 --repeats 3 --workload read
python3 motivation/plot_bpf_trace.py
```

It writes `bpf_trace_overhead.{csv,json,md}` and `FIG-Bpf-Trace.{pdf,png}`.
Without root/bpftrace the benchmark exits non-zero with a clear message and
writes nothing. Design and claim boundaries are in
`docs/step27-bpf-dependency-tracing.md`.

## Quantitative causal-retention experiment

The controlled effect-DAG benchmark sweeps trajectory length, dependency
shape, fault position, and independent-work ratio. Causal, temporal, and
whole-session recovery receive the same declared read effects; a fourth mode
deliberately removes dependency capture to isolate why tracing/manifests are
necessary.

```bash
PYTHONPATH=src:. python experiments/scripts/bench_causal_retention.py --repeats 3
```

It writes `causal_retention.{csv,json,md}` plus per-repeat raw CSV data. The
benchmark checks both the speculative merged view and the host state after
commit, so retention numbers are tied to actual AgentTX recovery rather than a
symbolic DAG simulation.

## Real-agent recovery experiment

The live recovery benchmark adds the decision-making layer: a seeded failure
contains one faulty producer, independent later work, a derived artifact, and a
failing test. The LLM must inspect the AgentTX ledger, select the faulty step,
invoke causal rollback, and finish with the independent work preserved.

```bash
PYTHONPATH=src:. python experiments/scripts/bench_real_agent_recovery.py \
  --repeats 3 --max-turns 30
```

Results are stored in `real_agent_recovery.{csv,json,md}`. Credentials are read
from the local environment and never written to an artifact.

## Avoided token replay after recovery

The retention experiment connects causal filesystem recovery to LLM cost. A
fixed failure DAG contains valid work before and after the fault. AgentTX causal
rollback retains both documents; an optimistic temporal checkpoint loses the
later one; whole branch/session abort loses both. Real DeepSeek `write_file`
calls regenerate only the lost artifacts, and response usage reports the exact
replay tokens.

```bash
PYTHONPATH=src:. /home/pengpeng/miniconda3/envs/agenttx/bin/python \
  experiments/scripts/bench_token_recovery.py \
  --document-lines 12 24 48 --repeats 3
```

The central metric is *avoided replay tokens*, not total end-to-end recovery
tokens. Results live in `token_recovery.{csv,json,md}` and
`token_recovery_raw.csv`; design and limitations are in
`docs/step24-token-replay-evaluation.md`.

## Complete autonomous recovery token comparison

Step 26 runs the same post-policy task through the full `LLMToolAgent` loop and
charges diagnosis, prompt/tool context, planning, validation, and regenerated
content. This complements rather than replaces the isolated Step 24 replay
attribution.

```bash
PYTHONPATH=src:. python3 experiments/scripts/bench_token_end_to_end.py \
  --document-lines 12 24 48 --repeats 3 --max-turns 20
python3 motivation/plot_token_end_to_end.py
```

A credentialed run writes `token_end_to_end.{csv,json,md}` plus raw CSV and the
paper figure. The current VM has no API credentials, so numeric results and
figures are intentionally absent. See
`docs/step26-end-to-end-token-comparison.md`.

## FAST-style multi-length line experiments

The FAST'25 Pan paper uses compact 2x2 panels, marker-and-line curves, and
point annotations over several workload parameters. AgentTX now has the same
presentation for a more informative length sweep:

```bash
PYTHONPATH=src:. python motivation/bench_scaling.py \
  --lengths 54 64 96 --repeats 2
PYTHONPATH=src:. python motivation/bench_tail_scaling.py \
  --lengths 54 64 96 --repeats 2
```

`plot_scaling.ipynb` reads `motivation_scaling.csv` and shows per-call cost,
end-to-end cost, overhead relative to bare execution, and the read-trace
penalty. `plot_tail_scaling.ipynb` reads `motivation_tail_scaling.csv` and
shows p50/p95 call and trajectory tails. Their paper figures are
`FIG-Motivation-Scaling.{pdf,png}` and
`FIG-Motivation-Tail-Scaling.{pdf,png}`.

## Motivation storyline

1. The unoptimized path traces every tool and pays setup/teardown repeatedly.
2. Trusted read/write effects remove redundant tracing while preserving causal dependencies.
3. Persistent command scripts, deferred blob GC, and direct execution remove repeated userspace work.
4. A persistent try worker removes per-call namespace/overlay setup and produces the largest endpoint reduction.
5. Incremental upperdir snapshots reduce snapshot-stage traversal without weakening boundary fallbacks.
6. Crash injection, long-session reload, concurrent-agent isolation, and real-agent repeats show that the optimized path remains recoverable and useful under realistic execution.

The numbers are VM-local directional measurements. The motivation claim is the
cost decomposition and the preservation of correctness, not a universal latency
number.
