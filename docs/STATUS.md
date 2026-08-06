# AgentTX status — done vs remaining

Last updated: 2026-08-06 (VM `/home/bfq/agenttx`).

## Completed

### Problem framing
- Chose **Problem A: Agent Effect Transactions** (trajectory-level speculation / rollback / selective commit), not per-call `try` wrapping.
- Design notes in `docs/problem.md`, `docs/architecture.md`.

### Runtime (v0)
- **Effect ledger** with read/write/delete representation, dependency edges, cascade / temporal rollback, and a commit frontier. Filesystem writes/deletes are captured automatically; reads still require explicit hints.
- **Shared semisolate** pool (`try -N`) with upperdir digests + interactive `try commit` auto-confirm.
- **Surgical rollback** via per-step upperdir snapshots (`layers.py`).
- **True path-selective commit** from the ledger frontier using anchored `try -I` filters; overlapping later writes fail closed.
- OverlayFS character-device and `.wh.*` **whiteouts are recorded as delete effects**.
- Metadata-aware fingerprints record repeated `chmod`/`chown`/`touch`, empty directories, renames, and symlink changes.
- Session resume preserves monotonically increasing snapshot ids; `agenttx.json` uses same-directory atomic replace with file and directory `fsync`.
- **Coding harness** + commit **policy** (allow/deny, ignore caches).
- **LLM tool agent** (`scripts/agenttx-agent`) with OpenAI-compatible / DeepSeek config in `~/.agenttx_llm.env` (not in git).

### Experiments / evidence
| Claim | Evidence | Result artifact |
|---|---|---|
| Per-call `try` is expensive | Step 1 overhead | `experiments/results/try_overhead_n*.csv` |
| Shared overlay + ledger works | Step 2 | `shared_overlay_n20.csv`, ledger JSON |
| Surgical cascade rollback | Step 3 demo + suite | `demo_surgical_rollback.py`, `evidence_suite.*` |
| Native frontier-selective commit | Step 5 + real-try integration | `test_runtime_integration.py`, `evidence_suite.*` |
| Long coding traj under AgentTX | Step 4 | `long_trajectory.csv` |
| Live DeepSeek speculative edit + policy commit | live demo | `live_agent_ledger.json` |
| AgentTX-LLM vs Aider refactor | compare bench | `refactor_agent_compare.{csv,md}` |
| Cascade / selective / pollution / recovery / policy | evidence suite (all ok) | `evidence_suite.{csv,md,json}` |
| Scaling curve n=5..40 | scaling bench | `scaling_curve.{csv,md}` |

**Evidence suite highlights (2026-08-06):**
- Cascade rollback: host clean until commit; only intended files land.
- Native path-selective `commit(up_to)` materializes only ledger paths through the frontier; later independent paths remain speculative.
- A partial frontier that overlaps a later write is rejected instead of committing the wrong version.
- Mistake recovery: buggy `mul` never hits host; after rollback + fix, pytest passes.
- Policy blocks `*.pem` / secrets; selective commit of safe files works.
- Isolation matrix: bare pollution rate 1.0 vs AgentTX 0.0 before commit (coding traj).

**Refactor compare (DeepSeek):**
- AgentTX-LLM: ~14s, host clean before commit, tests pass.
- Aider baseline: ~41s with `--yes-always --no-git`, host polluted immediately, tests failed (`tests_rc=2`).

### Process / repo hygiene
- VM-only project writes (`docs/CONSTRAINTS.md`).
- Temps under `/tmp/agenttx-*` deleted before commits.
- Local commits only; **not pushed** unless requested.
- Open-source agent notes: `docs/open-source-agents.md`.

## Remaining / open

### High priority (systems)
1. **Automatic read and negative-lookup tracing** - filesystem writes/deletes are observed, but read dependencies currently require extra_reads; causal rollback is therefore incomplete by default.
2. **Causal (not only temporal) rollback as default API** - causal_dependents exists; runtime still uses temporal cascade_rollback_targets.
3. **Same-path historical commit reconstruction** - path-selective partial commit currently fails closed if a later step rewrote a selected path; snapshots could materialize the earlier version safely.
4. **Crash-atomic filesystem commit** - metadata persistence is atomic, but a crash during multi-path host materialization can still expose a partially committed frontier; add a WAL/recovery protocol.
5. **Scalable snapshots** - rollback snapshots still copy the accumulated upperdir, so storage and latency grow with speculative state.
6. **Lower overlay overhead** - shared pool remains substantially slower than bare execution on the evidence trajectory; explore a longer-lived mount or alternative layering design.
7. **Non-filesystem effects** - network/cloud side effects (currently coarse hide_network only).

### Evaluation gaps
8. Harder / longer agent workloads (multi-package refactors, failing CI loops).
9. More baselines: full container / gVisor / OS-level sandbox comparison.
10. Stronger Aider (or other agents) bakeoff with fair timeouts and success criteria.
11. Statistical repeats / variance reporting for LLM runs (cost-aware).

### Product / paper
12. OSDI paper draft (HLS: problem → root cause → AET → design points → eval).
13. Problem B (adversarial mediation) — explicitly deferred.
14. Push `main` to GitHub when approved (currently ahead of `origin/main`).
15. Rotate DeepSeek API key if it was exposed in chat; keep secrets out of git.

## How to re-run evidence

```bash
source ~/.agenttx_llm.env
export PATH="$HOME/miniconda3/envs/agenttx/bin:$PATH"
export PYTHONPATH=/home/bfq/agenttx/src:/home/bfq/agenttx
cd /home/bfq/agenttx

python -m pytest -q
python experiments/scripts/bench_evidence_suite.py
python experiments/scripts/bench_scaling.py
AIDER_TIMEOUT_S=180 python experiments/scripts/bench_refactor_compare.py
```

## Git tip

Local history may use `commit-tree` when environment injects commit trailers; do not push until explicitly asked.
