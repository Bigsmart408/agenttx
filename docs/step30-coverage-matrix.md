# Step 30 — Syscall / object-identity coverage matrix

## Question
Which capture and identity topologies are correct on the supported contract, and which fail closed?

## Method
`experiments/scripts/bench_coverage_matrix.py` (default 3 repeats) exercises ordinary path, `openat(AT_FDCWD)`, negative lookup, symlink alias, rename, pre-existing and upper-created hard links, external alias, bind mount (if permitted), and fd-relative dirfd opens.

## Result
Artifact: `experiments/results/coverage_matrix.{csv,json,md}`.
Supported cells pass 3/3. External aliases fail closed 3/3. Bind mount is unavailable without privilege on this host and is recorded as such. Paper Table `tab:coverage`.
