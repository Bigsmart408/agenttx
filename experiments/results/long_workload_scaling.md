# Long workload scaling and variance

Lengths: 54, 64, 96, 128; modes: bare, agenttx_without_read_tracing, agenttx_full; repeats per point: 2.
The same deterministic workload prefix is used at every length; the fault and repair remain fixed.

| length | mode | wall mean (s) | wall stdev (s) | ms/step mean | ms/step stdev | failures | host polluted | ledger effects | read effects |
|---:|---|---:|---:|---:|---:|---:|:---:|---:|---:|
| 54 | bare | 3.279204 | 0.089653 | 60.726 | 1.66 | 2.0 | True | 0.0 | 0.0 |
| 54 | agenttx_without_read_tracing | 8.02529 | 0.237984 | 148.616 | 4.407 | 2.0 | False | 46.0 | 10.0 |
| 54 | agenttx_full | 11.899862 | 0.537167 | 220.368 | 9.948 | 2.0 | False | 1021.0 | 985.0 |
| 64 | bare | 3.276744 | 0.012654 | 51.199 | 0.198 | 2.0 | True | 0.0 | 0.0 |
| 64 | agenttx_without_read_tracing | 7.975755 | 0.154672 | 124.621 | 2.417 | 2.0 | False | 56.0 | 15.0 |
| 64 | agenttx_full | 12.442333 | 0.891134 | 194.411 | 13.924 | 2.0 | False | 1046.0 | 1005.0 |
| 96 | bare | 3.340365 | 0.011831 | 34.795 | 0.123 | 2.0 | True | 0.0 | 0.0 |
| 96 | agenttx_without_read_tracing | 9.226002 | 0.727908 | 96.104 | 7.582 | 2.0 | False | 88.0 | 31.0 |
| 96 | agenttx_full | 13.160496 | 0.363952 | 137.088 | 3.791 | 2.0 | False | 1126.0 | 1069.0 |
| 128 | bare | 3.453814 | 0.061714 | 26.983 | 0.482 | 2.0 | True | 0.0 | 0.0 |
| 128 | agenttx_without_read_tracing | 10.610559 | 0.085441 | 82.895 | 0.668 | 2.0 | False | 120.0 | 47.0 |
| 128 | agenttx_full | 13.779233 | 0.431427 | 107.65 | 3.371 | 2.0 | False | 1206.0 | 1133.0 |

Interpretation: the bare row is the execution lower bound; AgentTX rows include overlay, ledger, and (for full mode) strace tracing.
The workload is deterministic and VM-local; these measurements are not a universal throughput claim.
