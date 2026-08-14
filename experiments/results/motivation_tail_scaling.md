# AgentTX motivation tail scaling

p50/p95 tail measurements over several deterministic workload lengths.

| length | mode | step p50 (ms) | step p95 (ms) | run p50 (ms) | run p95 (ms) | failure rate |
|---:|---|---:|---:|---:|---:|---:|
| 54 | agenttx_without_read_tracing | 16.128 | 304.555 | 5164.635 | 5221.681 | 0.037037 |
| 54 | agenttx_full | 19.496 | 632.163 | 8932.783 | 9402.304 | 0.037037 |
| 64 | agenttx_without_read_tracing | 16.646 | 306.105 | 5591.259 | 5718.827 | 0.03125 |
| 64 | agenttx_full | 19.975 | 624.587 | 8799.933 | 8918.493 | 0.03125 |
| 96 | agenttx_without_read_tracing | 16.788 | 301.92 | 6113.378 | 6141.489 | 0.020833 |
| 96 | agenttx_full | 20.748 | 596.413 | 9545.836 | 9698.088 | 0.020833 |
