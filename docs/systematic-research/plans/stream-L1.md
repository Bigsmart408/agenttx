# L1 Structure: AgentTX | OSDI (12 pages) | 2026-08-09

This structure is extracted from the existing draft and reconciled with
`problem.md`, `research-challenges.md`, `experiments-explained.md`, `review.md`,
`STATUS.md`, and the measured optimization history.  It follows the OSDI
systems-paper budget while preserving the current USENIX skeleton.

## Page Budget

| Section | Target pages | Purpose |
|---|---:|---|
| Abstract | 0.25 | Five-step problem, gap, insight, system, evidence summary |
| 1. Introduction | 1.5 | Establish causal recovery as the missing transaction unit |
| 2. Background and Problem | 1.0 | Define effects and separate isolation from recovery granularity |
| 3. Observations and Motivation | 1.25 | Quantify coarse recovery and naive isolation; derive challenges |
| 4. Agent Effect Transactions | 2.0 | Define the abstraction, effect DAG, rollback invariant, frontier |
| 5. Design and Implementation | 2.0 | Explain how the Linux prototype realizes the abstraction |
| 6. Evaluation | 3.0 | Validate cost, semantics, agent usability, token value, robustness |
| 7--9. Discussion, Related Work, Conclusion | 1.0 | Scope claims, position novelty, close the thesis |
| **Total** | **12.0 + references** | Expand from the current 8-page initial draft as evidence matures |

## Abstract

A. Long agent trajectories create provisional effects that direct execution exposes.

B. Command, session, and temporal recovery units cannot retain interleaved valid work.

C. The key insight is to transact over causal effects rather than a time boundary.

D. AgentTX realizes this insight with an effect DAG, shared speculation, selective
reconstruction, and a durable frontier.

E. Report semantic retention, replay-token savings, and honest runtime overhead in five
sentences without citations or undefined terms.

## Section 1: Introduction

**Purpose:** Move from the field trend to the recovery-unit mismatch, derive the three
systems challenges, then present AET and its evidence.

A. **Domain context.** Coding agents are long-running systems programs whose intermediate
filesystem mutations are provisional.

B. **Existing approaches.** Classify bare execution, per-call isolation, session/branch
isolation, and temporal checkpointing.  Distinguish state continuity from recovery
granularity.

C. **Observed failure of existing units.** Quantify host pollution, 260.7 ms/step per-call
cost, 41% temporal retention, and 4% invalid removal without dependencies.  Introduce the
three causal challenges: Dependency Discovery, Object Identity, and Selective
Reconstruction.

D. **Root cause and insight.** Existing substrates index state by command, time, and path;
agent failures propagate through causal dependencies among versioned effects.  State the
AET thesis in one paragraph.

E. **Techniques.** Present effect capture/ledger and selective reconstruction/frontier as
core techniques.  Present persistent worker and incremental snapshots as extra performance
engineering rather than a forced fourth correctness challenge.

F. **System and headline evidence.** Name the Linux prototype, state 100%/100% controlled
semantic result, avoided replay tokens, and the 148.5 ms/step overhead boundary.

G. **Contributions.** Four one-line bullets: problem evidence, abstraction, system, and
evaluation.  Keep problem and key idea separate.

*Figures:* none.  The Introduction should not duplicate the motivation or architecture
figures.

## Section 2: Background and Problem

**Purpose:** Define the minimum vocabulary and make recovery granularity explicit before
the system appears.

A. **Trajectory effects.** Define tool boundaries and READ/NEGATIVE/WRITE/DELETE.  Explain
why negative lookup and directory ancestry affect dependencies.

B. **Isolation versus recovery.** Compare bare, command, session, checkpoint, and causal
recovery units.  State that recovery-granularity rows are abstractions, not external
artifact results.

C. **Requirements and scope.** Derive host cleanliness, continuous speculative state,
selective recovery, durable publication, and unprivileged deployment.  Exclude adversarial
confinement and irreversible non-filesystem effects.

*Tables:*

- Table 1: isolation and recovery granularity | col=double | at step B | existing
  `tab:granularity`.

## Section 3: Observations and Motivation

**Purpose:** Earn the right to propose AET with measurements and then derive the design
challenges from the root cause.

A. **Observation 1 -- naive isolation is safe but costly.** Use the deterministic 64-call
workload and preserved optimization history.  Keep bare as an unsafe lower bound and
no-trace as an incorrect ablation.

B. **Observation 2 -- time is not causality.** Give the interleaved producer, descendant,
and independent-work example.  Connect lost files to real replay-token cost.

C. **Root-cause drill-down.** State Dependency Discovery, Object Identity, and Selective
Reconstruction.  For each, explain the mechanism and consequence.  Mark hard-link copy-up
as measured incomplete identity coverage.

D. **Design implication.** Dependency capture and reconstruction must be co-designed; a
smaller rollback set without a correct graph is unsafe.

*Figures:*

- Figure 1: optimization history | col=double | at step A | existing
  `FIG-Motivation-Optimization.pdf`.
- Future optional figure: one three-step causal example | col=single | at step B | create
  only if the text example is insufficient.

## Section 4: Agent Effect Transactions

**Purpose:** Separate the AET abstraction from the AgentTX implementation.

A. **Overview and goals.** Introduce session state, speculative view, ledger, recovery, and
frontier.  Map each abstraction component to the requirements in Section 2.

