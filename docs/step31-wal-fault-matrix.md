# Step 31 — WAL phase fault-injection matrix

## Question
Do prepare / apply / install / materialize / commit crashes restore host and frontier consistently on reload?

## Method
`experiments/scripts/bench_wal_fault_matrix.py` (default 10; paper uses 5) injects a crash at each publication phase, reloads the session, and checks host/frontier convergence plus preservation of unrelated files.

## Result
Artifact: `experiments/results/wal_fault_matrix.{csv,json,md}`.
All six phases recover in 5/5 runs. Paper Table `tab:wal`.
