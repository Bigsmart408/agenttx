# Repeated comparison baselines

Fixed trajectory: 2 writes; independent fresh-workspace samples per mode: 1.

## Runtime distribution

| mode | samples | mean ms/step | p50 ms/step | p95 ms/step | p99 ms/step | stdev s |
|---|---:|---:|---:|---:|---:|---:|
| bare | 1 | 2.543 | 2.543 | 2.543 | 2.543 | 0.0 |
| per_call_try | 1 | 192.814 | 192.814 | 192.814 | 192.814 | 0.0 |
| session_try | 1 | 105.473 | 105.473 | 105.473 | 105.473 | 0.0 |
| shared_try | 1 | 175.855 | 175.855 | 175.855 | 175.855 | 0.0 |
| shared_checkpoint | 1 | 211.401 | 211.401 | 211.401 | 211.401 | 0.0 |
| bubblewrap | 1 | 6.792 | 6.792 | 6.792 | 6.792 | 0.0 |
| agenttx_without_read_tracing | 1 | 249.754 | 249.754 | 249.754 | 249.754 | 0.0 |
| agenttx_full | 1 | 231.218 | 231.218 | 231.218 | 231.218 | 0.0 |

## Recovery semantics

| mode | samples | supported rate | causal-correct count | causal-correct rate | host-clean rate |
|---|---:|---:|---:|---:|---:|
| bare | 1 | 1.0 | 0 | 0.0 | 0.0 |
| per_call_try | 1 | 1.0 | 0 | 0.0 | 1.0 |
| session_try | 1 | 1.0 | 0 | 0.0 | 1.0 |
| shared_try | 1 | 1.0 | 0 | 0.0 | 1.0 |
| shared_checkpoint | 1 | 1.0 | 0 | 0.0 | 1.0 |
| bubblewrap | 1 | 1.0 | 0 | 0.0 | 1.0 |
| agenttx_without_read_tracing | 1 | 1.0 | 0 | 0.0 | 1.0 |
| agenttx_full | 1 | 1.0 | 1 | 1.0 | 1.0 |

The repeated runtime rows quantify VM variance; the recovery rows test whether the semantic result is stable across fresh workspaces.
Bubblewrap is an isolation/abort lower bound, not a causal-recovery implementation.