B. **Effect DAG.** Define ledger nodes, overlap, dependency rules, negative dependencies,
and the transitive causal closure.

C. **Selective reconstruction.** Define the write/delete target set, historical source
state, retained-effect overlap check, and fail-closed invariant.

D. **Commit frontier.** Define monotonic approval, rolled-back holes, historical same-path
materialization, and the separation between speculative recovery and host publication.

*Figures:*

- Figure 2: AET/AgentTX lifecycle overview | col=double | at step A | existing
  `fig:overview`, replace the LaTeX box sketch with a publication-quality diagram later.

## Section 5: Design and Implementation

**Purpose:** Explain how AgentTX realizes AET and where the substrate limits correctness.

A. **Effect capture and identity.** Describe upperdir fingerprints, whiteouts, `strace`,
fd-relative resolution, symlink aliases, trusted declarations, and fail-closed tracing.
Discuss why command-string inference was rejected.

B. **Continuous speculative execution.** Explain shared `try -N`, the persistent worker,
fallback after crash, and synchronized workspace/PWD semantics.  State the tradeoff between
amortized setup and a correctness-sensitive long-lived worker.

C. **Versioned reconstruction.** Explain content-addressed per-step snapshots, incremental
upperdir replay, path restoration, temporal swapping, and hard-link copy-up limits.

D. **Durable frontier and policy.** Explain anchored include filters, historical commit,
policy enforcement before WAL preparation, WAL phases, reload behavior, and why recovery
does not imply external multi-path atomic visibility.

*Tables:*

- Future operation-to-effect coverage table | col=single | at step A | needed before final
  submission to make the modeled/unsupported syscall contract explicit.

## Section 6: Evaluation

**Purpose:** Answer explicit research questions and keep semantic, user-cost, and runtime
claims distinct.

A. **Questions and setup.** State RQ1 overhead, RQ2 semantic precision/retention, RQ3 agent
usability and replay cost, and RQ4 robustness.  Describe VM, workloads, repeats, p50/p95,
host-cleanliness checks, and why external artifacts are qualitative for now.

B. **RQ1 -- runtime overhead and scaling.** Compare the six runtime modes, explain why bare
and no-trace are not correctness-equivalent, report length scaling and tail latency, and
connect the result to the preserved optimization history.

C. **RQ2 -- causal retention.** Describe the 144 real-overlay runs and varied DAG factors.
Report useful retention and invalid removal together.  Use the no-dependency ablation to
show that policy without capture is unsafe.

D. **RQ3 -- real-agent recovery and replay tokens.** Separate the three agent control-plane
runs from the 27 controlled replay samples.  State that zero causal replay excludes
diagnosis, tests, and pre-failure tokens.

E. **RQ4 -- robustness and resource cost.** Cover crash fallback, 256-step reload, disjoint
four-agent concurrency, snapshot bytes, and explicit same-path concurrency limits.

*Tables and figures:*

- Table 2: current 64-call runtime | col=single | at step B | existing `tab:runtime`.
- Figure 3: causal retention and ablation | col=double | at step C | existing
  `FIG-Causal-Retention.pdf`.
- Figure 4: avoided replay tokens | col=double | at step D | existing
  `FIG-Token-Recovery.pdf`.
- Figure 5: robustness bundle | col=double | at step E | existing
  `FIG-Robustness.pdf`.

## Section 7: Discussion and Limitations

**Purpose:** Defend the central claim by making design and implementation limits concrete.

A. Explain why hard-link copy-up blocks causal-by-default and what substrate would be
needed.

B. Explain `strace` portability/completeness and why eBPF changes collection cost but not
effect semantics.

C. Bound non-filesystem effects, multi-path visibility, seeded tasks, and disjoint
concurrency.

D. Separate final-submission evidence gaps from architectural limitations.

*Figures:* none.

## Section 8: Related Work

**Purpose:** Position the recovery unit rather than claim universal superiority.

A. Effect capture and semisolates: `try` is the substrate; AgentTX contributes cross-step
causal orchestration.

B. Agent-native filesystems and branches: compare permission/snapshot/branch selection
with causal subgraph selection.

C. Checkpoint and restore: distinguish broader state coverage and checkpoint efficiency
from choosing non-contiguous invalid effects.

D. State external-artifact comparison status fairly.

*Tables:* add a capability matrix only after external systems and their documented
semantics are verified.

## Section 9: Conclusion

A. Long agents need a boundary larger than one command and a recovery unit smaller than a
session.

B. AET changes the recovery unit from time to causal effects; AgentTX realizes it with a
shared speculative view, causal ledger, reconstruction, and durable frontier.

C. Close with the strongest controlled semantic and replay results, then one precise scope
sentence.  Do not introduce future-work lists.

*Figures:* none.

## Cross-Section Contracts

- Use **Agent Effect Transaction (AET)** for the abstraction and **AgentTX** for the Linux
  prototype.
- Use **causal rollback** for explicit non-contiguous recovery; do not imply it is the
  current default API.
- Use **avoided replay tokens**, not total token savings.
- Call checkpoint and whole-abort results **recovery-granularity emulations**.
- Treat persistent worker and incremental snapshots as additional performance engineering,
  not correctness challenges.
- Every evaluation caption states a takeaway, and every numerical claim is traceable to a
  committed result artifact.
