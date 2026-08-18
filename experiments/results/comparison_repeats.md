# Repeated comparison baselines

Fixed trajectory: 10 writes; independent fresh-workspace samples per mode: 50.

## Runtime distribution

| mode | samples | mean ms/step | p50 ms/step | p95 ms/step | p99 ms/step | stdev s |
|---|---:|---:|---:|---:|---:|---:|
| bare | 50 | 2.164 | 2.095 | 2.323 | 4.976 | 0.006046 |
| per_call_try | 50 | 193.726 | 193.826 | 201.427 | 202.746 | 0.040859 |
| session_try | 50 | 18.621 | 18.232 | 21.988 | 22.39 | 0.013168 |
| shared_try | 50 | 184.23 | 184.204 | 190.712 | 196.776 | 0.040779 |
| shared_checkpoint | 50 | 47.186 | 46.599 | 51.206 | 52.147 | 0.018666 |
| bubblewrap | 50 | 1.145 | 1.162 | 1.274 | 1.301 | 0.000939 |
| agenttx_without_read_tracing | 50 | 50.395 | 49.818 | 54.1 | 55.508 | 0.022186 |
| agenttx_full | 50 | 58.757 | 57.738 | 63.511 | 68.169 | 0.027546 |

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
