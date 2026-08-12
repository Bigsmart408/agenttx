# AgentTX motivation scaling

Current implementations over the deterministic long coding-agent workload.

| length | mode | wall mean (s) | ms/step | stdev (ms/step) | failures | host polluted |
|---:|---|---:|---:|---:|---:|:---:|
| 54 | bare | 4.029126 | 74.613 | 0.56 | 2.0 | True |
| 54 | agenttx_without_read_tracing | 4.96858 | 92.011 | 0.128 | 2.0 | False |
| 54 | agenttx_full | 9.44442 | 174.897 | 0.157 | 2.0 | False |
| 64 | bare | 4.053341 | 63.333 | 0.111 | 2.0 | True |
| 64 | agenttx_without_read_tracing | 5.132591 | 80.197 | 0.787 | 2.0 | False |
| 64 | agenttx_full | 9.704853 | 151.638 | 0.778 | 2.0 | False |
| 96 | bare | 4.174657 | 43.486 | 0.16 | 2.0 | True |
| 96 | agenttx_without_read_tracing | 5.824699 | 60.674 | 0.412 | 2.0 | False |
| 96 | agenttx_full | 10.733541 | 111.808 | 0.627 | 2.0 | False |
