# AgentTX evidence suite

| exp | mode | ok | wall_s | notes |
|---|---|---|---:|---|
| cascade_rollback |  | True | 1.24 | rollback step1 cascades; host clean until commit; only a+e land |
| selective_commit_via_rollback |  | True | 0.82 | rollback(2)+commit keeps keep0/1 only |
| frontier_selective_commit |  | True | 0.82 | commit(1) writes keep0/1 while later2 remains speculative |
| host_pollution_vs_bare |  | True | 0.89 | bare writes visible mid-traj; agenttx host clean until commit |
| mistake_recovery |  | True | 3.58 | buggy mul never hits host; rollback; fixed mul committed; pytest pass |
| policy_blocks_dangerous_commit |  | True | 0.72 | deny secrets/*.pem blocks full commit; after rollback, ok.txt commits alone |
| isolation_matrix | bare | True | 1.12 | coding traj pollution/failures/wall |
| isolation_matrix | agenttx | True | 3.55 | coding traj pollution/failures/wall |
