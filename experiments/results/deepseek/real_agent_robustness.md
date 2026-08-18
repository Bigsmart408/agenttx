# Real AgentTX agent robustness

Provider: `deepseek`; model: `deepseek-v4-flash`; repeats: 3; task: seeded multi-file refactor.

| metric | value |
|---|---:|
| wall p50 (s) | 16.564075 |
| wall p95 (s) | 18.465274 |
| tool calls p50 / p95 | 15.0 / 16.8 |
| finished rate | 1.0 |
| success rate | 1.0 |
| host leak rate before commit | 0.0 |
| tests pass rate after commit | 1.0 |

Each repeat uses a fresh workspace and session. The API key is read only from the environment and is never serialized.
