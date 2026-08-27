# Repeated comparison baselines

Fixed trajectory: 10 writes; independent fresh-workspace samples per mode: 50.

## Runtime distribution

| mode | samples | mean ms/step | p50 ms/step | p95 ms/step | p99 ms/step | stdev s |
|---|---:|---:|---:|---:|---:|---:|
| bare | 50 | 1.481 | 1.184 | 1.497 | 8.086 | 0.013667 |
| per_call_try | 50 | 1843.523 | 1775.882 | 2081.563 | 2092.963 | 1.585622 |
| session_try | 50 | 198.783 | 192.409 | 255.19 | 267.591 | 0.27668 |
| shared_try | 50 | 2008.148 | 2001.217 | 2089.847 | 2098.075 | 0.658407 |
| shared_checkpoint | 50 | 400.052 | 395.958 | 459.678 | 469.125 | 0.300602 |
| bubblewrap | 50 | 1.416 | 1.115 | 3.628 | 6.263 | 0.011833 |
| agenttx_without_read_tracing | 50 | 368.688 | 364.666 | 398.128 | 422.24 | 0.148897 |
| agenttx_full | 50 | 382.191 | 377.387 | 420.63 | 426.298 | 0.164543 |

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
