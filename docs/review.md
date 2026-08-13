# AgentTX code review — current gaps

**Date:** 2026-08-09
**Repo:** `/home/pengpeng/agenttx`
**Reviewed through:** `e78416a` (`Add agent-driven causal recovery`)
**Scope:** Defect-first / gap review of the *current* implementation.
**Non-goal:** This document does **not** prescribe immediate patches; it inventories gaps for planning.

Companion graphic: Cursor canvas `agenttx-code-graphic` (architecture + gap map).

---

## 1. Executive summary

AgentTX has grown from a Step 1–4 scaffold into a working trajectory runtime: shared `try` session, effect ledger, selective/historical commit, causal rollback, strace deps, content-addressed / incremental snapshots, persistent try worker, WAL recovery, and a sizable evaluation matrix (Steps 5–21).

The remaining gaps are no longer “missing the whole system.” They cluster into:

1. **API / product defaults** that under-sell the causal story
2. **Completeness holes** in effect capture and aliasing
3. **Performance** still dominated by overlay + tracing
4. **Atomicity / threat-model limits** of userspace commit
5. **Agent integration & eval** still thinner than the runtime core
6. **Paper / external bakeoff** unfinished

---

## 2. What the current code *does* cover (context)

| Layer | Modules | Working capability |
|---|---|---|
| Interceptor | `runtime.py`, `harness.py`, `agents/llm_agent.py` | Tool-boundary run into shared overlay |
| Ledger | `ledger.py` | Hierarchical path overlap + R/W/D/N parents; temporal + causal dependents |
| Capture | `semisolate.py`, `trace.py`, `effects.py` | Upperdir digests, whiteouts-as-deletes, optional strace reads/negatives |
| Snapshots | `layers.py` | Content-addressed blobs, incremental hard-link snapshots, path restore |
| Commit | `semisolate.commit` / `commit_from_snapshot`, `commit_wal.py` | Path-selective `try -I`, historical same-path frontier, WAL reload repair |
| Rollback | `rollback` / `rollback_causal` | Temporal cascade (default) vs causal retain-independent (explicit) |
| Perf path | `try_worker.py`, trusted-tool trace bypass | Persistent worker + known-tool shortcuts (iteration history under `optimization_history/`) |
| Eval | `experiments/`, `tests/` (~53 tests) | Evidence suite, scaling, robustness, real DeepSeek agent repeats, related-work survey |

---

## 3. Architecture gaps (priority ordered)

### P0 — Correctness / safety surface

#### G1. Runtime commit-policy invariant implemented
- **Implemented:** `AgentTX.commit_frontier` now checks the session `CommitPolicy` before path planning, WAL creation, or host materialization, so direct API, CLI, harness, and LLM entry points share one fail-closed invariant.
- **Reload safety:** custom allow/deny globs are persisted in `agenttx.json` and reconstructed by `AgentTX.load`; old sessions receive the default policy.
- **Evidence:** Step 22 covers direct runtime denial, a separate CLI commit process, and stricter-policy reload. The broader expressiveness gap remains G8 rather than an entry-point bypass.

#### G2. Default rollback is still temporal, not causal
- **Where:** `AgentTX.rollback` → `rollback_from` → `Ledger.cascade_rollback_targets` (all later steps).
- **Causal path:** `rollback_causal` / CLI `--causal` only.
- **Risk:** Paper/product claim (“non-contiguous causal recovery”) is opt-in; default API discards independent later work — closer to checkpoint abort than AET.
- **Blocker for flipping default:** Step 23 empirically shows that lower hard links split during OverlayFS copy-up: an alias read sees stale data and selective commit breaks inode identity. This cannot be repaired by ledger edges alone; bind mounts also remain untested.

