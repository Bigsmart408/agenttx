# Motivation runtime comparison

Current implementations on the deterministic long Coding Agent workload.
Historical optimization iterations are reported separately in `motivation_optimization_history.md`.

| mode | wall mean (s) | wall p50 (s) | wall p95 (s) | ms/step | failures | host polluted |
|---|---:|---:|---:|---:|---:|:---:|
| bare | 2.904244 | 2.904244 | 2.920465 | 45.379 | 2.0 | True |
| per_call_try | 107.0017 | 107.0017 | 107.577068 | 1671.902 | 34.0 | False |
| shared_try | 107.236251 | 107.236251 | 107.661214 | 1675.566 | 34.0 | False |
| shared_checkpoint | 7.091452 | 7.091452 | 7.150008 | 110.804 | 2.0 | False |
| agenttx_without_read_tracing | 7.038108 | 7.038108 | 7.102501 | 109.97 | 2.0 | False |
| agenttx_full | 10.310303 | 10.310303 | 10.319393 | 161.098 | 2.0 | False |
