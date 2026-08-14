# AgentTX motivation scaling

Current implementations over the deterministic long coding-agent workload.

| length | mode | wall mean (s) | ms/step | stdev (ms/step) | failures | host polluted |
|---:|---|---:|---:|---:|---:|:---:|
| 54 | bare | 2.914393 | 53.97 | 0.28 | 2.0 | True |
| 54 | agenttx_without_read_tracing | 8.030396 | 148.711 | 27.673 | 2.0 | False |
| 54 | agenttx_full | 11.086647 | 205.308 | 12.779 | 2.0 | False |
| 64 | bare | 2.881745 | 45.027 | 0.102 | 2.0 | True |
| 64 | agenttx_without_read_tracing | 7.376768 | 115.262 | 3.161 | 2.0 | False |
| 64 | agenttx_full | 10.576071 | 165.251 | 1.778 | 2.0 | False |
| 96 | bare | 2.999903 | 31.249 | 0.342 | 2.0 | True |
| 96 | agenttx_without_read_tracing | 7.832704 | 81.591 | 1.721 | 2.0 | False |
| 96 | agenttx_full | 11.219481 | 116.87 | 1.364 | 2.0 | False |
