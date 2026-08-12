# Motivation runtime comparison

Current implementations on the deterministic long Coding Agent workload.
Historical optimization iterations are reported separately in `motivation_optimization_history.md`.

| mode | wall mean (s) | wall p50 (s) | wall p95 (s) | ms/step | failures | host polluted |
|---|---:|---:|---:|---:|---:|:---:|
| bare | 4.087606 | 4.087606 | 4.088364 | 63.869 | 2.0 | True |
| per_call_try | 15.467342 | 15.467342 | 15.47912 | 241.677 | 34.0 | False |
| shared_try | 15.170974 | 15.170974 | 15.174757 | 237.046 | 34.0 | False |
| shared_checkpoint | 4.992559 | 4.992559 | 4.9996 | 78.009 | 2.0 | False |
| agenttx_without_read_tracing | 5.113441 | 5.113441 | 5.126098 | 79.898 | 2.0 | False |
| agenttx_full | 9.670678 | 9.670678 | 9.689753 | 151.104 | 2.0 | False |
