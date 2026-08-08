# AgentTX motivation experiments

This directory turns the iterative AgentTX optimization work into a reproducible
paper-motivation section. The central observation is that a long coding-agent
trajectory multiplies per-tool overhead: syscall tracing, temporary command
scripts, blob-store maintenance, shell parsing, try namespace setup, and full
upperdir traversal.

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

The three notebooks use the OSDI-Pa paper plotting conventions (compact
multi-panel figures, `bmh` grid styling, Roman-compatible fonts, and 300-dpi
PDF export), but consume AgentTX-specific result artifacts:

- `plot.ipynb` combines the before/after optimization chain with the current
  bare/per-call/shared/AgentTX baselines.
- `plot_tail.ipynb` shows deterministic p50/p95 tails alongside repeated
  real-agent task latency and success.
- `report.ipynb` is a short narrative notebook that tabulates the same
  measurements for paper drafting.

From the repository root, run them with a notebook environment that has
`jupyter`, `pandas`, `numpy`, and `matplotlib` installed:

```bash
jupyter nbconvert --to notebook --execute motivation/plot.ipynb \
  --output motivation/plot.executed.ipynb
jupyter nbconvert --to notebook --execute motivation/plot_tail.ipynb \
  --output motivation/plot_tail.executed.ipynb
jupyter nbconvert --to notebook --execute motivation/report.ipynb \
  --output motivation/report.executed.ipynb
```

The plotting notebooks write `motivation/FIG-Motivation-Optimization.pdf`
and `motivation/FIG-Motivation-Tail.pdf`; the report notebook is intended for
interactive inspection and does not duplicate the source result files.

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
