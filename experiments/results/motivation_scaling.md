# AgentTX motivation scaling

Current implementations over the deterministic long coding-agent workload.

| length | mode | wall mean (s) | ms/step | stdev (ms/step) | failures | host polluted |
|---:|---|---:|---:|---:|---:|:---:|
| 54 | bare | 3.228746 | 59.792 | 3.654 | 2.0 | True |
| 54 | agenttx_without_read_tracing | 4.226966 | 78.277 | 2.158 | 2.0 | False |
| 54 | agenttx_full | 8.446721 | 156.421 | 11.871 | 2.0 | False |
| 64 | bare | 3.222834 | 50.357 | 0.576 | 2.0 | True |
| 64 | agenttx_without_read_tracing | 4.117267 | 64.332 | 0.599 | 2.0 | False |
| 64 | agenttx_full | 8.92925 | 139.52 | 2.209 | 2.0 | False |
| 96 | bare | 3.299026 | 34.365 | 0.773 | 2.0 | True |
| 96 | agenttx_without_read_tracing | 4.869616 | 50.725 | 1.209 | 2.0 | False |
| 96 | agenttx_full | 10.273951 | 107.02 | 6.431 | 2.0 | False |
