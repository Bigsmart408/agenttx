# Quantitative causal-retention evaluation

Controlled effect-DAG workload. `causal` and temporal baselines receive the same declared read effects; `causal_without_dependencies` is the dependency-capture ablation.

| sweep | x | mode | steps | targets | independent | precision | recall | useful retained | invalid removed | rollback p95 (ms) | correct rate |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| size | 16 | causal | 16 | 7 | 9 | 1.000 | 1.000 | 1.000 | 1.000 | 234.751 | 1.000 |
| size | 16 | causal_without_dependencies | 16 | 7 | 9 | 1.000 | 0.143 | 1.000 | 0.143 | 234.341 | 0.000 |
| size | 16 | temporal | 16 | 7 | 9 | 0.583 | 1.000 | 0.444 | 1.000 | 229.880 | 0.000 |
| size | 16 | whole_session | 16 | 7 | 9 | 0.438 | 1.000 | 0.000 | 1.000 | 220.198 | 0.000 |
| size | 32 | causal | 32 | 13 | 19 | 1.000 | 1.000 | 1.000 | 1.000 | 230.766 | 1.000 |
| size | 32 | causal_without_dependencies | 32 | 13 | 19 | 1.000 | 0.077 | 1.000 | 0.077 | 233.705 | 0.000 |
| size | 32 | temporal | 32 | 13 | 19 | 0.542 | 1.000 | 0.421 | 1.000 | 231.833 | 0.000 |
| size | 32 | whole_session | 32 | 13 | 19 | 0.406 | 1.000 | 0.000 | 1.000 | 230.330 | 0.000 |
| size | 64 | causal | 64 | 25 | 39 | 1.000 | 1.000 | 1.000 | 1.000 | 238.924 | 1.000 |
| size | 64 | causal_without_dependencies | 64 | 25 | 39 | 1.000 | 0.040 | 1.000 | 0.040 | 236.902 | 0.000 |
| size | 64 | temporal | 64 | 25 | 39 | 0.521 | 1.000 | 0.410 | 1.000 | 241.725 | 0.000 |
| size | 64 | whole_session | 64 | 25 | 39 | 0.391 | 1.000 | 0.000 | 1.000 | 242.675 | 0.000 |
| shape | chain | causal | 32 | 13 | 19 | 1.000 | 1.000 | 1.000 | 1.000 | 238.147 | 1.000 |
| shape | chain | causal_without_dependencies | 32 | 13 | 19 | 1.000 | 0.077 | 1.000 | 0.077 | 237.871 | 0.000 |
| shape | chain | temporal | 32 | 13 | 19 | 0.542 | 1.000 | 0.421 | 1.000 | 230.543 | 0.000 |
| shape | chain | whole_session | 32 | 13 | 19 | 0.406 | 1.000 | 0.000 | 1.000 | 230.273 | 0.000 |
| shape | fanout | causal | 32 | 13 | 19 | 1.000 | 1.000 | 1.000 | 1.000 | 288.231 | 1.000 |
| shape | fanout | causal_without_dependencies | 32 | 13 | 19 | 1.000 | 0.077 | 1.000 | 0.077 | 235.504 | 0.000 |
| shape | fanout | temporal | 32 | 13 | 19 | 0.542 | 1.000 | 0.421 | 1.000 | 231.582 | 0.000 |
| shape | fanout | whole_session | 32 | 13 | 19 | 0.406 | 1.000 | 0.000 | 1.000 | 230.010 | 0.000 |
| shape | layered | causal | 32 | 13 | 19 | 1.000 | 1.000 | 1.000 | 1.000 | 239.217 | 1.000 |
| shape | layered | causal_without_dependencies | 32 | 13 | 19 | 1.000 | 0.077 | 1.000 | 0.077 | 245.785 | 0.000 |
| shape | layered | temporal | 32 | 13 | 19 | 0.542 | 1.000 | 0.421 | 1.000 | 230.934 | 0.000 |
| shape | layered | whole_session | 32 | 13 | 19 | 0.406 | 1.000 | 0.000 | 1.000 | 231.177 | 0.000 |
| fault_position | 0.1 | causal | 32 | 15 | 17 | 1.000 | 1.000 | 1.000 | 1.000 | 232.454 | 1.000 |
| fault_position | 0.1 | causal_without_dependencies | 32 | 15 | 17 | 1.000 | 0.067 | 1.000 | 0.067 | 233.354 | 0.000 |
| fault_position | 0.1 | temporal | 32 | 15 | 17 | 0.517 | 1.000 | 0.176 | 1.000 | 231.044 | 0.000 |
| fault_position | 0.1 | whole_session | 32 | 15 | 17 | 0.469 | 1.000 | 0.000 | 1.000 | 225.679 | 0.000 |
| fault_position | 0.5 | causal | 32 | 9 | 23 | 1.000 | 1.000 | 1.000 | 1.000 | 247.942 | 1.000 |
| fault_position | 0.5 | causal_without_dependencies | 32 | 9 | 23 | 1.000 | 0.111 | 1.000 | 0.111 | 232.851 | 0.000 |
| fault_position | 0.5 | temporal | 32 | 9 | 23 | 0.562 | 1.000 | 0.696 | 1.000 | 229.933 | 0.000 |
| fault_position | 0.5 | whole_session | 32 | 9 | 23 | 0.281 | 1.000 | 0.000 | 1.000 | 229.625 | 0.000 |
| fault_position | 0.75 | causal | 32 | 5 | 27 | 1.000 | 1.000 | 1.000 | 1.000 | 236.189 | 1.000 |
| fault_position | 0.75 | causal_without_dependencies | 32 | 5 | 27 | 1.000 | 0.200 | 1.000 | 0.200 | 233.875 | 0.000 |
| fault_position | 0.75 | temporal | 32 | 5 | 27 | 0.625 | 1.000 | 0.889 | 1.000 | 229.162 | 0.000 |
| fault_position | 0.75 | whole_session | 32 | 5 | 27 | 0.156 | 1.000 | 0.000 | 1.000 | 229.458 | 0.000 |
| independence | 0.25 | causal | 32 | 18 | 14 | 1.000 | 1.000 | 1.000 | 1.000 | 237.872 | 1.000 |
| independence | 0.25 | causal_without_dependencies | 32 | 18 | 14 | 1.000 | 0.056 | 1.000 | 0.056 | 233.288 | 0.000 |
| independence | 0.25 | temporal | 32 | 18 | 14 | 0.750 | 1.000 | 0.571 | 1.000 | 231.424 | 0.000 |
| independence | 0.25 | whole_session | 32 | 18 | 14 | 0.562 | 1.000 | 0.000 | 1.000 | 231.601 | 0.000 |
| independence | 0.5 | causal | 32 | 13 | 19 | 1.000 | 1.000 | 1.000 | 1.000 | 248.763 | 1.000 |
| independence | 0.5 | causal_without_dependencies | 32 | 13 | 19 | 1.000 | 0.077 | 1.000 | 0.077 | 228.962 | 0.000 |
| independence | 0.5 | temporal | 32 | 13 | 19 | 0.542 | 1.000 | 0.421 | 1.000 | 225.087 | 0.000 |
| independence | 0.5 | whole_session | 32 | 13 | 19 | 0.406 | 1.000 | 0.000 | 1.000 | 225.028 | 0.000 |
| independence | 0.75 | causal | 32 | 7 | 25 | 1.000 | 1.000 | 1.000 | 1.000 | 235.368 | 1.000 |
| independence | 0.75 | causal_without_dependencies | 32 | 7 | 25 | 1.000 | 0.143 | 1.000 | 0.143 | 233.739 | 0.000 |
| independence | 0.75 | temporal | 32 | 7 | 25 | 0.292 | 1.000 | 0.320 | 1.000 | 233.436 | 0.000 |
| independence | 0.75 | whole_session | 32 | 7 | 25 | 0.219 | 1.000 | 0.000 | 1.000 | 229.886 | 0.000 |
