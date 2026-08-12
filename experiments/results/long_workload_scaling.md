# Long workload scaling and variance

Lengths: 54, 64, 96; modes: bare, agenttx_without_read_tracing, agenttx_full; repeats per point: 2.
The same deterministic workload prefix is used at every length; the fault and repair remain fixed.

| length | mode | wall mean (s) | wall stdev (s) | ms/step mean | ms/step stdev | failures | host polluted | ledger effects | read effects |
|---:|---|---:|---:|---:|---:|---:|:---:|---:|---:|
| 54 | bare | 4.060974 | 0.024163 | 75.203 | 0.447 | 2.0 | True | 0.0 | 0.0 |
| 54 | agenttx_without_read_tracing | 5.009508 | 0.023944 | 92.769 | 0.443 | 2.0 | False | 46.0 | 10.0 |
| 54 | agenttx_full | 9.560636 | 0.036 | 177.049 | 0.667 | 2.0 | False | 1041.0 | 1005.0 |
| 64 | bare | 4.088569 | 0.014653 | 63.884 | 0.229 | 2.0 | True | 0.0 | 0.0 |
| 64 | agenttx_without_read_tracing | 5.180771 | 0.007075 | 80.95 | 0.111 | 2.0 | False | 56.0 | 15.0 |
| 64 | agenttx_full | 9.828033 | 0.00453 | 153.563 | 0.071 | 2.0 | False | 1066.0 | 1025.0 |
| 96 | bare | 4.202223 | 0.003694 | 43.773 | 0.038 | 2.0 | True | 0.0 | 0.0 |
| 96 | agenttx_without_read_tracing | 5.881631 | 0.003761 | 61.267 | 0.039 | 2.0 | False | 88.0 | 31.0 |
| 96 | agenttx_full | 11.053426 | 0.185324 | 115.14 | 1.93 | 2.0 | False | 1146.0 | 1089.0 |

Interpretation: the bare row is the execution lower bound; AgentTX rows include overlay, ledger, and (for full mode) strace tracing.
The workload is deterministic and VM-local; these measurements are not a universal throughput claim.
