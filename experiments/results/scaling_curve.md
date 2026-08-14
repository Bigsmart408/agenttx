# Scaling curve (bare / per-call try / shared AgentTX)

| n | mode | wall_s_mean | per_step_ms |
|---:|---|---:|---:|
| 5 | bare | 0.007 | 1.3 |
| 5 | per_call_try | 10.701 | 2140.2 |
| 5 | shared_agenttx | 5.699 | 1139.7 |
| 10 | bare | 0.014 | 1.4 |
| 10 | per_call_try | 20.372 | 2037.2 |
| 10 | shared_agenttx | 4.029 | 402.9 |
| 20 | bare | 0.026 | 1.3 |
| 20 | per_call_try | 37.706 | 1885.3 |
| 20 | shared_agenttx | 4.351 | 217.5 |
| 40 | bare | 0.049 | 1.2 |
| 40 | per_call_try | 68.297 | 1707.4 |
| 40 | shared_agenttx | 4.284 | 107.1 |
