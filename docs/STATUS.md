# AgentTX status — done vs remaining

Last updated: 2026-08-14 (container `/home/pengpeng/agenttx`).

## Completed

### Problem framing
- Chose **Problem A: Agent Effect Transactions** (trajectory-level speculation / rollback / selective commit), not per-call `try` wrapping.
- Design notes in `docs/problem.md`, `docs/architecture.md`; paper-facing systems obstacles in `docs/research-challenges.md`; experiment terminology and evidence chain in `docs/experiments-explained.md`.

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
- Automatic strace-based dependency capture records successful workspace reads and ENOENT/ENOTDIR negative lookups, preserving both symlink request and resolved fd paths; tracing is default-on and fails closed unless explicitly disabled.
- Runtime-enforced commit **policy** (allow/deny, ignore caches) shared by direct API, CLI, coding harness, and LLM agent; custom rules persist across session reload.
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
| Symlink alias causal dependencies | Step 14 + trace/real-try integration | `test_trace.py`, `test_runtime_integration.py` |
| Long coding traj under AgentTX | Step 4 | `long_trajectory.csv` |
| Live DeepSeek speculative edit + policy commit | live demo | `live_agent_ledger.json` |
| AgentTX-LLM vs Aider refactor | compare bench | `refactor_agent_compare.{csv,md}` |
| Cascade / selective / pollution / recovery / policy | evidence suite (all ok) | `evidence_suite.{csv,md,json}` |
| Scaling curve n=5..40 | scaling bench | `scaling_curve.{csv,md}` |
| Baseline comparison matrix | Step 15 fixed causal-retention workload | `comparison_matrix.{csv,json,md}`, `step15-comparison-experiments.md` |
| Longer 64-call Agent workload | Step 16 deterministic multi-file refactor, failing CI, independent edits, derived artifact | `long_workload_matrix.{csv,json,md}`, `step16-long-agent-workloads.md` |
| Long workload scaling + variance | Step 17 lengths 54/64/96, two repeats; refreshed trace and snapshot measurements | `long_workload_scaling.{csv,json,md}`, `step17-evaluation-scaling.md` |
| Optimization iteration history | P1 before/after source snapshots, known-tool tracing bypass, persistent command-script reuse, deferred blob GC, direct script execution, persistent try worker, and incremental upperdir snapshots | `src/agenttx/optimization_history/`, `step18-optimization-iterations.md` |
| Robustness evaluation | Step 19 deterministic p50/p95 tails, worker crash injection/fallback, reloadable long session, concurrent isolated agents, and real LLM-agent repeats | `robustness.{csv,json,md}`, `real_agent_robustness.{csv,json,md}`, `step19-robustness-evaluation.md` |
| Motivation optimization chain | Paper-oriented summary and rerunnable current baseline comparison for all hot-path iterations | `motivation/`, `motivation_optimization_history.{csv,json,md}`, `motivation_runtime_comparison.{csv,json,md}` |
| tiao2 remote comparison refresh | ARM64 VM rerun of comparison, long-workload scaling, causal retention, tracing, snapshot, robustness, and external-baseline probes | `docs/tiao2-comparison-run.md`, refreshed `experiments/results/*` |
| Quantitative causal retention | Step 20 controlled DAG sweep over size, shape, fault position, and independence; causal/temporal/whole-session plus dependency-capture ablation | `causal_retention.{csv,json,md}`, `causal_retention_raw.csv`, `step20-causal-retention-evaluation.md` |
| Real-agent causal recovery | Step 21 DeepSeek ledger inspection, faulty-root selection, causal rollback, independent-work retention, and repaired commit over three fresh sessions | `real_agent_recovery.{csv,json,md}`, `step21-real-agent-causal-recovery.md` |
| Uniform commit-policy enforcement | Step 22 direct runtime, CLI subprocess, and session-reload checks prove denied paths cannot bypass policy | `test_runtime_integration.py`, `test_recovery.py`, `step22-runtime-commit-policy.md` |
| Hard-link alias boundary | Step 23 probe shows lower hard links split on OverlayFS copy-up: alias reads stay stale and selective commit breaks inode identity | `hardlink_alias_probe.{json,md}`, `step23-hardlink-overlay-boundary.md` |
| Avoided LLM replay tokens | Step 24 controlled real-DeepSeek replay sweep over 12/24/48-line artifacts and three recovery granularities | `token_recovery.{csv,json,md}`, `token_recovery_raw.csv`, `step24-token-replay-evaluation.md` |
| Full autonomous recovery tokens | Step 26 complete post-policy LLM diagnosis/tool/validation/repair loop; root preflight and 102-test suite pass; credentialed sweep refreshed on DeepSeek | `bench_token_end_to_end.py`, `plot_token_end_to_end.ipynb`, `step26-end-to-end-token-comparison.md` |
| eBPF dependency tracer (strace replacement) | Step 27 syscall-tracepoint capture with userspace pid-tree filtering (`/proc` snapshot + sched_process_fork), release-marker-held startup, strace-parity parser, `auto`/`strace`/`bpf` backend selection, CLI flag, persistent backend, and orchestration/parity tests | `src/agenttx/bpf_trace.py`, `src/agenttx/bpf_trace.bt` generation, `tests/test_bpf_trace.py`, `bench_bpf_trace.py`, `plot_bpf_trace.ipynb`, `step27-bpf-dependency-tracing.md` |

