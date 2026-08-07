# Scaling curve (bare / per-call try / shared AgentTX)

| n | mode | wall_s_mean | per_step_ms |
|---:|---|---:|---:|
| 5 | bare | 0.014 | 2.8 |
| 5 | per_call_try | 1.489 | 297.8 |
| 5 | shared_agenttx | 1.708 | 341.7 |
| 10 | bare | 0.035 | 3.5 |
| 10 | per_call_try | 2.858 | 285.8 |
| 10 | shared_agenttx | 3.285 | 328.5 |
| 20 | bare | 0.056 | 2.8 |
| 20 | per_call_try | 5.775 | 288.8 |
| 20 | shared_agenttx | 6.614 | 330.7 |
| 40 | bare | 0.103 | 2.6 |
| 40 | per_call_try | 11.954 | 298.9 |
| 40 | shared_agenttx | 13.380 | 334.5 |
