# AgentTX robustness evaluation

The bundle reports end-to-end tail latency, persistent-worker crash recovery, a long reloadable session, and concurrent isolated agents.

| suite | mode | p50 ms | p95 ms | wall ms | steps/agents | ok | note |
|---|---|---:|---:|---:|---:|:---:|---|
| p50_p95 | agenttx_without_read_tracing | 16.54 | 420.517 | 5197.784 | 64 | True | end-to-end call wall time includes AgentTX ledger persistence |
| p50_p95 | agenttx_full | 24.902 | 812.575 | 9797.473 | 64 | True | end-to-end call wall time includes AgentTX ledger persistence |
| worker_crash | agenttx_without_read_tracing |  |  |  |  | True | worker killed before dispatch; command completed through one-shot try fallback |
| long_session | agenttx_without_read_tracing | 41.686 | 69.925 | 10985.319 | 256 | True | session was closed and reloaded at the midpoint before final commit |
| concurrent_agents | agenttx_without_read_tracing | 1798.566 | 1814.522 | 1893.416 | 4 | True | agents use separate session overlays and commit into separate workspace subdirectories concurrently |
