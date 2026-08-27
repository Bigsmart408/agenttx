# L3 Consistency Report: AgentTX | OSDI | 2026-08-18

## Scope

This audit checks the polished `paper/main.tex` against `stream-L0.md`, the
merged-section `stream-L1.md`, the systems-paper guide, and current result
artifacts.  Missing experiments are recorded in
`docs/paper-outline-and-experiment-plan.md` instead of being hidden by prose.

## Thesis and structure

**PASS.** The abstract and Introduction state one thesis: the trajectory is the
speculation unit, while a causal subgraph of versioned effects is the recovery
unit.  Section 2 combines the minimum background with execution and recovery
evidence.  Section 3 defines AET without Linux mechanisms.  Section 4 maps the
abstraction to AgentTX.  Evaluation, limitations, related work, and conclusion
all retain this recovery-unit distinction.

The three challenges map to the design and evidence:

- Dependency Discovery maps to effect capture, the ledger, and the no-dependency
  ablation.
- Object Identity maps to hierarchy, symlinks, rename, and tested hard-link
  groups.  Bind mounts, external aliases, and arbitrary generation changes fail
  closed.
- Selective Reconstruction maps to causal closure, historical state, overlap
  rejection, frontier publication, and WAL recovery.

Persistent workers and incremental snapshots remain performance engineering,
not a fourth correctness challenge.

## Result consistency

**PASS for the claims left in the draft.** The manuscript consistently reports:

- 144 controlled real-overlay runs over 48 aggregated configurations;
- 100% independent-work retention and 100% invalid-descendant removal;
- 41% temporal retention at 64 calls and 4% invalid removal without read
  dependencies;
- 1,424.7 and 3,340.3 avoided replay tokens at 48 document entries;
- 148.5 ms/step for the 64-call full path, 43% below per-call isolation;
- 58.757 ms/step mean and 68.169 ms/step p99 for the canonical x86 ten-write
  comparison, with 50/50 causal-correct full-system runs;
- 202.746 ms/step p99 for per-call `try`, making the full path 66.4% lower at
  p99 on that fixed trajectory.

The token result is always called **avoided replay**, not total session-token
savings.  Temporal checkpoint and whole-session abort are recovery-granularity
emulations rather than external artifacts.  The GitHub-context figure is
explicitly exploratory: each task and policy has one run, and tokens are not
treated as savings when the success predicate differs.

The 50-run table and p99 figure use only the canonical x86 artifact.  The paper
keeps that fixed ten-write trajectory separate from the 64-call coding workload
and does not compare their absolute latency values.

## Claim boundaries

**PASS.** The draft does not claim complete syscall coverage, arbitrary
filesystem identity, non-filesystem transactions, external multi-path atomic
visibility, automatic upstream issue resolution, or measured superiority over
unavailable artifacts.  Tested hard-link cases are described as supported;
unsupported topology remains fail closed.

The strace and persistent eBPF comparison is limited to one 12-step probe.  The
paper states that both capture the same read and negative-lookup cases only in
that probe and makes no general tracer-equivalence claim.

## Prose and symbol hygiene

**PASS.** The revision removes the visible development-stage labels P0/P1,
diff-anchored words such as “now,” the Unicode numeric dash, and promotional
phrasing.  Long background taxonomies and repeated limitation lists were
collapsed.  The Abstract is a compact version of the Introduction and preserves
all sample counts and claim boundaries.

## LaTeX and visual QA

- `latexmk` completes with no undefined references or citations.
- Output is US Letter, twelve pages including references; the main text ends on
  page 11.
- Overview/design pages 4--6 and evaluation pages 7--10 were rendered to PNG
  and inspected after the structure update.
- No text, table, equation, caption, footer, or figure is clipped or overlapped.
- Only cosmetic underfull-box warnings remain; no overfull box is reported.

## Evidence still needed for submission

1. Storage, frequency control, and confidence intervals for the canonical
   repeated runtime comparison; CPU, vCPU, memory, kernel, commit, and command
   are already recorded in its manifest.
2. At least three repeats per GitHub-context task/policy, plus fair repeated
   Aider runs under the same model, timeout, prompt, and validator.
3. A WAL phase fault-injection matrix and a syscall/object-identity coverage
   matrix.
4. Shared-path concurrency and snapshot scaling by repository size, churn, and
   session length, or correspondingly narrower claims.
5. Artifact-native external baselines where reproducible, with a blocker table
   for the rest.

**Verdict:** the draft is consistent and compilable.  Its controlled semantic
claim and fixed-trajectory runtime distribution are supported.  Live-agent
generalization, crash statistics, and external comparisons remain the main OSDI
evidence gaps.
