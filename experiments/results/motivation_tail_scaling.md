# AgentTX motivation tail scaling

p50/p95 tail measurements over several deterministic workload lengths.

| length | mode | step p50 (ms) | step p95 (ms) | run p50 (ms) | run p95 (ms) | failure rate |
|---:|---|---:|---:|---:|---:|---:|
| 54 | agenttx_without_read_tracing | 15.605 | 415.632 | 4964.304 | 4978.042 | 0.037037 |
| 54 | agenttx_full | 21.44 | 810.064 | 9444.317 | 9486.995 | 0.037037 |
| 64 | agenttx_without_read_tracing | 16.361 | 398.428 | 5032.781 | 5054.236 | 0.03125 |
| 64 | agenttx_full | 24.772 | 801.382 | 9671.919 | 9681.343 | 0.03125 |
| 96 | agenttx_without_read_tracing | 17.923 | 399.445 | 5713.355 | 5733.796 | 0.020833 |
| 96 | agenttx_full | 28.943 | 791.216 | 10757.586 | 10765.382 | 0.020833 |
