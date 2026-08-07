# Real AgentTX agent robustness

Model: `deepseek-chat`; repeats: 3; task: seeded multi-file refactor.

| metric | value |
|---|---:|
| wall p50 (s) | 12.327848 |
| wall p95 (s) | 14.154968 |
| tool calls p50 / p95 | 13.0 / 15.7 |
| finished rate | 1.0 |
| success rate | 1.0 |
| host leak rate before commit | 0.0 |
| tests pass rate after commit | 1.0 |

Each repeat uses a fresh workspace and session. The API key is read only from the environment and is never serialized.
