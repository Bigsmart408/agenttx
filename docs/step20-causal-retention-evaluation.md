# Step 20: Quantitative causal-retention evaluation

## Question

AgentTX claims that recovery should remove a faulty action and its descendants
without discarding unrelated work produced later in the same agent trajectory.
The earlier three-node example established feasibility, but it did not answer
whether this property survives longer or differently shaped dependency graphs,
nor did it separate dependency capture from rollback policy.

This experiment asks:

1. How much useful independent work survives recovery?
2. Are all causally invalid descendants removed?
3. Does the result depend on DAG size, shape, fault position, or the amount of
   independent work?
4. What latency does selective reconstruction add?

## Workload

`experiments/workloads/causal_retention_dag.py` creates topologically ordered
file-producing tool calls. Each output has a unique path, avoiding artificial
same-path write conflicts. The generator supports chain, fan-out, and layered
dependency shapes. It interleaves independent calls with descendants of one
designated faulty producer and computes the expected transitive rollback set
from the generated graph.

The default sweep covers:

- DAG sizes: 16, 32, and 64 calls;
- dependency shapes: chain, fan-out, and layered;
- requested fault positions: 10%, 50%, and 75%;
- requested post-fault independent fractions: 25%, 50%, and 75%;
- three fresh-session repeats per configuration.

## Compared recovery policies

- `causal`: AgentTX `rollback_causal`, with controlled declared READ effects;
- `temporal`: rollback from the faulty step through the end of the trajectory;
- `whole_session`: discard all speculative steps;
- `causal_without_dependencies`: AgentTX causal rollback after deliberately
  removing read-dependency capture.

The first three policies receive identical declared read effects. This makes
the comparison a recovery-policy experiment rather than a `strace` overhead
experiment. The fourth policy is an explicit dependency-capture ablation. An
optional `causal_traced` mode exercises automatic `strace` capture separately.

## Metrics and correctness checks

- **rollback precision**: expected invalid targets / all rolled-back steps;
- **rollback recall**: rolled-back invalid targets / all expected targets;
- **independent retention**: surviving independent steps / all independent
  steps;
- **target removal**: removed invalid steps / all invalid steps;
- **recovery utility**: independent retention multiplied by target removal;
- rollback latency (mean, standard deviation, and p95);
- final correctness and pre-commit host cleanliness.

Every run executes real AgentTX tool calls in the shared overlay. After
recovery, the benchmark checks the merged view, commits all remaining active
steps, and checks the physical host workspace. Thus the reported result cannot
pass through symbolic ledger scoring alone.

## Reproduce

```bash
cd /home/pengpeng/agenttx
export PYTHONPATH=src:.
/home/pengpeng/miniconda3/envs/agenttx/bin/python \
  experiments/scripts/bench_causal_retention.py --repeats 3
```

Outputs:

- `experiments/results/causal_retention.csv` (aggregates);
- `experiments/results/causal_retention_raw.csv` (per-run observations);
- `experiments/results/causal_retention.json` (machine-readable bundle);
- `experiments/results/causal_retention.md` (compact table);
- `motivation/plot_causal_retention.ipynb` and
  `motivation/FIG-Causal-Retention.{pdf,png}` (paper view).

## Result

All 144 runs kept the physical host clean before commit. Across every tested
configuration, causal recovery retained 100% of independent work, removed 100%
of invalid descendants, and reached a 100% final-correctness rate. At 64 calls,
its rollback p95 was 272.7 ms.

The baselines expose the two distinct failure modes. At 64 calls, temporal
rollback removed every invalid descendant but retained only 41.0% of independent
work; whole-session discard retained 0%. The dependency-capture ablation kept
100% of nominally independent work but removed only 4.0% of the invalid causal
subgraph. Consequently, only dependency-aware causal rollback satisfies both
halves of recovery correctness at the same time.

## Interpretation boundary

The experiment establishes the semantics of path-based causal recovery on
controlled filesystem-effect DAGs. It does not claim complete dependency
capture for every Linux syscall or non-filesystem side effect. Automatic trace
coverage, aliasing beyond current symlink handling, and network/cloud effects
remain separate limitations.
