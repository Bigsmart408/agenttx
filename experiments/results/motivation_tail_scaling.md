# AgentTX motivation tail scaling

p50/p95 tail measurements over several deterministic workload lengths.

| length | mode | step p50 (ms) | step p95 (ms) | run p50 (ms) | run p95 (ms) | failure rate |
|---:|---|---:|---:|---:|---:|---:|
| 54 | agenttx_without_read_tracing | 16.076 | 324.54 | 4138.959 | 4164.076 | 0.037037 |
| 54 | agenttx_full | 19.584 | 724.025 | 8198.834 | 8273.296 | 0.037037 |
| 64 | agenttx_without_read_tracing | 15.929 | 308.643 | 4109.162 | 4162.953 | 0.03125 |
| 64 | agenttx_full | 21.373 | 718.572 | 8987.127 | 9674.797 | 0.03125 |
| 96 | agenttx_without_read_tracing | 17.779 | 304.985 | 4768.334 | 4819.968 | 0.020833 |
| 96 | agenttx_full | 24.39 | 684.802 | 9948.028 | 10187.065 | 0.020833 |
