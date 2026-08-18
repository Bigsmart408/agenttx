# Repeated comparison baselines

Fixed trajectory: 10 writes; independent fresh-workspace samples per mode: 10.

## Runtime distribution

| mode | samples | mean ms/step | p50 ms/step | p95 ms/step | p99 ms/step | stdev s |
|---|---:|---:|---:|---:|---:|---:|
| bare | 10 | 1.285 | 1.248 | 1.532 | 1.63 | 0.001624 |
| per_call_try | 10 | 1702.051 | 1687.741 | 1817.432 | 1897.546 | 0.787619 |
| session_try | 10 | 169.76 | 169.136 | 173.899 | 174.899 | 0.02528 |
| shared_try | 10 | 1682.964 | 1683.071 | 1703.384 | 1704.803 | 0.181532 |
| shared_checkpoint | 10 | 361.043 | 360.245 | 373.003 | 373.319 | 0.08384 |
| bubblewrap | 10 | 1.06 | 1.07 | 1.193 | 1.202 | 0.000969 |
| agenttx_without_read_tracing | 10 | 361.746 | 363.371 | 367.946 | 369.903 | 0.054675 |
| agenttx_full | 10 | 371.918 | 373.42 | 380.349 | 380.92 | 0.080464 |

## Recovery semantics

| mode | samples | supported rate | causal-correct count | causal-correct rate | host-clean rate |
|---|---:|---:|---:|---:|---:|
| bare | 10 | 1.0 | 0 | 0.0 | 0.0 |
| per_call_try | 10 | 1.0 | 0 | 0.0 | 1.0 |
| session_try | 10 | 1.0 | 0 | 0.0 | 1.0 |
| shared_try | 10 | 1.0 | 0 | 0.0 | 1.0 |
| shared_checkpoint | 10 | 1.0 | 0 | 0.0 | 1.0 |
| bubblewrap | 10 | 1.0 | 0 | 0.0 | 1.0 |
| agenttx_without_read_tracing | 10 | 1.0 | 0 | 0.0 | 1.0 |
| agenttx_full | 10 | 1.0 | 10 | 1.0 | 1.0 |

The repeated runtime rows quantify VM variance; the recovery rows test whether the semantic result is stable across fresh workspaces.
Bubblewrap is an isolation/abort lower bound, not a causal-recovery implementation.
