# AgentTX evidence suite

| exp | mode | ok | wall_s | notes |
|---|---|---|---:|---|
| cascade_rollback |  | True | 1.11 | rollback step1 cascades; host clean until commit; only a+e land |
| selective_commit_via_rollback |  | True | 0.89 | rollback(2)+commit keeps keep0/1 only |
| naive_frontier_commit_gap |  | True | 0.74 | GAP: commit(1) still writes drop2 (full overlay commit) |
| host_pollution_vs_bare |  | True | 1.30 | bare writes visible mid-traj; agenttx host clean until commit |
| mistake_recovery |  | True | 2.33 | buggy mul never hits host; rollback; fixed mul committed; pytest pass |
| policy_blocks_dangerous_commit |  | True | 0.55 | deny secrets/*.pem blocks full commit; after rollback, ok.txt commits alone |
| isolation_matrix | bare | True | 0.80 | coding traj pollution/failures/wall |
| isolation_matrix | agenttx | True | 5.92 | coding traj pollution/failures/wall |
