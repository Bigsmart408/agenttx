# L0: AgentTX | Venue: OSDI (12 pages) | 2026-08-18

## 1. Big Background

Coding agents are evolving from single-turn code generators into long-running systems
programs.  One task may inspect a repository, edit several modules, run compilers and
tests, and revise a failed approach. The user regards
these actions as one adaptive trajectory, but the operating system exposes them as
ordinary processes whose intermediate filesystem effects are immediately visible unless
an external isolation layer intervenes.

**Judgment: PASS.**  The trend is specific to tool-using coding agents and establishes a
systems problem rather than a language-model quality problem.

## 2. Small Background

Current isolation mechanisms expose invocation, session or branch, and temporal
checkpoint boundaries. A try invocation can wrap a command, script, or whole
agent session. Repeating isolation per tool call fragments trajectory state and
repeats setup; wrapping the session alone does not supply internal step histories,
dependencies, or context reconstruction. A session or branch preserves state but naturally
commits or aborts the whole unit.  A checkpoint restores a prefix, so all later work is
discarded even when it did not depend on the failure.

**Judgment: PASS.**  This taxonomy identifies the recovery-unit mismatch that AgentTX
targets without claiming that all prior systems have identical implementations.

## 3. Existing Problems

- **P1: safety versus recovery utility.** Bare execution is fast but exposes every
  intermediate mutation.  Coarse recovery keeps the host clean but discards valid work.
  Across 400 paired manifests, turn-checkpoint and temporal-step recovery discarded
  54.02% and 56.90% of valid work.  **Severity: high.**
- **P2: causal correctness across opaque tools.** Command text does not reveal transitive
  reads, negative lookups, or aliases.  Removing dependency capture caused the 64-call
  ablation to remove only 4% of invalid descendants.  **Severity: high.**
- **P3: practical trajectory isolation.** Rebuilding isolation around every call breaks
  continuous speculative state and costs 1684.9 ms/step on the 64-call workload.  The
  full AgentTX path costs 90.9 ms/step; unsafe bare execution costs 8.4 ms/step.
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
Using invocation isolation separately for each call can also recreate the isolation
context. Invocation lifetime alone does not determine whether internal effects can be
selected and reconstructed by dependency.

**Judgment: PASS.**  This root cause explains C1--C3 and the state-continuity component of
P3.  Residual tracing and OverlayFS costs are implementation limits, not new root causes.

## 5. Key Idea

Treat the whole agent trajectory as an **Agent Effect Transaction (AET)**. Execute all
tool calls in one shared speculative view, derive dependencies among observed effects,
and reconstruct the workspace and agent-visible context without the affected subgraph.

**Thesis.** The trajectory should be the speculation unit, while a dependency-defined
effect subgraph should be the recovery unit.  AET encodes this separation; AgentTX is its
Linux realization.

**Judgment: PASS.**  The idea directly changes the recovery unit from time to causality and
is distinct from the AgentTX prototype that realizes it.

## 6. Design Points

- **DP1 (core): effect capture and causal ledger.** Capture READ, NEGATIVE, WRITE, and
  DELETE effects together with agent-visible result flow; normalize hierarchy and
  supported aliases; derive cross-step dependencies. This addresses P2 and C1, and
  partially addresses C2.
- **DP2 (core): shared speculation and selective workspace reconstruction.** Reuse one semisolate,
  retain versioned per-step upperdir state, compute the transitive causal closure, and
  restore only affected paths.  Fail closed on retained-effect overlap.  This addresses
  P1, P3, and C3.
- **DP3 (core): context-aligned rollback.** Bind tool-call spans to ledger steps,
  close recovery over filesystem and message dependencies to one fixed point, and use
  that same step set to reconstruct the workspace and model-visible history. This
  completes C3 by keeping the agent's context aligned with the reconstructed workspace.
- **DP4 (bonus engineering): amortized long-trajectory execution.** A persistent `try`
  worker, trusted-tool declarations, content-addressed blobs, and incremental snapshots
  reduce repeated setup and storage work.  This improves P3 but is not presented as an
  additional correctness challenge.

All core problems are covered within an explicit identity contract.  Tested hard-link
groups are supported; bind mounts, external aliases, and arbitrary generation changes
remain fail-closed C2 coverage rather than hidden behind the ledger abstraction.

## 7. System and Experiments

**System.** A roughly 3.5 K-line Python/Linux prototype built on unprivileged `try`,
OverlayFS, and `strace`.  It implements a persistent shared semisolate, effect ledger,
selective rollback, context rewind, and content-addressed snapshots.

**Evidence.** The current evaluation includes a 64-call runtime comparison, a canonical
x86 ten-write comparison with 50 fresh workspaces per mode, 32/64/96/128-call scaling,
400 paired motivation manifests, artifact-native YoloFS and Waypoint recovery on all
400 manifests, 144 real-overlay causal-retention runs, a dependency
ablation, conversation-rewind tests, three real-agent causal-recovery runs, and
27 real-LLM replay-token samples.

