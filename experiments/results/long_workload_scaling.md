# Long workload scaling and variance

Lengths: 54, 64, 96; modes: bare, agenttx_without_read_tracing, agenttx_full; repeats per point: 2.
The same deterministic workload prefix is used at every length; the fault and repair remain fixed.

| length | mode | wall mean (s) | wall stdev (s) | ms/step mean | ms/step stdev | failures | host polluted | ledger effects | read effects |
|---:|---|---:|---:|---:|---:|---:|:---:|---:|---:|
| 54 | bare | 2.758661 | 0.030227 | 51.086 | 0.56 | 2.0 | True | 0.0 | 0.0 |
| 54 | agenttx_without_read_tracing | 7.146729 | 0.074374 | 132.347 | 1.377 | 2.0 | False | 46.0 | 10.0 |
| 54 | agenttx_full | 10.308414 | 0.152786 | 190.897 | 2.829 | 2.0 | False | 1041.0 | 1005.0 |
| 64 | bare | 2.82984 | 0.062173 | 44.216 | 0.971 | 2.0 | True | 0.0 | 0.0 |
| 64 | agenttx_without_read_tracing | 7.39154 | 0.066953 | 115.493 | 1.046 | 2.0 | False | 56.0 | 15.0 |
| 64 | agenttx_full | 10.640573 | 0.113483 | 166.259 | 1.773 | 2.0 | False | 1066.0 | 1025.0 |
| 96 | bare | 2.913818 | 0.023412 | 30.352 | 0.244 | 2.0 | True | 0.0 | 0.0 |
| 96 | agenttx_without_read_tracing | 8.311881 | 0.236684 | 86.582 | 2.465 | 2.0 | False | 88.0 | 31.0 |
| 96 | agenttx_full | 11.591164 | 0.591109 | 120.741 | 6.157 | 2.0 | False | 1146.0 | 1089.0 |

Interpretation: the bare row is the execution lower bound; AgentTX rows include overlay, ledger, and (for full mode) strace tracing.
The workload is deterministic and VM-local; these measurements are not a universal throughput claim.
