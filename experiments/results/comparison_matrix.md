# AgentTX comparison matrix

This artifact separates runtime overhead from causal-recovery semantics.
The recovery workload is fixed: `a -> b`, independent `c`, then failure.

## Overhead (same 10-write trajectory, 3 repeats)

| mode | supported | wall mean (s) | stdev (s) | per step (ms) | note |
|---|:---:|---:|---:|---:|---|
| bare | True | 0.030735 | 0.001041 | 3.073 |  |
| per_call_try | True | 2.608234 | 0.021426 | 260.823 |  |
| session_try | True | 0.25024 | 0.025951 | 25.024 |  |
| shared_try | True | 2.552706 | 0.066535 | 255.271 |  |
| shared_checkpoint | True | 2.616077 | 0.068924 | 261.608 |  |
| bubblewrap | True | 0.017312 | 0.000762 | 1.731 |  |
| agenttx_without_read_tracing | True | 2.761809 | 0.080699 | 276.181 |  |
| agenttx_full | True | 3.077769 | 0.107471 | 307.777 |  |

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
