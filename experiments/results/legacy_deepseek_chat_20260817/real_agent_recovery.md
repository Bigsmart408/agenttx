# Real-agent causal recovery

Model: `deepseek-chat`; repeats: 3.

| metric | value |
|---|---:|
| wall p50 / p95 (s) | 28.95681 / 30.773955 |
| full recovery success rate | 1.0 |
| faulty-root selection rate | 1.0 |
| correct causal-target rate | 1.0 |
| independent-work retention rate | 1.0 |
| invalid-derived removal rate | 1.0 |
| tests pass rate | 1.0 |
| host leak rate before commit | 0.0 |

Each repeat starts from a fresh workspace. The agent must inspect the ledger, choose the injected faulty root, invoke causal rollback exactly once, preserve an independent note, remove a derived artifact, and pass tests before commit.
