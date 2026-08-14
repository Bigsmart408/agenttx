# AgentTX evidence suite

| exp | mode | ok | wall_s | notes |
|---|---|---|---:|---|
| cascade_rollback |  | True | 8.17 | rollback step1 cascades; host clean until commit; only a+e land |
| selective_commit_via_rollback |  | True | 6.18 | rollback(2)+commit keeps keep0/1 only |
| frontier_selective_commit |  | True | 7.21 | commit(1) writes keep0/1 while later2 remains speculative |
| host_pollution_vs_bare |  | True | 8.11 | bare writes visible mid-traj; agenttx host clean until commit |
| mistake_recovery |  | True | 10.33 | buggy mul never hits host; rollback; fixed mul committed; pytest pass |
| policy_blocks_dangerous_commit |  | True | 6.17 | deny secrets/*.pem blocks full commit; after rollback, ok.txt commits alone |
| isolation_matrix | bare | True | 1.11 | coding traj pollution/failures/wall |
| isolation_matrix | agenttx | True | 4.71 | coding traj pollution/failures/wall |