**Evidence suite highlights (2026-08-07):**
- Cascade rollback: host clean until commit; only intended files land.
- Native path-selective `commit(up_to)` materializes only ledger paths through the frontier; later independent paths remain speculative.
- A partial frontier that overlaps a later write is rejected instead of committing the wrong version.
- Mistake recovery: buggy `mul` never hits host; after rollback + fix, pytest passes.
- Policy blocks `*.pem` / secrets; selective commit of safe files works.
- Isolation matrix: bare pollution rate 1.0 vs AgentTX 0.0 before commit (coding traj).
- Baseline matrix: full AgentTX is the only supported mode that retains independent `c` while removing `a` and derived `b`; disabling read tracing retains `b`.
- Baseline overhead (10 writes, 3 repeats): bare 3.1 ms/step, session try 25.0 ms/step, shared try 255.3 ms/step, AgentTX full 307.8 ms/step. These are VM-specific and not a universal speed claim.
- Evidence suite rerun: all cascade rollback, selective/frontier commit, host-pollution, mistake-recovery, policy, and isolation checks passed.
- Long workload: 64 deterministic tool calls, multi-file refactor plus failing CI and independent docs/config edits; full AgentTX retains the independent files and removes the faulty formatter plus derived report, while no-trace retains the report.
- Scaling/variance: long workload lengths 54/64/96 with two repeats; full AgentTX is 456.5?495.8 ms/step and read tracing contributes 8.0% on a 20-step no-op trace.
- Optimization history: every hot-path iteration now preserves its prior source under `src/agenttx/optimization_history`; iteration 05 reduced full 64-call cost from 393.6 to 151.5 ms/step with a persistent try worker, and iteration 06 reduced cumulative snapshot-stage time from 0.384 to 0.158 s with incremental upperdir replay (two-repeat VM-local measurements; no endpoint speedup claim for iteration 06).
- Robustness evaluation: Step 19 records deterministic per-call/per-run p50 and p95 (no-trace 17.114/334.112 ms; full-trace 22.761/743.230 ms), verifies worker fallback plus restart after injected crash, reloads and commits a 256-step session, and runs 4 concurrent agents with no cross-contamination. The real-agent extension ran three `deepseek-chat` refactors with wall p50/p95 12.328/14.155 s, 100% success, and zero pre-commit host leaks.
- Motivation chain: `motivation/` now provides a one-command current baseline comparison and a paper-ready history summary joining all optimization iterations with deterministic and real-agent robustness results.
- Quantitative causal retention: 144 real-overlay runs across 48 aggregated configurations all kept the host clean. Causal recovery retained 100% of independent work and removed 100% of invalid descendants; at 64 calls temporal rollback retained 41.0%, whole-session discard retained 0%, and causal rollback without dependency capture removed only 4.0% of invalid work. Causal rollback p95 at 64 calls was 272.7 ms.
- Real-agent causal recovery: `deepseek-chat` inspected the AgentTX ledger and selected the injected faulty root in all 3 fresh sessions. Full recovery, correct target selection, independent-note retention, invalid-artifact removal, and post-commit tests were 100%; host leak before commit was 0%, with wall p50/p95 29.0/30.8 s.
- Commit policy invariant: direct runtime and CLI commits now fail closed on denied paths before WAL/materialization. Custom allow/deny globs are stored in `agenttx.json` and remain enforced after reload; blocked commits leave the host clean and frontier unchanged.
- Hard-link boundary: on the Linux 5.15 `try` OverlayFS path, writing one name of a lower hard link leaves the sibling alias stale in the speculative view and splits the inode at selective commit. Ledger-only canonicalization cannot restore POSIX semantics; causal-by-default remains blocked on a different substrate or kernel/FUSE support.
- Token replay: 27/27 controlled `deepseek-chat` recovery samples passed with zero pre-commit host leaks. Causal rollback retained both valid artifacts and required no LLM replay; optimistic temporal recovery replayed one document (692.3/971.3/1,335.7 mean tokens at 12/24/48 lines), while whole branch/session abort replayed two (1,435.7/1,886.7/2,891.0). These are avoided replay tokens, not total end-to-end recovery tokens or external-artifact results.
- End-to-end token comparison: Step 26 charges the complete post-policy autonomous recovery loop and records prompt/completion/total usage, tool/model calls, regeneration, success, leakage, and latency. The x86 conda environment, root-compatible `try` substrate, root preflight, and 79-test suite are ready; the 27-sample numeric sweep remains pending only because no API credential is configured, and no placeholders were added.
- eBPF tracing: Step 27 adds a kernel-tracepoint dependency tracer with the same read/negative semantics as the strace parser (including its classification quirks). The traced tree is filtered in userspace (a `/proc` descendant snapshot taken when the probes report READY, extended by `sched_process_fork` lines), the command is held on a release marker whose content it polls (FIFO pairing does not cross the OverlayFS boundary, and existence polling races overlay negative-dentry caching), optional `vfs_open`/`dpath()` symlink-alias resolution runs on bpftrace >= 0.10, `auto`/`strace`/`bpf` backend selection persists per session, and fail-closed behavior is identical to Step 7. Host quirks are handled by tracepoint discovery plus aliases (Ubuntu 5.4 names `stat`/`lstat` as `newstat`/`newlstat`), legacy field layouts, per-syscall probe blocks, and version-gated `-q`/`BPFTRACE_STRLEN`. The 22-test unit suite passes, and on this root-capable host the full eBPF pipeline (persistent-worker and one-shot) captures real READ/NEGATIVE effects end-to-end. The overhead sweep (`bench_bpf_trace.py`, 20 steps x 3 repeats, read workload) measured mean per-step ms: no tracing 11.5, strace 18.9 (+64.5%), eBPF 1376.7 — the eBPF cost is bpftrace 0.9.4's per-step attach/teardown (a timed SIGINT shutdown of the 40-probe script takes ~1.2 s), so there is no endpoint win over strace for short steps on this host's old bpftrace; capture fidelity was 60/60 for both traced modes (`experiments/results/bpf_trace_overhead.{csv,json,md}`, `motivation/FIG-Bpf-Trace.{pdf,png}`).
- Unprivileged overlay recovery: Ubuntu 5.4 kernels carry a SAUCE `clone_private_mount` check that rejects overlay lowerdirs whose subtree contains MNT_LOCKED child mounts (docker/snap/workspace mounts are all locked in this sandbox), which previously made the `try` backend unusable without root. `scripts/bootstrap.sh` now patches `try` with a recursive-overlay fallback that overlays each mount-free subtree and each child mount root individually; the full 102-test suite, evidence suite, comparison matrix, long-workload, scaling, robustness, causal-retention, token-replay, real-agent, and Aider comparison experiments all pass unprivileged on this host. The eBPF numeric sweep needs root (bpf syscall); it was measured on this host's root-capable session.
- Workspace-start invariant: real-agent benchmark construction exposed stale inherited `$PWD` despite `subprocess cwd=`. One-shot and persistent-worker launches now synchronize `PWD` with the protected workspace, with a regression test for a different caller directory.

