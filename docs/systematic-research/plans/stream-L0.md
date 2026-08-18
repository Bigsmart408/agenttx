# L0: AgentTX | Venue: OSDI (12 pages) | 2026-08-18

## 1. Big Background

Coding agents are evolving from single-turn code generators into long-running systems
programs.  One task may inspect a repository, edit several modules, run compilers and
tests, revise a failed approach, and publish only the verified result.  The user regards
these actions as one adaptive trajectory, but the operating system exposes them as
ordinary processes whose intermediate filesystem effects are immediately visible unless
an external isolation layer intervenes.

**Judgment: PASS.**  The trend is specific to tool-using coding agents and establishes a
systems problem rather than a language-model quality problem.

## 2. Small Background

Current isolation mechanisms choose one of three units: a command sandbox, a session or
branch, or a temporal checkpoint.  A command sandbox protects the host but fragments
trajectory state and repeats setup.  A session or branch preserves state but naturally
commits or aborts the whole unit.  A checkpoint restores a prefix, so all later work is
discarded even when it did not depend on the failure.

**Judgment: PASS.**  This taxonomy identifies the recovery-unit mismatch that AgentTX
targets without claiming that all prior systems have identical implementations.

## 3. Existing Problems

- **P1: safety versus recovery utility.** Bare execution is fast but exposes every
  intermediate mutation.  Coarse recovery keeps the host clean but discards valid work.
  In the 64-call controlled DAG, temporal recovery retained 41% of independent work and
  whole-session recovery retained none.  **Severity: high.**
- **P2: causal correctness across opaque tools.** Command text does not reveal transitive
  reads, negative lookups, or aliases.  Removing dependency capture caused the 64-call
  ablation to remove only 4% of invalid descendants.  **Severity: high.**
- **P3: practical trajectory isolation.** Rebuilding isolation around every call breaks
  continuous speculative state and costs 260.7 ms/step on the 64-call workload.  The
  optimized AgentTX path costs 148.5 ms/step; unsafe bare execution costs 49.7 ms/step.
  **Severity: high, performance remains open.**

### Systems challenges and causal test

- **C1 -- Dependency Discovery.** Recover producer--consumer edges from actual effects of
  opaque subprocess trees.  **Causal test: PASS.** A complete causal effect history makes
  hidden cross-command dependencies explicit.
- **C2 -- Object Identity.** Decide when different paths denote the same object across
  hierarchy, symlinks, renames, hard links, and copy-up.  **Causal test: PASS.** Stable
  effect identities remove path-string ambiguity.  The tested pre-existing and
  upper-created hard-link groups pass; bind mounts, external aliases, and arbitrary
  generation changes remain the measured boundary.
- **C3 -- Selective Reconstruction.** Materialize the complement of a non-contiguous
  failed subgraph while preserving later independent effects.  **Causal test: PASS.** A
  versioned causal history identifies both the rollback set and the historical states
  needed to rebuild it.

## 4. Root Cause

The fundamental bottleneck is that existing isolation substrates organize speculative
state by **command, namespace, time, and pathname**, whereas an agent failure propagates
through **causal dependencies among versioned effects**.  Prior approaches can expose or
restore a snapshot, command, session, or branch, but they do not maintain the causal
history needed to select and reconstruct an arbitrary subgraph inside one trajectory.
Per-call designs also recreate the isolation context because the trajectory is not a
first-class transactional unit.

**Judgment: PASS.**  This root cause explains C1--C3 and the state-continuity component of
P3.  Residual tracing and OverlayFS costs are implementation limits, not new root causes.

## 5. Key Idea

Treat the whole agent trajectory as an **Agent Effect Transaction (AET)**: execute all tool
calls in one shared speculative view, convert observed filesystem effects into a causal
DAG, reconstruct only the failed causal closure, and publish approved effects through a
durable commit frontier.

**Thesis.** Causal effect transactions are a better recovery unit than temporal snapshots
for long-running coding agents whose valid and invalid work interleave.

**Judgment: PASS.**  The idea directly changes the recovery unit from time to causality and
is distinct from the AgentTX prototype that realizes it.

## 6. Design Points

- **DP1 (core): effect capture and causal ledger.** Capture READ, NEGATIVE, WRITE, and
  DELETE effects at tool boundaries; normalize hierarchy and supported aliases; derive
  cross-step dependencies.  This addresses P2 and C1, and partially addresses C2.
- **DP2 (core): shared speculation and selective reconstruction.** Reuse one semisolate,
  retain versioned per-step upperdir state, compute the transitive causal closure, and
  restore only affected paths.  Fail closed on retained-effect overlap.  This addresses
  P1, P3, and C3.
- **DP3 (core): durable publication.** Advance a policy-checked commit frontier, reconstruct
  historical versions when later work overlaps, and protect multi-path materialization
  with a WAL.  This completes P1/C3 by separating recovery from publication.
- **DP4 (bonus engineering): amortized long-trajectory execution.** A persistent `try`
  worker, trusted-tool declarations, content-addressed blobs, and incremental snapshots
  reduce repeated setup and storage work.  This improves P3 but is not presented as a
  fourth correctness challenge.

All core problems are covered within an explicit identity contract.  Tested hard-link
groups are supported; bind mounts, external aliases, and arbitrary generation changes
remain fail-closed C2 coverage rather than hidden behind the ledger abstraction.

## 7. System and Experiments

**System.** A roughly 3.5 K-line Python/Linux prototype built on unprivileged `try`,
OverlayFS, and `strace`.  It implements a persistent shared semisolate, effect ledger,
selective rollback, content-addressed snapshots, commit policy, frontier materialization,
and crash-recovery WAL.

**Evidence.** The current evaluation includes a 64-call runtime comparison, a canonical
x86 ten-write comparison with 50 fresh workspaces per mode, 54/64/96-call scaling,
preserved optimization iterations, 144 real-overlay causal-retention runs, a dependency
ablation, three real-agent causal-recovery runs, 27 real-LLM replay-token samples, worker
crash injection, a reloadable 256-step session, and four disjoint concurrent agents.

**Headline results.** Full causal recovery retained 100% of independent work and removed
100% of invalid descendants in the controlled DAG suite.  At the largest replay input it
avoided 1,424.7 tokens versus an optimistic temporal checkpoint and 3,340.3 versus a whole
abort.  In the canonical repeated comparison, full AgentTX reached 58.757 ms/step mean and
68.169 ms/step p99, 66.4% below per-call `try` at p99, and was causal-correct in 50/50 runs.
The separate 64-call full path remained 43% below per-call isolation.

**Claim boundary.** Checkpoint and whole-abort results emulate recovery granularity rather
than external artifacts.  Real-agent tasks are seeded, causal rollback is explicit rather
than the default, the object-identity contract is incomplete outside tested hard-link
groups, and non-filesystem effects are outside the transaction.

**Judgment: PASS for draft stage.**  The system and evidence support the central semantic
claim.  External artifact comparisons and broader repositories remain required for an
OSDI submission.
