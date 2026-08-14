# AgentTX comparison matrix

This artifact separates runtime overhead from causal-recovery semantics.
The recovery workload is fixed: `a -> b`, independent `c`, then failure.

## Overhead (same 10-write trajectory, 3 repeats)

| mode | supported | wall mean (s) | stdev (s) | per step (ms) | note |
|---|:---:|---:|---:|---:|---|
| bare | True | 0.012823 | 0.000802 | 1.282 |  |
| per_call_try | True | 21.268921 | 0.298713 | 2126.892 |  |
| session_try | True | 1.870609 | 0.12319 | 187.061 |  |
| shared_try | True | 18.94783 | 0.392289 | 1894.783 |  |
| shared_checkpoint | True | 3.703467 | 0.058882 | 370.347 |  |
| bubblewrap | True | 0.010302 | 0.000721 | 1.03 |  |
| agenttx_without_read_tracing | True | 3.893193 | 0.104767 | 389.319 |  |
| agenttx_full | True | 4.114792 | 0.100626 | 411.479 |  |

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
