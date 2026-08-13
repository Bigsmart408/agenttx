# Step 17 — long-workload scaling and non-comparison measurements

## Scope

This step extends the deterministic 64-call workload experiment without adding
external baseline systems. It measures workload length, repeat variance, tracing
cost, snapshot storage, and the existing small synthetic scaling curve.

The long scaling script runs lengths 54, 64, and 96 with two repeats per point
for Bare, AgentTX without read tracing, and AgentTX full. The trajectory prefix,
fault location, repair location, and final correctness oracle are unchanged.

```bash
cd /home/pengpeng/agenttx
PYTHONPATH=src:. python3 experiments/scripts/bench_long_scaling.py \
  --lengths 54 64 96 --repeats 2
PYTHONPATH=src:. python3 experiments/scripts/bench_scaling.py
PYTHONPATH=src:. python3 experiments/scripts/bench_trace_overhead.py \
  --steps 20 --repeats 3
PYTHONPATH=src:. python3 experiments/scripts/bench_snapshot_storage.py
```

## Long-workload scaling result

| length | mode | wall mean (s) | stdev (s) | ms/step | failures | ledger effects | read effects |
|---:|---|---:|---:|---:|---:|---:|---:|
| 54 | bare | 4.234 | 0.378 | 78.403 | 2 | — | — |
| 54 | AgentTX no-trace | 19.954 | 0.408 | 369.509 | 2 | 46 | 10 |
| 54 | AgentTX full | 26.628 | 0.344 | 493.116 | 2 | 1,057 | 1,021 |
| 64 | bare | 3.370 | 0.106 | 52.663 | 2 | — | — |
| 64 | AgentTX no-trace | 23.105 | 0.295 | 361.008 | 2 | 56 | 15 |
| 64 | AgentTX full | 31.734 | 0.156 | 495.843 | 2 | 1,092 | 1,051 |
| 96 | bare | 3.636 | 0.006 | 37.877 | 2 | — | — |
| 96 | AgentTX no-trace | 33.378 | 0.574 | 347.683 | 2 | 88 | 31 |
| 96 | AgentTX full | 43.827 | 0.492 | 456.531 | 2 | 1,204 | 1,147 |

All AgentTX runs kept the host clean. The two non-zero results are the fixed
missing-file lookup and injected formatting CI failure. Full tracing adds about
109–135 ms/step over the no-trace ablation in this workload, while supplying the
read edges needed to remove the derived report during causal recovery.

## Complementary measurements

- The existing 5/10/20/40-call curve reports Bare at 2.6–3.5 ms/step, per-call
  `try` at 286–299 ms/step, and shared AgentTX at 329–342 ms/step.
- A fresh 20-step no-op trace experiment reports 295.63 ms/step without tracing
  and 319.43 ms/step with tracing: +23.80 ms/step, or 8.0%.
- The 128-file, 12-snapshot storage experiment reports 100.7 MB logical snapshot
  bytes and 9.1 MB physical unique bytes (ratio 0.0905), showing content
  deduplication but not eliminating directory/WAL traversal costs.
- The refreshed evidence suite passes all cascade rollback, selective/frontier commit,
  host-pollution, mistake-recovery, policy, and isolation checks.

## Interpretation and limits

The length points demonstrate that the harness remains usable as the agent
trajectory grows and expose the separate cost of automatic dependency tracing.
They are not a universal throughput claim: the workload is deterministic, the
VM is shared, and there are only two repeats per long point. Final OSDI numbers
still need repeated real-agent tasks with p50/p95 reporting.

Artifacts:

- `experiments/results/long_workload_scaling.{csv,json,md}`
- `experiments/results/scaling_curve.{csv,md}`
- `experiments/results/trace_overhead.{csv,md}`
- `experiments/results/snapshot_storage.{csv,md}`