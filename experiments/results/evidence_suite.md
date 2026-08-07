# AgentTX evidence suite

| exp | mode | ok | wall_s | notes |
|---|---|---|---:|---|
| cascade_rollback |  | True | 1.94 | rollback step1 cascades; host clean until commit; only a+e land |
| selective_commit_via_rollback |  | True | 1.62 | rollback(2)+commit keeps keep0/1 only |
| frontier_selective_commit |  | True | 1.35 | commit(1) writes keep0/1 while later2 remains speculative |
| host_pollution_vs_bare |  | True | 2.26 | bare writes visible mid-traj; agenttx host clean until commit |
| mistake_recovery |  | True | 4.63 | buggy mul never hits host; rollback; fixed mul committed; pytest pass |
| policy_blocks_dangerous_commit |  | True | 0.97 | deny secrets/*.pem blocks full commit; after rollback, ok.txt commits alone |
| isolation_matrix | bare | True | 1.13 | coding traj pollution/failures/wall |
| isolation_matrix | agenttx | True | 12.78 | coding traj pollution/failures/wall |
