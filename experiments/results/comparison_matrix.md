# AgentTX comparison matrix

This artifact separates runtime overhead from causal-recovery semantics.
The recovery workload is fixed: `a -> b`, independent `c`, then failure.

## Overhead (same 10-write trajectory, 3 repeats)

| mode | supported | wall mean (s) | stdev (s) | per step (ms) | note |
|---|:---:|---:|---:|---:|---|
| bare | True | 0.016411 | 0.000338 | 1.641 |  |
| per_call_try | True | 2.390725 | 0.006043 | 239.072 |  |
| session_try | True | 0.234114 | 0.002231 | 23.411 |  |
| shared_try | True | 2.35546 | 0.017137 | 235.546 |  |
| shared_checkpoint | True | 0.366428 | 0.001472 | 36.643 |  |
| bubblewrap | True | 0.008478 | 0.000189 | 0.848 |  |
| agenttx_without_read_tracing | True | 0.399126 | 0.001698 | 39.913 |  |
| agenttx_full | True | 0.488148 | 0.001926 | 48.815 |  |

## Recovery semantics

| mode | supported | host before recovery | host after recovery | causal retention correct | note |
|---|:---:|---|---|:---:|---|
| bare | True | `{'a.txt': True, 'b.txt': True, 'c.txt': True}` | `{'a.txt': False, 'b.txt': False, 'c.txt': False}` | False | host writes visible before recovery; whole cleanup loses c |
| per_call_try | True | `{'a.txt': False, 'b.txt': False, 'c.txt': False}` | `{'a.txt': False, 'b.txt': False, 'c.txt': False}` | False | isolated calls cannot pass a from step 0 to step 1 |
| session_try | True | `{'a.txt': False, 'b.txt': False, 'c.txt': False}` | `{'a.txt': False, 'b.txt': False, 'c.txt': False}` | False | one session can see state, but abort discards the whole session |
| shared_try | True | `{'a.txt': False, 'b.txt': False, 'c.txt': False}` | `{'a.txt': False, 'b.txt': False, 'c.txt': False}` | False | shared upperdir preserves state, but recovery is whole-session discard |
| shared_checkpoint | True | `{'a.txt': False, 'b.txt': False, 'c.txt': False}` | `{'a.txt': False, 'b.txt': False, 'c.txt': False}` | False | full checkpoint restore removes independent c with the failed prefix |
| bubblewrap | True | `{'a.txt': False, 'b.txt': False, 'c.txt': False}` | `{'a.txt': False, 'b.txt': False, 'c.txt': False}` | False | whole-session namespace abort; no causal retention |
| agenttx_without_read_tracing | True | `{'a.txt': False, 'b.txt': False, 'c.txt': False}` | `{'a.txt': False, 'b.txt': True, 'c.txt': True}` | False | causal targets=[0]; read tracing=False |
| agenttx_full | True | `{'a.txt': False, 'b.txt': False, 'c.txt': False}` | `{'a.txt': False, 'b.txt': False, 'c.txt': True}` | True | causal targets=[0, 1]; read tracing=True |
