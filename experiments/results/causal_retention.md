# Quantitative causal-retention evaluation

Controlled effect-DAG workload. `causal` and temporal baselines receive the same declared read effects; `causal_without_dependencies` is the dependency-capture ablation.

| sweep | x | mode | steps | targets | independent | precision | recall | useful retained | invalid removed | rollback p95 (ms) | correct rate |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| size | 16 | causal | 16 | 7 | 9 | 1.000 | 1.000 | 1.000 | 1.000 | 130.464 | 1.000 |
| size | 16 | causal_without_dependencies | 16 | 7 | 9 | 1.000 | 0.143 | 1.000 | 0.143 | 125.204 | 0.000 |
| size | 16 | temporal | 16 | 7 | 9 | 0.583 | 1.000 | 0.444 | 1.000 | 131.815 | 0.000 |
| size | 16 | whole_session | 16 | 7 | 9 | 0.438 | 1.000 | 0.000 | 1.000 | 130.228 | 0.000 |
| size | 32 | causal | 32 | 13 | 19 | 1.000 | 1.000 | 1.000 | 1.000 | 138.214 | 1.000 |
| size | 32 | causal_without_dependencies | 32 | 13 | 19 | 1.000 | 0.077 | 1.000 | 0.077 | 76.802 | 0.000 |
| size | 32 | temporal | 32 | 13 | 19 | 0.542 | 1.000 | 0.421 | 1.000 | 146.607 | 0.000 |
| size | 32 | whole_session | 32 | 13 | 19 | 0.406 | 1.000 | 0.000 | 1.000 | 93.687 | 0.000 |
| size | 64 | causal | 64 | 25 | 39 | 1.000 | 1.000 | 1.000 | 1.000 | 112.543 | 1.000 |
| size | 64 | causal_without_dependencies | 64 | 25 | 39 | 1.000 | 0.040 | 1.000 | 0.040 | 83.058 | 0.000 |
| size | 64 | temporal | 64 | 25 | 39 | 0.521 | 1.000 | 0.410 | 1.000 | 131.842 | 0.000 |
| size | 64 | whole_session | 64 | 25 | 39 | 0.391 | 1.000 | 0.000 | 1.000 | 134.591 | 0.000 |
| shape | chain | causal | 32 | 13 | 19 | 1.000 | 1.000 | 1.000 | 1.000 | 88.548 | 1.000 |
| shape | chain | causal_without_dependencies | 32 | 13 | 19 | 1.000 | 0.077 | 1.000 | 0.077 | 79.057 | 0.000 |
| shape | chain | temporal | 32 | 13 | 19 | 0.542 | 1.000 | 0.421 | 1.000 | 92.854 | 0.000 |
| shape | chain | whole_session | 32 | 13 | 19 | 0.406 | 1.000 | 0.000 | 1.000 | 95.531 | 0.000 |
| shape | fanout | causal | 32 | 13 | 19 | 1.000 | 1.000 | 1.000 | 1.000 | 87.046 | 1.000 |
| shape | fanout | causal_without_dependencies | 32 | 13 | 19 | 1.000 | 0.077 | 1.000 | 0.077 | 77.184 | 0.000 |
| shape | fanout | temporal | 32 | 13 | 19 | 0.542 | 1.000 | 0.421 | 1.000 | 93.459 | 0.000 |
| shape | fanout | whole_session | 32 | 13 | 19 | 0.406 | 1.000 | 0.000 | 1.000 | 151.014 | 0.000 |
| shape | layered | causal | 32 | 13 | 19 | 1.000 | 1.000 | 1.000 | 1.000 | 87.567 | 1.000 |
| shape | layered | causal_without_dependencies | 32 | 13 | 19 | 1.000 | 0.077 | 1.000 | 0.077 | 77.198 | 0.000 |
| shape | layered | temporal | 32 | 13 | 19 | 0.542 | 1.000 | 0.421 | 1.000 | 94.867 | 0.000 |
| shape | layered | whole_session | 32 | 13 | 19 | 0.406 | 1.000 | 0.000 | 1.000 | 96.677 | 0.000 |
| fault_position | 0.1 | causal | 32 | 15 | 17 | 1.000 | 1.000 | 1.000 | 1.000 | 91.927 | 1.000 |
| fault_position | 0.1 | causal_without_dependencies | 32 | 15 | 17 | 1.000 | 0.067 | 1.000 | 0.067 | 123.344 | 0.000 |
| fault_position | 0.1 | temporal | 32 | 15 | 17 | 0.517 | 1.000 | 0.176 | 1.000 | 96.625 | 0.000 |
| fault_position | 0.1 | whole_session | 32 | 15 | 17 | 0.469 | 1.000 | 0.000 | 1.000 | 94.257 | 0.000 |
| fault_position | 0.5 | causal | 32 | 9 | 23 | 1.000 | 1.000 | 1.000 | 1.000 | 84.781 | 1.000 |
| fault_position | 0.5 | causal_without_dependencies | 32 | 9 | 23 | 1.000 | 0.111 | 1.000 | 0.111 | 76.944 | 0.000 |
| fault_position | 0.5 | temporal | 32 | 9 | 23 | 0.562 | 1.000 | 0.696 | 1.000 | 91.469 | 0.000 |
| fault_position | 0.5 | whole_session | 32 | 9 | 23 | 0.281 | 1.000 | 0.000 | 1.000 | 94.471 | 0.000 |
| fault_position | 0.75 | causal | 32 | 5 | 27 | 1.000 | 1.000 | 1.000 | 1.000 | 81.364 | 1.000 |
| fault_position | 0.75 | causal_without_dependencies | 32 | 5 | 27 | 1.000 | 0.200 | 1.000 | 0.200 | 77.129 | 0.000 |
| fault_position | 0.75 | temporal | 32 | 5 | 27 | 0.625 | 1.000 | 0.889 | 1.000 | 88.470 | 0.000 |
| fault_position | 0.75 | whole_session | 32 | 5 | 27 | 0.156 | 1.000 | 0.000 | 1.000 | 93.781 | 0.000 |
| independence | 0.25 | causal | 32 | 18 | 14 | 1.000 | 1.000 | 1.000 | 1.000 | 91.945 | 1.000 |
| independence | 0.25 | causal_without_dependencies | 32 | 18 | 14 | 1.000 | 0.056 | 1.000 | 0.056 | 77.530 | 0.000 |
| independence | 0.25 | temporal | 32 | 18 | 14 | 0.750 | 1.000 | 0.571 | 1.000 | 94.020 | 0.000 |
| independence | 0.25 | whole_session | 32 | 18 | 14 | 0.562 | 1.000 | 0.000 | 1.000 | 94.489 | 0.000 |
| independence | 0.5 | causal | 32 | 13 | 19 | 1.000 | 1.000 | 1.000 | 1.000 | 87.282 | 1.000 |
| independence | 0.5 | causal_without_dependencies | 32 | 13 | 19 | 1.000 | 0.077 | 1.000 | 0.077 | 76.949 | 0.000 |
| independence | 0.5 | temporal | 32 | 13 | 19 | 0.542 | 1.000 | 0.421 | 1.000 | 94.363 | 0.000 |
| independence | 0.5 | whole_session | 32 | 13 | 19 | 0.406 | 1.000 | 0.000 | 1.000 | 94.710 | 0.000 |
| independence | 0.75 | causal | 32 | 7 | 25 | 1.000 | 1.000 | 1.000 | 1.000 | 81.749 | 1.000 |
| independence | 0.75 | causal_without_dependencies | 32 | 7 | 25 | 1.000 | 0.143 | 1.000 | 0.143 | 77.056 | 0.000 |
| independence | 0.75 | temporal | 32 | 7 | 25 | 0.292 | 1.000 | 0.320 | 1.000 | 94.339 | 0.000 |
| independence | 0.75 | whole_session | 32 | 7 | 25 | 0.219 | 1.000 | 0.000 | 1.000 | 94.662 | 0.000 |