**Refactor compare (DeepSeek):**
- AgentTX-LLM: ~19s, host clean before commit, tests pass.
- Aider baseline: ~194s (with `--yes-always --no-git --no-check-update` and a writable cache HOME), host polluted immediately, tests pass (`tests_rc=0`).

### Process / repo hygiene
- VM-only project writes (`docs/CONSTRAINTS.md`).
- Temps under `/tmp/agenttx-*` deleted before commits.
- Local commits only; **not pushed** unless requested.
- Open-source agent notes: `docs/open-source-agents.md`.

## Remaining / open

### High priority (systems)
1. **Causal rollback as the default API** - explicit rollback_causal now retains independent work, hierarchical paths, and symlink aliases. Step 23 shows lower hard-link semantics are broken by OverlayFS copy-up before ledger analysis; switching the default is paused pending a different substrate or kernel/FUSE support, plus bind-mount coverage.
2. **Scalable snapshots** - content-addressed blobs reduce repeated file storage (9.0% physical/logical bytes in the Step 12 workload), but directory traversal and historical/WAL copies still grow with speculative state.
3. **Crash-atomic filesystem commit** - a durable WAL now restores interrupted host materialization on reload; an in-flight external observer can still see partial paths, so kernel-level atomicity remains out of scope.
4. **Lower overlay overhead** - known-tool tracing bypass, a longer-lived try worker, and incremental snapshots are measured and preserved in iteration history; shared pool remains substantially slower than bare execution, so ledger and overlay traversal costs remain open.
5. **Trace portability and completeness** - capture now has two backends: the original Linux strace path and a new eBPF tracepoint tracer (Step 27) that is preferred automatically when attachable; `auto`/`strace`/`bpf` is selectable per session. Unsupported syscalls and non-symlink aliases (non-AT_FDCWD dirfds without `dpath()`) still need a formal coverage story, and the eBPF backend needs a root-capable host to measure its overhead.
6. **Non-filesystem effects** - network/cloud side effects (currently coarse hide_network only).

