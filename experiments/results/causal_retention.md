# Quantitative causal-retention evaluation

Controlled effect-DAG workload. `causal` and temporal baselines receive the same declared read effects; `causal_without_dependencies` is the dependency-capture ablation.

| sweep | x | mode | steps | targets | independent | precision | recall | useful retained | invalid removed | rollback p95 (ms) | correct rate |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| size | 16 | causal | 16 | 7 | 9 | 1.000 | 1.000 | 1.000 | 1.000 | 251.237 | 1.000 |
| size | 16 | causal_without_dependencies | 16 | 7 | 9 | 1.000 | 0.143 | 1.000 | 0.143 | 177.695 | 0.000 |
| size | 16 | temporal | 16 | 7 | 9 | 0.583 | 1.000 | 0.444 | 1.000 | 183.072 | 0.000 |
| size | 16 | whole_session | 16 | 7 | 9 | 0.438 | 1.000 | 0.000 | 1.000 | 146.151 | 0.000 |
| size | 32 | causal | 32 | 13 | 19 | 1.000 | 1.000 | 1.000 | 1.000 | 149.595 | 1.000 |
| size | 32 | causal_without_dependencies | 32 | 13 | 19 | 1.000 | 0.077 | 1.000 | 0.077 | 139.578 | 0.000 |
| size | 32 | temporal | 32 | 13 | 19 | 0.542 | 1.000 | 0.421 | 1.000 | 206.001 | 0.000 |
| size | 32 | whole_session | 32 | 13 | 19 | 0.406 | 1.000 | 0.000 | 1.000 | 170.911 | 0.000 |
| size | 64 | causal | 64 | 25 | 39 | 1.000 | 1.000 | 1.000 | 1.000 | 272.744 | 1.000 |
| size | 64 | causal_without_dependencies | 64 | 25 | 39 | 1.000 | 0.040 | 1.000 | 0.040 | 244.636 | 0.000 |
| size | 64 | temporal | 64 | 25 | 39 | 0.521 | 1.000 | 0.410 | 1.000 | 268.679 | 0.000 |
| size | 64 | whole_session | 64 | 25 | 39 | 0.391 | 1.000 | 0.000 | 1.000 | 229.858 | 0.000 |
| shape | chain | causal | 32 | 13 | 19 | 1.000 | 1.000 | 1.000 | 1.000 | 224.240 | 1.000 |
| shape | chain | causal_without_dependencies | 32 | 13 | 19 | 1.000 | 0.077 | 1.000 | 0.077 | 140.542 | 0.000 |
| shape | chain | temporal | 32 | 13 | 19 | 0.542 | 1.000 | 0.421 | 1.000 | 164.290 | 0.000 |
| shape | chain | whole_session | 32 | 13 | 19 | 0.406 | 1.000 | 0.000 | 1.000 | 165.259 | 0.000 |
| shape | fanout | causal | 32 | 13 | 19 | 1.000 | 1.000 | 1.000 | 1.000 | 156.201 | 1.000 |
| shape | fanout | causal_without_dependencies | 32 | 13 | 19 | 1.000 | 0.077 | 1.000 | 0.077 | 143.575 | 0.000 |
| shape | fanout | temporal | 32 | 13 | 19 | 0.542 | 1.000 | 0.421 | 1.000 | 237.864 | 0.000 |
| shape | fanout | whole_session | 32 | 13 | 19 | 0.406 | 1.000 | 0.000 | 1.000 | 170.510 | 0.000 |
| shape | layered | causal | 32 | 13 | 19 | 1.000 | 1.000 | 1.000 | 1.000 | 150.438 | 1.000 |
| shape | layered | causal_without_dependencies | 32 | 13 | 19 | 1.000 | 0.077 | 1.000 | 0.077 | 187.212 | 0.000 |
| shape | layered | temporal | 32 | 13 | 19 | 0.542 | 1.000 | 0.421 | 1.000 | 151.592 | 0.000 |
| shape | layered | whole_session | 32 | 13 | 19 | 0.406 | 1.000 | 0.000 | 1.000 | 254.704 | 0.000 |
| fault_position | 0.1 | causal | 32 | 15 | 17 | 1.000 | 1.000 | 1.000 | 1.000 | 158.162 | 1.000 |
| fault_position | 0.1 | causal_without_dependencies | 32 | 15 | 17 | 1.000 | 0.067 | 1.000 | 0.067 | 240.999 | 0.000 |
| fault_position | 0.1 | temporal | 32 | 15 | 17 | 0.517 | 1.000 | 0.176 | 1.000 | 252.089 | 0.000 |
| fault_position | 0.1 | whole_session | 32 | 15 | 17 | 0.469 | 1.000 | 0.000 | 1.000 | 523.461 | 0.000 |
| fault_position | 0.5 | causal | 32 | 9 | 23 | 1.000 | 1.000 | 1.000 | 1.000 | 188.327 | 1.000 |
| fault_position | 0.5 | causal_without_dependencies | 32 | 9 | 23 | 1.000 | 0.111 | 1.000 | 0.111 | 188.171 | 0.000 |
| fault_position | 0.5 | temporal | 32 | 9 | 23 | 0.562 | 1.000 | 0.696 | 1.000 | 212.858 | 0.000 |
| fault_position | 0.5 | whole_session | 32 | 9 | 23 | 0.281 | 1.000 | 0.000 | 1.000 | 221.944 | 0.000 |
| fault_position | 0.75 | causal | 32 | 5 | 27 | 1.000 | 1.000 | 1.000 | 1.000 | 184.423 | 1.000 |
| fault_position | 0.75 | causal_without_dependencies | 32 | 5 | 27 | 1.000 | 0.200 | 1.000 | 0.200 | 180.431 | 0.000 |
| fault_position | 0.75 | temporal | 32 | 5 | 27 | 0.625 | 1.000 | 0.889 | 1.000 | 209.938 | 0.000 |
| fault_position | 0.75 | whole_session | 32 | 5 | 27 | 0.156 | 1.000 | 0.000 | 1.000 | 205.169 | 0.000 |
| independence | 0.25 | causal | 32 | 18 | 14 | 1.000 | 1.000 | 1.000 | 1.000 | 193.304 | 1.000 |
| independence | 0.25 | causal_without_dependencies | 32 | 18 | 14 | 1.000 | 0.056 | 1.000 | 0.056 | 137.721 | 0.000 |
| independence | 0.25 | temporal | 32 | 18 | 14 | 0.750 | 1.000 | 0.571 | 1.000 | 154.743 | 0.000 |
| independence | 0.25 | whole_session | 32 | 18 | 14 | 0.562 | 1.000 | 0.000 | 1.000 | 213.037 | 0.000 |
| independence | 0.5 | causal | 32 | 13 | 19 | 1.000 | 1.000 | 1.000 | 1.000 | 145.432 | 1.000 |
| independence | 0.5 | causal_without_dependencies | 32 | 13 | 19 | 1.000 | 0.077 | 1.000 | 0.077 | 133.702 | 0.000 |
| independence | 0.5 | temporal | 32 | 13 | 19 | 0.542 | 1.000 | 0.421 | 1.000 | 157.686 | 0.000 |
| independence | 0.5 | whole_session | 32 | 13 | 19 | 0.406 | 1.000 | 0.000 | 1.000 | 157.329 | 0.000 |
| independence | 0.75 | causal | 32 | 7 | 25 | 1.000 | 1.000 | 1.000 | 1.000 | 142.380 | 1.000 |
| independence | 0.75 | causal_without_dependencies | 32 | 7 | 25 | 1.000 | 0.143 | 1.000 | 0.143 | 137.981 | 0.000 |
| independence | 0.75 | temporal | 32 | 7 | 25 | 0.292 | 1.000 | 0.320 | 1.000 | 214.199 | 0.000 |
| independence | 0.75 | whole_session | 32 | 7 | 25 | 0.219 | 1.000 | 0.000 | 1.000 | 206.756 | 0.000 |
