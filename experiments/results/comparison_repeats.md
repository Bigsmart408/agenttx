# Repeated comparison baselines

Fixed trajectory: 10 writes; independent fresh-workspace samples per mode: 50.

## Runtime distribution

| mode | samples | mean ms/step | p50 ms/step | p95 ms/step | p99 ms/step | stdev s |
|---|---:|---:|---:|---:|---:|---:|
| bare | 50 | 1.632 | 1.619 | 1.668 | 2.003 | 0.001021 |
| per_call_try | 50 | 245.365 | 245.742 | 250.565 | 252.112 | 0.028538 |
| session_try | 50 | 24.0 | 23.982 | 24.537 | 25.284 | 0.004286 |
| shared_try | 50 | 237.18 | 235.877 | 242.729 | 244.667 | 0.027955 |
| shared_checkpoint | 50 | 37.196 | 37.078 | 37.68 | 39.528 | 0.005418 |
| bubblewrap | 50 | 0.85 | 0.844 | 0.871 | 0.975 | 0.000289 |
| agenttx_without_read_tracing | 50 | 40.547 | 40.453 | 40.923 | 42.88 | 0.005283 |
| agenttx_full | 50 | 49.689 | 49.599 | 50.9 | 51.146 | 0.004711 |

## Recovery semantics

| mode | samples | supported rate | causal-correct count | causal-correct rate | host-clean rate |
|---|---:|---:|---:|---:|---:|
| bare | 50 | 1.0 | 0 | 0.0 | 0.0 |
| per_call_try | 50 | 1.0 | 0 | 0.0 | 1.0 |
| session_try | 50 | 1.0 | 0 | 0.0 | 1.0 |
| shared_try | 50 | 1.0 | 0 | 0.0 | 1.0 |
| shared_checkpoint | 50 | 1.0 | 0 | 0.0 | 1.0 |
| bubblewrap | 50 | 1.0 | 0 | 0.0 | 1.0 |
| agenttx_without_read_tracing | 50 | 1.0 | 0 | 0.0 | 1.0 |
| agenttx_full | 50 | 1.0 | 50 | 1.0 | 1.0 |

The repeated runtime rows quantify VM variance; the recovery rows test whether the semantic result is stable across fresh workspaces.
Bubblewrap is an isolation/abort lower bound, not a causal-recovery implementation.