### Evaluation gaps
8. Harder / longer agent workloads: deterministic 54/64/96-call scaling, a reloadable 256-step session, controlled 16/32/64-call causal DAGs, and real-agent requested recovery are covered by Steps 16-21; real multi-package LLM agents remain.
9. External baselines: tiao2 now runs the previously missing bubblewrap lower bound. BranchFS was cloned but its ARM64 build is blocked by the host Cargo 1.75/fuser API mismatch; Waypoint is blocked by the missing CRIU executable; Sandlock/YoloFS/DeltaBox/Crab/Cordon remain artifact- or environment-blocked. See `docs/tiao2-comparison-run.md` for command-level evidence.
10. Stronger Aider (or other agents) bakeoff with fair timeouts and success criteria.
11. Statistical repeats / variance reporting for real LLM runs (cost-aware) remains open; deterministic runtime tails now have p50/p95 coverage.
    Step 24 adds three repeats and p50/p95 for controlled LLM replay cost. Step 26 defines the paired full autonomous recovery sweep; its credentialed numeric run is blocked only on API credentials on x86, while broader multi-model repeats remain open.

### Product / paper
12. OSDI paper draft (HLS: problem → root cause → AET → design points → eval).
13. Problem B (adversarial mediation) — explicitly deferred.
14. Push `main` to GitHub when approved (currently ahead of `origin/main`).
15. Rotate DeepSeek API key if it was exposed in chat; keep secrets out of git.

## How to re-run evidence

```bash
source ~/.agenttx_llm.env
export PATH="$HOME/miniconda3/envs/agenttx/bin:$PATH"
export PYTHONPATH=/home/pengpeng/agenttx/src:/home/pengpeng/agenttx
cd /home/pengpeng/agenttx

python -m pytest -q
python experiments/scripts/bench_evidence_suite.py
python experiments/scripts/bench_scaling.py
python experiments/scripts/bench_comparison_matrix.py --repeats 3 --n 10
python experiments/scripts/bench_long_trajectory.py --length 64 --repeats 1
python experiments/scripts/bench_long_scaling.py --lengths 54 64 96 --repeats 2
python experiments/scripts/bench_robustness.py --tail-length 64 --tail-repeats 3 --long-steps 256 --long-resume-at 128 --agents 4 --concurrent-steps 16
python experiments/scripts/bench_causal_retention.py --repeats 3
python experiments/scripts/probe_hardlink_alias.py
PYTHONPATH=src:. /home/pengpeng/miniconda3/envs/agenttx/bin/python experiments/scripts/bench_token_recovery.py --document-lines 12 24 48 --repeats 3
PYTHONPATH=src:. python3 experiments/scripts/bench_token_end_to_end.py --document-lines 12 24 48 --repeats 3 --max-turns 20
PYTHONPATH=src:. python3 experiments/scripts/bench_bpf_trace.py --steps 20 --repeats 3 --workload read
PYTHONPATH=src:. python3 motivation/plot_bpf_trace.py
PYTHONPATH=src:. /home/pengpeng/miniconda3/envs/agenttx/bin/python experiments/scripts/bench_real_agent.py --repeats 3 --max-turns 35
PYTHONPATH=src:. /home/pengpeng/miniconda3/envs/agenttx/bin/python experiments/scripts/bench_real_agent_recovery.py --repeats 3 --max-turns 30
PYTHONPATH=src:. python3 motivation/bench_optimization_comparison.py --length 64 --repeats 2
PYTHONPATH=src:. python3 motivation/summarize_optimization_history.py
AIDER_TIMEOUT_S=180 python experiments/scripts/bench_refactor_compare.py
```

## Git tip

Local history may use `commit-tree` when environment injects commit trailers; do not push until explicitly asked.
