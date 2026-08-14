# Real AgentTX agent robustness

Provider: `deepseek`; model: `deepseek-v4-flash`; repeats: 1; task: seeded multi-file refactor.

| metric | value |
|---|---:|
| wall p50 (s) | 20.106559 |
| wall p95 (s) | 20.106559 |
| tool calls p50 / p95 | 12.0 / 12.0 |
| finished rate | 1.0 |
| success rate | 1.0 |
| host leak rate before commit | 0.0 |
| tests pass rate after commit | 1.0 |

Each repeat uses a fresh workspace and session. The API key is read only from the environment and is never serialized.
