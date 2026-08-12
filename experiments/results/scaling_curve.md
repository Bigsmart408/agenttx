# Scaling curve (bare / per-call try / shared AgentTX)

| n | mode | wall_s_mean | per_step_ms |
|---:|---|---:|---:|
| 5 | bare | 0.008 | 1.6 |
| 5 | per_call_try | 1.149 | 229.8 |
| 5 | shared_agenttx | 0.407 | 81.4 |
| 10 | bare | 0.015 | 1.5 |
| 10 | per_call_try | 2.357 | 235.7 |
| 10 | shared_agenttx | 0.494 | 49.4 |
| 20 | bare | 0.029 | 1.5 |
| 20 | per_call_try | 4.707 | 235.3 |
| 20 | shared_agenttx | 0.674 | 33.7 |
| 40 | bare | 0.059 | 1.5 |
| 40 | per_call_try | 9.376 | 234.4 |
| 40 | shared_agenttx | 1.046 | 26.1 |
