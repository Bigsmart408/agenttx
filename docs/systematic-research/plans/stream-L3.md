# L3 Consistency Report: AgentTX | OSDI | 2026-09-23

## Current status

**OPEN -- evaluation restructuring required.** The core rollback thesis and
workspace--context semantics remain consistent, but the canonical experiment
order changed after the last paper audit. The required order is now:

```text
Microbenchmark
  -> Real-world Workloads (SWE-Bench Lite and Terminal-Bench)
  -> Breakdown
  -> Other Overheads
```

Correctness and interrupted-recovery tests move to the appendix. The current
`paper/main.tex` still presents the previously completed pilot evidence as four
RQ subsections. It must not be marked structurally complete until the same 400
Motivation manifests have complete full-system Microbenchmark results and the
complete live-agent real-world runs replace the pilot/oracle cells. The
Evaluation prose must then be reordered accordingly.

## Scope

This audit checks `paper/main.tex` against the L0 thesis, L1 section contracts,
the current result artifacts, and the systems-paper writing guide. It covers
novelty hierarchy, terminology, quantitative claims, prose, references, and
the compiled document.

## Novelty and structure

**PASS.** The paper now states one claim throughout: the trajectory is the
speculation unit, while a dependency-defined subgraph of versioned effects is
the recovery unit. AET is the abstraction that separates these units. AgentTX
is the Linux realization through effect capture, object-aware history,
selective workspace reconstruction, and context-aligned rollback.

The three challenges map directly to the abstraction and implementation:

- Dependency Discovery maps to typed effect capture and ledger edges.
- Object Identity maps to hierarchy, symlink, rename, and tested alias handling.
- Selective Reconstruction maps to affected-subgraph selection, historical versions,
  and conversation rewind.
OverlayFS, tracing, persistent workers, and incremental snapshots are presented
as mechanisms or optimizations, not as independent conceptual contributions.
Publication is explicitly outside the paper's recovery scope.

## Results and claim boundaries

**PASS.** The motivation reports one population of 400 paired manifests; no
separate small-case validation is part of the paper protocol. The current
results report 54.02% and 56.90% collateral
loss for turn-checkpoint and temporal-step recovery, and 97.5% non-prefix exact
survivor states. The evaluation consistently reports 100% independent-work
retention, 100% invalid-descendant removal, and replay avoidance of 1,424.7 and
3,340.3 tokens at 48 entries.

The paper distinguishes controlled recovery semantics from official task
repair, local boundary emulations from artifact-native systems, and avoided
replay from total session-token savings. Filesystem identity, external side
effects, publication scope, model count, and native-agent evidence remain
explicitly bounded.

The 400-case artifact comparison is complete for try, BranchFS, YoloFS, and
Waypoint. YoloFS and Waypoint each discard 65.89% of valid work, compared with
54.02% for the oracle-aided turn-boundary upper bound. BranchFS and try discard
the complete speculative unit through their native abort/discard operations.

Figure 3 also reports all four artifact-native results over the 210-case
dependency sweep. YoloFS and Waypoint select identical survivor states, as do
BranchFS and try. The figure names every system and distinguishes coincident
measurements with separate markers.

## Prose and terminology

**PASS.** Abstract, Introduction, AET, implementation, evaluation, related
work, and conclusion use the same abstraction--implementation hierarchy.
The Introduction summarizes the Motivation's three qualitative findings but
does not repeat its case counts, per-policy percentages, or figure-level data.
Motivation introduces the Ideal oracle as removing the failed step and its
dependent steps without assuming the later AET terminology. Paragraphs use at
most six sentences; substantive prose sentences use at most 30 words. Internal
experiment labels and stale three-challenge wording are absent.

## LaTeX and visual QA

- `make -C paper` completes and produces a 15-page PDF.
- No undefined references, undefined citations, overfull boxes, LaTeX errors,
  or duplicate figure/table anchors remain.
- All Motivation figures are single-column; their internal subplots render
  without clipping, overlap, or displacement.
- The selected motivation and external-artifact suites pass all 12 tests with
  the repository root on `PYTHONPATH`.

**Verdict:** the draft remains compilable and preserves the central novelty
claim, but L3 is open because Evaluation has not yet been migrated to the new
evidence order and still lacks the canonical system-microbenchmark,
full real-world, interrupted-recovery, and resource-overhead datasets.
