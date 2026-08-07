# Long workload scaling and variance

Lengths: 54, 64, 96; modes: bare, agenttx_without_read_tracing, agenttx_full; repeats per point: 2.
The same deterministic workload prefix is used at every length; the fault and repair remain fixed.

| length | mode | wall mean (s) | wall stdev (s) | ms/step mean | ms/step stdev | failures | host polluted | ledger effects | read effects |
|---:|---|---:|---:|---:|---:|---:|:---:|---:|---:|
| 54 | bare | 4.23378 | 0.377666 | 78.403 | 6.994 | 2.0 | True | 0.0 | 0.0 |
| 54 | agenttx_without_read_tracing | 19.953507 | 0.40816 | 369.509 | 7.559 | 2.0 | False | 46.0 | 10.0 |
| 54 | agenttx_full | 26.628247 | 0.343609 | 493.116 | 6.363 | 2.0 | False | 1057.0 | 1021.0 |
| 64 | bare | 3.370419 | 0.106084 | 52.663 | 1.658 | 2.0 | True | 0.0 | 0.0 |
| 64 | agenttx_without_read_tracing | 23.104534 | 0.29462 | 361.008 | 4.603 | 2.0 | False | 56.0 | 15.0 |
| 64 | agenttx_full | 31.733925 | 0.155866 | 495.843 | 2.435 | 2.0 | False | 1092.0 | 1051.0 |
| 96 | bare | 3.636195 | 0.006193 | 37.877 | 0.065 | 2.0 | True | 0.0 | 0.0 |
| 96 | agenttx_without_read_tracing | 33.377581 | 0.573858 | 347.683 | 5.978 | 2.0 | False | 88.0 | 31.0 |
| 96 | agenttx_full | 43.827021 | 0.491808 | 456.531 | 5.123 | 2.0 | False | 1204.0 | 1147.0 |

Interpretation: the bare row is the execution lower bound; AgentTX rows include overlay, ledger, and (for full mode) strace tracing.
The workload is deterministic and VM-local; these measurements are not a universal throughput claim.
