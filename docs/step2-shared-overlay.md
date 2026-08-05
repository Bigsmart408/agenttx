# Step 2 — Shared overlay + minimal ledger

## What landed

- `Ledger`: path-level R/W/D/N effects, RAW edges, cascade rollback targets, commit frontier
- `SharedSemisolate`: reuse one `try -N DIR` sandbox across tool calls
- `AgentTXRuntime`: tool-boundary interceptor that diffs `try summary` into per-step effects

## Why this is not "wrap try"

Per-call `try` pays full overlay setup each tool call (~160ms). Shared `-N` keeps one upperdir and only remounts/executes, which is the first systems lever toward incremental semisolates.

## Known v0 limits

- Reads are not inferred from `try summary` (pass `extra_reads` when needed)
- Cascade rollback currently resets the whole shared sandbox (no surgical layer drop yet)
- Non-FS effects out of scope
