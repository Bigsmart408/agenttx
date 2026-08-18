# Repeated comparison baselines

Fixed trajectory: 2 writes; independent fresh-workspace samples per mode: 1.

## Runtime distribution

| mode | samples | mean ms/step | p50 ms/step | p95 ms/step | p99 ms/step | stdev s |
|---|---:|---:|---:|---:|---:|---:|
| bare | 1 | 1.77 | 1.77 | 1.77 | 1.77 | 0.0 |
| per_call_try | 1 | 4865.507 | 4865.507 | 4865.507 | 4865.507 | 0.0 |
| session_try | 1 | 2381.428 | 2381.428 | 2381.428 | 2381.428 | 0.0 |
| shared_try | 1 | 4909.424 | 4909.424 | 4909.424 | 4909.424 | 0.0 |
| shared_checkpoint | 1 | 5086.145 | 5086.145 | 5086.145 | 5086.145 | 0.0 |
| bubblewrap | 1 | 6.91 | 6.91 | 6.91 | 6.91 | 0.0 |
| agenttx_without_read_tracing | 1 | 4957.758 | 4957.758 | 4957.758 | 4957.758 | 0.0 |
| agenttx_full | 1 | 4930.34 | 4930.34 | 4930.34 | 4930.34 | 0.0 |

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