#### G3. Alias model is symlink-ancestor only
- **Where:** `AgentTX._resolve_alias_ancestors` walks lexical symlink ancestors in merged view; ledger uses `_paths_overlap` prefixes.
- **Missing:** hard-link aliases, bind mounts, `mount --bind`, cross-device path equivalence, non-ancestor symlink *targets* used as alternate names without ancestor walk coverage in all cases.
- **Measured boundary:** `hardlink_alias_probe.{json,md}` demonstrates that the current substrate diverges from POSIX hard-link visibility before causal analysis. A FUSE/kernel-aware or different snapshot substrate is required for faithful support; bind-mounted tool caches remain a separate risk.

#### G4. Trace completeness is partial and Linux-only
- **Where:** `trace.py` (`open`/`openat`/`openat2` focused); `SharedSemisolate` requires `strace` unless `trace_reads=False`.
- **Gaps:**
  - No portable Windows/macOS story
  - Formal unsupported-syscall list not enforced as fail-closed coverage matrix
  - Rename/link/unlink/symlink/stat families need an explicit “modeled vs ignored” contract (ignored syscalls ⇒ under-approximated parents ⇒ unsafe retain on causal rollback)
  - Trusted-tool bypass (`known write/read` shortcuts) is a correctness cliff if tool implementations drift from declared effects

#### G5. Multi-path commit is crash-*recoverable*, not externally atomic
- **Where:** `CommitWAL` + `commit_frontier`
- **Fact:** Reload can restore / finalize after interruption; an in-flight observer can still see a subset of paths applied.
- **Gap vs claim:** “transactional commit” for multi-path host materialization is not kernel-atomic; fine for research if scoped, dangerous if marketed as ACID host updates.

---

### P1 — Systems completeness

#### G6. Non-filesystem effects are almost absent
- **Where:** `hide_network: bool` toggles try network hiding only.
- **Missing:** network/cloud/API side-effect ledger, DNS, credentials helpers, package registries, container pulls.
- **Impact:** Coding agents that `curl | bash` or hit SaaS APIs are outside the transaction story.

#### G7. LLM recovery control is implemented; commit planning remains thin
- **Implemented:** `agents/llm_agent.py` now exposes ledger/frontier inspection and `rollback_causal(step_id)`. Step 21 exercises both with a real DeepSeek agent over three fresh failure-and-recovery sessions.
- **Remaining:** the model still lacks a structured selective-commit preview, path-by-path justification, and human approval workflow.
- **Impact:** The agent can now request the distinguishing AET recovery operation, but the post-recovery commit UX is still a benchmark-controlled policy step.

#### G8. Policy model is path-glob only
- **Where:** `policy.py` allow/deny globs + cache ignores.
- **Missing:** progressive permission (YoloFS-like), capability classes, rate limits, human-in-the-loop approve UX, integration hooks for IFC/policy engines (ActPlane-class).
- **Impact:** Enough for demo deny of `*.pem`; thin for adversarial or enterprise agent mediation (Problem B still deferred — consistent, but a product gap).

#### G9. Snapshot / WAL storage still scales with speculative state
- **Evidence:** Step 12 content-addressing helps; Step 18 incremental snapshots cut snapshot stage time but did **not** deliver clear end-to-end ms/step wins; STATUS still lists scalable snapshots as open.
- **Gap:** Long speculative trajectories + historical commit + WAL backups can still amplify disk and pause times; no bound/SLA.

#### G10. Persistent worker is a correctness-sensitive fast path
- **Where:** `try_worker.py` + `SharedSemisolate._run_worker`
- **Good:** crash injection + one-shot fallback exists (`inject_worker_crash_once`).
- **Gaps:** framed IPC trusts worker inside overlay; fallback path reintroduces per-call try cost; interaction of worker death with in-flight upperdir / mounts needs ongoing chaos coverage beyond current robustness suite.

---

### P2 — Performance (motivation still open)

From `experiments/results/motivation_runtime_comparison.md` (deterministic long coding traj):

| mode | ~ms/step | host polluted |
|---|---:|:---:|
| bare | ~50 | yes |
| agenttx_without_read_tracing | ~63 | no |
| agenttx_full | ~148 | no |
| per_call_try / shared_try | ~250+ | no (but high failures in that matrix) |