**Headline results.** Full causal recovery retained 100% of independent work and removed
100% of invalid descendants in the controlled DAG suite.  At the largest replay input it
avoided 1,424.7 tokens versus an optimistic temporal checkpoint and 3,340.3 versus a whole
abort.  In the canonical repeated comparison, full AgentTX reached 58.757 ms/step mean and
68.169 ms/step p99, 66.4% below per-call `try` at p99, and was causal-correct in 50/50 runs.
The separate 64-call full path remained 43% below per-call isolation.

**Claim boundary.** YoloFS and Waypoint results are artifact-native recovered-state
measurements on the 400-case suite. Other checkpoint and whole-abort results are
oracle-aided semantic upper bounds for their recovery granularity, not measurements of
named external systems. Real-agent tasks are seeded, causal rollback is explicit rather
than the default, the object-identity contract is incomplete outside tested hard-link
groups, and non-filesystem effects are outside the transaction.

**Judgment: PASS for draft stage.**  The system and evidence support the central semantic
claim. Broader repository coverage remains required for an OSDI submission.

## Chinese design draft steering — 2026-10-27

The user requests a Chinese first draft before editing the English manuscript.
The motivation anchor is boundary-induced loss of independent work. Oracle
localization belongs only to the controlled motivation methodology: it isolates
granularity loss and is not a deployment assumption. The user's subsequent
correction explicitly rejects treating the root as known in the design.
The workflow starts from a failure observation, constructs candidate recovery
plans, and uses the dependency graph to determine invalidation sets and test
them. Fault localization is explained separately, with no claim that the true
root is available or uniquely identified. The central contribution remains
dependency-selected recovery rather than improved root-cause diagnosis.

For this draft, express the existing challenges as: (1) discovering recovery
dependencies across opaque tool calls and observable result flow; (2) relating
effects to objects and their historical versions across supported names and
aliases; (3) reconstructing a non-prefix workspace and matching agent-visible
context from one survivor set. Keep the original three concerns, but explain
the failure caused by omitting each. Do not introduce fault localization as a
fourth core challenge. Object identity is an input to graph construction and
version reconstruction, not an unrelated filesystem feature.

Preserve the native-trace boundary: avoidable loss depends on interleaved
independent work; the controlled study does not establish universal large
real-agent token savings. A graph edge is an observed or conservative recovery
constraint, not a proof of complete semantic causality. Do not claim minimal
true causal sets, unique root causes, or complete hidden-reasoning recovery.

Output: `paper/design-workflow.zh-CN.md`, plus challenge-change rationale in
`review/challenges-revision.zh-CN.md`. These are reviewable proposals, not a
replacement of the English manuscript or a final-paper completion claim.

## Approved English design revision — 2026-10-27

The user now authorizes direct revision of the English design using the Chinese
draft's reasoning, with workflow-level prose and pseudocode where an algorithm
needs precision. Revise Sections 4–5 and the two directly connected challenge
paragraphs. Retain the existing experimental results and literature sections.
Do not treat the true failure source as known. Explain candidate localization
as a separate stage, and use the dependency graph to determine the invalidation
set for each candidate. Keep the causal graph central to reducing boundary
loss; object history and matching visible context realize that selection.

The user excludes arXiv:2608.10502 from further consideration. Do not add it or
its claims to the manuscript. No broad first-causal-rollback or first-joint-
context-recovery claim is needed. Implementation descriptions must match the
current affected-path historical restoration, not a fresh-generation swap.

## Completed real-workload integration — 2026-10-30

The user authorizes analysis and plotting of the finished TB campaign alongside
SWE-Bench, direct paper integration, and replacement of Figure 8's composition.
The accepted thesis and causal-chain design are unchanged. This revision adds
application-level cost evidence; it does not attribute all savings to graph
selection without mechanism ablations.

- SWE: 300 tasks × six policies; one run per cell, 1,799 complete. AgentTX
  succeeds on 285/300 under target-test and recovery checks. Matched-success
  API-token savings against no rollback/checkpoint/branch are
  58.43%/17.55%/27.86%.
- TB: 241 tasks × two harnesses × six policies; all 2,892 cells terminal after
  registered outcome retries and separately audited environment/provider
  repairs. Latest AgentTX full-contract successes are 208/241 (SWE-agent) and
  223/241 (Codex), not first-attempt pass@1.
- Complete-cost comparisons include every recorded failed/retry/replay attempt
  and pair only successful, fully billed histories in the same compatible
  configuration. The frozen audit has 13 configuration groups, 11 nonempty,
  and 1,886 distinct task/harness/control pairs. No usage is imputed as zero.
  Main-control savings are 24.70%/44.69%/35.91% for SWE-agent and
  26.47%/25.39%/28.43% for Codex.
- Boundaries: costs condition on eligible successful pairs, context settings
  differ between policies, and the cohorts have different retry rules. SWE
  injection uses gold-patch file paths and success is not official resolved
  rate. TB is protocol-validation evidence: uniform hard token-budget and
  prompt-accounting gates are incomplete.
- Preserve counterevidence: TB/Codex costs 10.85% more than Ideal on matched
  pairs (task-bootstrap interval spans zero), succeeds less often than branch
  rollback, and has negative ML/training category comparisons. No universal
  benefit or cross-harness ranking is claimed.

Reproducible sources, hashes, pair counts and conditional task-bootstrap
intervals are recorded in `review/realworld-results-20261030/analysis.json`.
Other missing canonical evaluation evidence remains open.
