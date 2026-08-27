# Quantitative causal-retention evaluation

Controlled effect-DAG workload. `causal` and temporal baselines receive the same declared read effects; `causal_without_dependencies` is the dependency-capture ablation.

| sweep | x | mode | steps | targets | independent | precision | recall | useful retained | invalid removed | rollback p95 (ms) | correct rate |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| size | 16 | causal | 16 | 7 | 9 | 1.000 | 1.000 | 1.000 | 1.000 | 628.826 | 1.000 |
| size | 16 | causal_without_dependencies | 16 | 7 | 9 | 1.000 | 0.143 | 1.000 | 0.143 | 485.120 | 0.000 |
| size | 16 | temporal | 16 | 7 | 9 | 0.583 | 1.000 | 0.444 | 1.000 | 387.655 | 0.000 |
| size | 16 | whole_session | 16 | 7 | 9 | 0.438 | 1.000 | 0.000 | 1.000 | 440.192 | 0.000 |
| size | 32 | causal | 32 | 13 | 19 | 1.000 | 1.000 | 1.000 | 1.000 | 439.918 | 1.000 |
| size | 32 | causal_without_dependencies | 32 | 13 | 19 | 1.000 | 0.077 | 1.000 | 0.077 | 345.132 | 0.000 |
| size | 32 | temporal | 32 | 13 | 19 | 0.542 | 1.000 | 0.421 | 1.000 | 344.872 | 0.000 |
| size | 32 | whole_session | 32 | 13 | 19 | 0.406 | 1.000 | 0.000 | 1.000 | 393.799 | 0.000 |
| size | 64 | causal | 64 | 25 | 39 | 1.000 | 1.000 | 1.000 | 1.000 | 500.988 | 1.000 |
| size | 64 | causal_without_dependencies | 64 | 25 | 39 | 1.000 | 0.040 | 1.000 | 0.040 | 437.893 | 0.000 |
| size | 64 | temporal | 64 | 25 | 39 | 0.521 | 1.000 | 0.410 | 1.000 | 363.670 | 0.000 |
| size | 64 | whole_session | 64 | 25 | 39 | 0.391 | 1.000 | 0.000 | 1.000 | 419.510 | 0.000 |
| shape | chain | causal | 32 | 13 | 19 | 1.000 | 1.000 | 1.000 | 1.000 | 347.819 | 1.000 |
| shape | chain | causal_without_dependencies | 32 | 13 | 19 | 1.000 | 0.077 | 1.000 | 0.077 | 342.227 | 0.000 |
| shape | chain | temporal | 32 | 13 | 19 | 0.542 | 1.000 | 0.421 | 1.000 | 442.493 | 0.000 |
| shape | chain | whole_session | 32 | 13 | 19 | 0.406 | 1.000 | 0.000 | 1.000 | 737.064 | 0.000 |
| shape | fanout | causal | 32 | 13 | 19 | 1.000 | 1.000 | 1.000 | 1.000 | 294.237 | 1.000 |
| shape | fanout | causal_without_dependencies | 32 | 13 | 19 | 1.000 | 0.077 | 1.000 | 0.077 | 527.494 | 0.000 |
| shape | fanout | temporal | 32 | 13 | 19 | 0.542 | 1.000 | 0.421 | 1.000 | 397.723 | 0.000 |
| shape | fanout | whole_session | 32 | 13 | 19 | 0.406 | 1.000 | 0.000 | 1.000 | 398.038 | 0.000 |
| shape | layered | causal | 32 | 13 | 19 | 1.000 | 1.000 | 1.000 | 1.000 | 591.774 | 1.000 |
| shape | layered | causal_without_dependencies | 32 | 13 | 19 | 1.000 | 0.077 | 1.000 | 0.077 | 336.932 | 0.000 |
| shape | layered | temporal | 32 | 13 | 19 | 0.542 | 1.000 | 0.421 | 1.000 | 348.474 | 0.000 |
| shape | layered | whole_session | 32 | 13 | 19 | 0.406 | 1.000 | 0.000 | 1.000 | 397.180 | 0.000 |
| fault_position | 0.1 | causal | 32 | 15 | 17 | 1.000 | 1.000 | 1.000 | 1.000 | 295.943 | 1.000 |
| fault_position | 0.1 | causal_without_dependencies | 32 | 15 | 17 | 1.000 | 0.067 | 1.000 | 0.067 | 289.065 | 0.000 |
| fault_position | 0.1 | temporal | 32 | 15 | 17 | 0.517 | 1.000 | 0.176 | 1.000 | 301.710 | 0.000 |
| fault_position | 0.1 | whole_session | 32 | 15 | 17 | 0.469 | 1.000 | 0.000 | 1.000 | 300.756 | 0.000 |
| fault_position | 0.5 | causal | 32 | 9 | 23 | 1.000 | 1.000 | 1.000 | 1.000 | 340.077 | 1.000 |
| fault_position | 0.5 | causal_without_dependencies | 32 | 9 | 23 | 1.000 | 0.111 | 1.000 | 0.111 | 289.382 | 0.000 |
| fault_position | 0.5 | temporal | 32 | 9 | 23 | 0.562 | 1.000 | 0.696 | 1.000 | 343.937 | 0.000 |
| fault_position | 0.5 | whole_session | 32 | 9 | 23 | 0.281 | 1.000 | 0.000 | 1.000 | 296.732 | 0.000 |
| fault_position | 0.75 | causal | 32 | 5 | 27 | 1.000 | 1.000 | 1.000 | 1.000 | 288.743 | 1.000 |
| fault_position | 0.75 | causal_without_dependencies | 32 | 5 | 27 | 1.000 | 0.200 | 1.000 | 0.200 | 300.076 | 0.000 |
| fault_position | 0.75 | temporal | 32 | 5 | 27 | 0.625 | 1.000 | 0.889 | 1.000 | 301.543 | 0.000 |
| fault_position | 0.75 | whole_session | 32 | 5 | 27 | 0.156 | 1.000 | 0.000 | 1.000 | 296.321 | 0.000 |
| independence | 0.25 | causal | 32 | 18 | 14 | 1.000 | 1.000 | 1.000 | 1.000 | 347.986 | 1.000 |
| independence | 0.25 | causal_without_dependencies | 32 | 18 | 14 | 1.000 | 0.056 | 1.000 | 0.056 | 289.545 | 0.000 |
| independence | 0.25 | temporal | 32 | 18 | 14 | 0.750 | 1.000 | 0.571 | 1.000 | 297.751 | 0.000 |
| independence | 0.25 | whole_session | 32 | 18 | 14 | 0.562 | 1.000 | 0.000 | 1.000 | 295.462 | 0.000 |
| independence | 0.5 | causal | 32 | 13 | 19 | 1.000 | 1.000 | 1.000 | 1.000 | 294.275 | 1.000 |
| independence | 0.5 | causal_without_dependencies | 32 | 13 | 19 | 1.000 | 0.077 | 1.000 | 0.077 | 289.501 | 0.000 |
| independence | 0.5 | temporal | 32 | 13 | 19 | 0.542 | 1.000 | 0.421 | 1.000 | 296.370 | 0.000 |
| independence | 0.5 | whole_session | 32 | 13 | 19 | 0.406 | 1.000 | 0.000 | 1.000 | 297.603 | 0.000 |
| independence | 0.75 | causal | 32 | 7 | 25 | 1.000 | 1.000 | 1.000 | 1.000 | 390.926 | 1.000 |
| independence | 0.75 | causal_without_dependencies | 32 | 7 | 25 | 1.000 | 0.143 | 1.000 | 0.143 | 335.752 | 0.000 |
| independence | 0.75 | temporal | 32 | 7 | 25 | 0.292 | 1.000 | 0.320 | 1.000 | 294.923 | 0.000 |
| independence | 0.75 | whole_session | 32 | 7 | 25 | 0.219 | 1.000 | 0.000 | 1.000 | 298.286 | 0.000 |
