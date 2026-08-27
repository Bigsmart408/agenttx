# WAL phase fault-injection matrix

Repeats per phase: 5. Crash, reload, then check host/frontier convergence.

| phase | expected | recovery rate | host keep |
|---|---|---:|---:|
| before_prepare | no_wal_recommit | 1.00 | 1.00 |
| prepared | restore_then_recommit | 1.00 | 1.00 |
| applying | restore_then_recommit | 1.00 | 1.00 |
| during_install | restore_then_recommit | 1.00 | 1.00 |
| materialized | finalize_or_converge | 1.00 | 1.00 |
| committed | durable_frontier | 1.00 | 1.00 |

