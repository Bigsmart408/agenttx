# Scaling curve (bare / per-call try / shared AgentTX)

| n | mode | wall_s_mean | per_step_ms |
|---:|---|---:|---:|
| 5 | bare | 0.007 | 1.4 |
| 5 | per_call_try | 0.874 | 174.8 |
| 5 | shared_agenttx | 0.887 | 177.4 |
| 10 | bare | 0.011 | 1.1 |
| 10 | per_call_try | 1.751 | 175.1 |
| 10 | shared_agenttx | 1.773 | 177.3 |
| 20 | bare | 0.021 | 1.0 |
| 20 | per_call_try | 3.469 | 173.4 |
| 20 | shared_agenttx | 3.632 | 181.6 |
| 40 | bare | 0.043 | 1.1 |
| 40 | per_call_try | 7.435 | 185.9 |
| 40 | shared_agenttx | 7.938 | 198.5 |
