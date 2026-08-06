# AgentTX status — done vs remaining

Last updated: 2026-08-06 (VM `/home/bfq/agenttx`).

## Completed

### Problem framing
- Chose **Problem A: Agent Effect Transactions** (trajectory-level speculation / rollback / selective commit), not per-call `try` wrapping.
- Design notes in `docs/problem.md`, `docs/architecture.md`.

### Runtime (v0)
- **Effect ledger** with automatically captured workspace reads, negative lookups, writes, and deletes; parent/child path dependency edges; cascade / temporal rollback; and a commit frontier.
- **Shared semisolate** pool (`try -N`) with upperdir digests + interactive `try commit` auto-confirm.
- **Surgical rollback** via per-step upperdir snapshots (`layers.py`).
- **Explicit causal rollback** reconstructs selected write/delete paths from a pre-step snapshot, retains independent later steps, and fails closed on retained-effect overlap; temporal rollback remains the backward-compatible default.
- **True path-selective commit** from the ledger frontier using anchored `try -I` filters.
- Historical same-path frontier commit reconstructs the pre-later-step upperdir, materializes the earlier version, and restores later speculative state.
- OverlayFS character-device and `.wh.*` **whiteouts are recorded as delete effects**.
- Metadata-aware fingerprints record repeated `chmod`/`chown`/`touch`, empty directories, renames, and symlink changes.
- Session resume preserves monotonically increasing snapshot ids; `agenttx.json` uses same-directory atomic replace with file and directory `fsync`.
- Rollback snapshots preserve native OverlayFS whiteouts via same-filesystem hard links, safely traverse mode-000 trees, and deduplicate regular-file states through immutable content-addressed blobs.
- Commit restores selected file/directory metadata after materialization and converts upstream false-success error text into a failed commit.
- Commit WAL snapshots selected host paths and the upperdir before materialization; reload restores interrupted partial commits or finalizes a durably persisted frontier.
- Automatic strace-based dependency capture records successful workspace reads and ENOENT/ENOTDIR negative lookups; tracing is default-on and fails closed unless explicitly disabled.
- **Coding harness** + commit **policy** (allow/deny, ignore caches).
- **LLM tool agent** (`scripts/agenttx-agent`) with OpenAI-compatible / DeepSeek config in `~/.agenttx_llm.env` (not in git).

### Experiments / evidence
| Claim | Evidence | Result artifact |
|---|---|---|
| Per-call `try` is expensive | Step 1 overhead | `experiments/results/try_overhead_n*.csv` |
| Shared overlay + ledger works | Step 2 | `shared_overlay_n20.csv`, ledger JSON |
| Surgical cascade rollback | Step 3 demo + suite | `demo_surgical_rollback.py`, `evidence_suite.*` |
| Native frontier-selective commit | Step 5 + real-try integration | `test_runtime_integration.py`, `evidence_suite.*` |
| Automatic read/negative dependencies | Step 7 + real-strace integration | `test_trace.py`, `trace_overhead.{csv,md}` |
| Whiteout/mode-000 rollback durability | Step 8 + real-try integration | `test_filesystem_effects_integration.py` |
| Non-contiguous causal rollback | Step 9 + real-try integration | `test_runtime_integration.py` |
| Interrupted multi-path commit recovery | Step 10 + crash injection | `test_recovery.py`, `commit_wal.py` |
| Historical same-path frontier commit | Step 11 + real-try integration | `test_runtime_integration.py`, `layers.py` |
| Content-addressed snapshot storage | Step 12 benchmark | `bench_snapshot_storage.py`, `snapshot_storage.{csv,md}` |
| Hierarchical causal dependencies | Step 13 + real-try integration | `test_ledger.py`, `test_runtime_integration.py` |
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
1. **Causal rollback as the default API** - explicit rollback_causal now retains independent work and hierarchical path dependencies; switching the default still needs symlink/bind-mount alias coverage and replay evaluation.
2. **Scalable snapshots** - content-addressed blobs reduce repeated file storage (9.0% physical/logical bytes in the Step 12 workload), but directory traversal and historical/WAL copies still grow with speculative state.
3. **Crash-atomic filesystem commit** - a durable WAL now restores interrupted host materialization on reload; an in-flight external observer can still see partial paths, so kernel-level atomicity remains out of scope.
4. **Lower overlay overhead** - shared pool remains substantially slower than bare execution on the evidence trajectory; explore a longer-lived mount or alternative layering design.
5. **Trace portability and completeness** - current capture requires Linux strace, models workspace-local path syscalls, and uses exact-path dependency matching; unsupported syscalls and hierarchical aliasing need a formal coverage story.
6. **Non-filesystem effects** - network/cloud side effects (currently coarse hide_network only).

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
