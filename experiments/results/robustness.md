# AgentTX robustness evaluation

The bundle reports end-to-end tail latency, persistent-worker crash recovery, a long reloadable session, and concurrent isolated agents.

| suite | mode | p50 ms | p95 ms | wall ms | steps/agents | ok | note |
|---|---|---:|---:|---:|---:|:---:|---|
| p50_p95 | agenttx_without_read_tracing | 16.461 | 291.319 | 5264.087 | 64 | True | end-to-end call wall time includes AgentTX ledger persistence |
| p50_p95 | agenttx_full | 19.825 | 612.579 | 8576.436 | 64 | True | end-to-end call wall time includes AgentTX ledger persistence |
| worker_crash | agenttx_without_read_tracing |  |  |  |  | True | worker killed before dispatch; command completed through one-shot try fallback |
| long_session | agenttx_without_read_tracing | 30.497 | 42.414 | 10069.712 | 256 | True | session was closed and reloaded at the midpoint before final commit |
| concurrent_agents | agenttx_without_read_tracing | 8357.243 | 9638.771 | 13641.18 | 4 | True | agents use separate session overlays and commit into separate workspace subdirectories concurrently |
