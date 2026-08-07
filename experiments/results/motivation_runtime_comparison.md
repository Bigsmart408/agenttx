# Motivation runtime comparison

Current implementations on the deterministic long Coding Agent workload.
Historical optimization iterations are reported separately in `motivation_optimization_history.md`.

| mode | wall mean (s) | wall p50 (s) | wall p95 (s) | ms/step | failures | host polluted |
|---|---:|---:|---:|---:|---:|:---:|
| bare | 3.179629 | 3.179629 | 3.195639 | 49.682 | 2.0 | True |
| per_call_try | 16.684178 | 16.684178 | 16.822873 | 260.69 | 34.0 | False |
| shared_try | 16.253986 | 16.253986 | 16.262162 | 253.969 | 34.0 | False |
| shared_checkpoint | 4.063891 | 4.063891 | 4.113137 | 63.498 | 2.0 | False |
| agenttx_without_read_tracing | 4.003867 | 4.003867 | 4.012039 | 62.56 | 2.0 | False |
| agenttx_full | 9.501785 | 9.501785 | 9.693457 | 148.465 | 2.0 | False |