#### G11. Full AgentTX still ~3× bare on the motivation workload
Read tracing dominates the gap (`~63` → `~148` ms/step). Worker iteration removed most setup tax; remaining cost is ledger/overlay/trace — STATUS correctly keeps this open.

#### G12. Optimization claims need stronger measurement hygiene
`optimization_iterations.md` notes non-interleaved VM runs for some iterations. Fine for engineering guidance; weak for paper tables until interleaved / locked-CPU repeats.

---

### P3 — Evaluation & paper gaps

#### G13. External systems bakeoff incomplete
`docs/related-work-2026.md` positions BranchFS / Waypoint / YoloFS / DeltaBox / Crab / Sandlock / ActPlane well, but runnable VM matrix still blocked for several artifacts/environments. Without head-to-head numbers, novelty vs YoloFS/BranchFS stays qualitative.

#### G14. Real multi-package / hostile agent workloads thin
Real DeepSeek refactor robustness looks strong (`host leak 0`, `tests pass 1.0` over 3 repeats), and Step 21 now covers mid-flight causal rollback requested by the agent with 3/3 successful root selections and repairs. The tasks remain seeded single-package fixtures — not multi-package, not adversarial tool misuse, and not long open-ended CI-fix loops.

#### G15. Concurrent agents only in disjoint workspaces
Robustness suite runs concurrent agents into separate subdirs/sessions. Shared-workspace multi-agent interference / commit fencing is unproven.

#### G16. No paper draft yet
HLS paper skeleton still listed in STATUS; code+eval volume now exceeds writing.

---

### P3 — Engineering / maintainability

#### G17. `optimization_history/` duplicates large module snapshots
Useful for ablation archaeology; increases review surface and drift risk vs live `src/agenttx/*`. Needs a clear “live vs frozen” policy (already partially documented, still heavy).

#### G18. Local Windows mirror is not the source of truth
Constraints keep writes on VM; a stale local scaffold can mislead reviewers (observed). Release/CI story for a single canonical tree is still informal.

---

## 4. Gap map (code → claim)

```
Agent / LLM tools ──G7──▶ recovery implemented; commit planning thin
        │
        ▼
   Harness + Policy ──G1/G8──▶ runtime invariant done; policy=path globs only
        │
        ▼
   AgentTX runtime ──G2──▶ default rollback temporal
        │
   ┌────┼──────────────┐
   ▼    ▼              ▼
Ledger Trace      Semisolate/Worker
 G3    G4           G10 / G11
 hier+symlink     strace-partial   overlay+trace cost
 only             Linux-only
        │
        ▼
  Selective commit + WAL ──G5/G9──▶ recoverability ≠ external atomicity; storage growth
        │
        ▼
     Host FS   (+ G6 non-FS effects missing)
```

---

## 5. Suggested next work (planning only — do not implement here)

Ordered for research payoff:

1. **Prepare G2** — alias/hardlink/bind coverage + replay suite, then switch default rollback to causal (keep temporal as flag).
2. **Formalize G4** — syscall coverage matrix; fail-closed or explicit “untracked” effect kind when unsupported ops occur.
3. **Extend G7** — add selective-commit preview and path-level rationale to the now-working recovery control plane.
4. **Paper + G13** — write HLS draft while standing up BranchFS/Waypoint where artifacts allow; keep blocked baselines in an appendix table.
5. **Perf G11** — treat “full tracing cost” as a first-class design point (sampling, trusted-manifest, or kernel support), not only micro-optimizations.

---

## 6. Review verdict

AgentTX is past “prototype that barely commits.” The core AET loop and agent-requested causal recovery are implemented and evidenced. The important remaining gaps are **defaults and completeness** (causal-by-default, alias/trace coverage, policy on all commit paths), **scope** (non-FS effects and harder multi-package/adversarial agents), and **external credibility** (bakeoffs + paper), plus an honest performance tax under full tracing.

This review is updated as implementation and evaluation gaps close.
