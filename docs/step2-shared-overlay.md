# Step 2 — Shared overlay + minimal ledger

## What landed

- `Ledger`: path-level R/W/D/N effects, RAW edges, cascade rollback targets, commit frontier
- `SharedSemisolate`: reuse one `try -N DIR` sandbox; upperdir digests for per-step effects
- `AgentTX`: session API (`begin/run/rollback/commit/status/close`) for CLI + tests
- CLI: `scripts/agenttx`
- Workaround: run each tool via a temp script file because `try` drops shell quoting on `bash -c`

## Perf

Shared still remounts via `try -N` each call; the win vs per-call is preserving prior upperdir state + cheaper effect capture (no double `try summary`). See `experiments/results/shared_overlay_n20.csv`.

## Known limits

- Reads need `extra_reads` (or future `-t` once quoting/trace is solid)
- Cascade rollback resets the whole shared sandbox (step 3: layered `-L`)
- Non-FS effects out of scope
