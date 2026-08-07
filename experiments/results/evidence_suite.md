# AgentTX evidence suite

| exp | mode | ok | wall_s | notes |
|---|---|---|---:|---|
| cascade_rollback |  | True | 1.86 | rollback step1 cascades; host clean until commit; only a+e land |
| selective_commit_via_rollback |  | True | 1.71 | rollback(2)+commit keeps keep0/1 only |
| frontier_selective_commit |  | True | 1.34 | commit(1) writes keep0/1 while later2 remains speculative |
| host_pollution_vs_bare |  | True | 2.11 | bare writes visible mid-traj; agenttx host clean until commit |
| mistake_recovery |  | True | 5.48 | buggy mul never hits host; rollback; fixed mul committed; pytest pass |
| policy_blocks_dangerous_commit |  | True | 0.89 | deny secrets/*.pem blocks full commit; after rollback, ok.txt commits alone |
| isolation_matrix | bare | True | 1.09 | coding traj pollution/failures/wall |
| isolation_matrix | agenttx | True | 10.58 | coding traj pollution/failures/wall |
