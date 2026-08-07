# AgentTX robustness evaluation

The bundle reports end-to-end tail latency, persistent-worker crash recovery, a long reloadable session, and concurrent isolated agents.

| suite | mode | p50 ms | p95 ms | wall ms | steps/agents | ok | note |
|---|---|---:|---:|---:|---:|:---:|---|
| p50_p95 | agenttx_without_read_tracing | 17.114 | 334.112 | 4216.381 | 64 | True | end-to-end call wall time includes AgentTX ledger persistence |
| p50_p95 | agenttx_full | 22.761 | 743.23 | 8942.204 | 64 | True | end-to-end call wall time includes AgentTX ledger persistence |
| worker_crash | agenttx_without_read_tracing |  |  |  |  | True | worker killed before dispatch; command completed through one-shot try fallback |
| long_session | agenttx_without_read_tracing | 36.286 | 72.312 | 10604.103 | 256 | True | session was closed and reloaded at the midpoint before final commit |
| concurrent_agents | agenttx_without_read_tracing | 2935.097 | 3043.653 | 3105.418 | 4 | True | agents use separate session overlays and commit into separate workspace subdirectories concurrently |
