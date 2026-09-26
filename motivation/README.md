# Intra-turn recovery motivation campaign

The canonical motivation campaign is the unified MB1--MB3 microbenchmark
specified in Section 2 of `agent.md`. Its only result root is:

```text
experiments/results/motivation/
```

Do not mix the canonical bundle with the historical top-level
`experiments/results/motivation_*.{csv,json,md}` files or with date-stamped
protocol directories.

## Current microbenchmark evidence and figures

| Phase | Role | Current source | Paper figure |
|---|---|---|---|
| MB1--MB2 | Fixed-closure task-length and chronology sweeps | `controlled_case/{controlled_recovery,controlled_position}_*.csv` | `FIG-Motivation-Coarse-Waste` |
| MB1--MB3 summary | Recovery completeness and collateral cost over all 400 paired manifests | controlled and microbenchmark raw CSVs | `FIG-Motivation-Suite` |
| MB3 | Controlled dependency-value sweep | `microbenchmark/motivation_recovery_raw.csv` | `FIG-Motivation-Dependency` |
| MB1--MB3 derived view | Coarse-boundary crossover over the same 400 paired manifests | controlled and microbenchmark raw CSVs | `FIG-Motivation-Boundary-Crossover` |
| Supporting analysis | One-factor sensitivity and robustness | `microbenchmark/sensitivity/*/` | `FIG-Motivation-Sensitivity` (supporting) |

These are the figures currently generated from the unified microbenchmark; the
crossover view introduces no additional cases. This is not a limit on the
number of figures allowed in the Motivation section. A
supporting figure may move into the main text when it establishes a distinct
claim; figure placement is not constrained to two figures.

Generate every currently valid figure with:

```bash
PYTHONPATH=src:. python3 motivation/plot_motivation_four.py
PYTHONPATH=src:. python3 motivation/plot_dependency_sensitivity.py
```

Regenerate the MB1--MB2 data with:

```bash
PYTHONPATH=src:. python3 experiments/scripts/bench_controlled_motivation.py \
  --output-dir experiments/results/motivation/controlled_case
```

The scripts validate schemas, policy manifests, paired-cell cardinalities, and
the absence of `agenttx_causal` before writing PDF and PNG files to both
`motivation/` and `paper/img/`. All figures use the paper's compact line-plot
style. MB3 includes all 21 preregistered dependency values and 10 paired cases
per value.
Canonical strategy identifiers and figure labels are defined in
`experiments/motivation_strategies.py`. All six policies are retained and
plotted. `Turn checkpoint` removes complete turns containing invalid effects,
`Temporal step` removes the global suffix starting at the fault, and `Whole
branch` removes the entire multi-turn branch. The boundaries may coincide for
a one-turn case, but separate as task length and dependency placement vary.

## Excluded M3 sources

M3 is a real-model replay experiment for the later system evaluation, not part
of the canonical motivation figures. The retained implementation uses the
multi-turn protocol in `m3/multiturn/`:
eight turns, 128 opaque operations, multiple faults across turns, audited
hierarchical diagnosis, and equal-cost policy prompts. A one-cell mechanism
smoke for Codex and native SWEAgent covers no-rollback, turn-checkpoint,
temporal-suffix, whole-branch, and AgentTX. Codex temporal is a fixed-budget
timeout; the other cells pass. Clean/ideal controls and the five-repeat
seven-policy campaign have not completed, so these are not final paper results.

The historical M3 figure combines legacy v8 `crash_no_rollback` measurements with
v7 clean, turn-checkpoint, temporal, whole-branch, and ideal-oracle measurements.
The model, reasoning effort, opaque artifact contract, and strict
one-artifact-per-write replay audit are fixed across the retained sources.
Coarse policies receive only the shared crash notice, AgentTX receives the
output of its own workspace localizer, and only ideal-oracle receives the
injected fault root. It is retained for historical audit and must not be used as
the current multi-turn result.

The plot does not compare unmatched campaign totals. For each backend it takes
the intersection of `(trace_id, repeat)` cells present for every displayed
policy: 30 cells for Codex and 25 for native SWEAgent. Failed attempts remain
charged in `tokens per successful cell`. Original configs, raw rows, summaries,
and reports are retained under `m3/{codex,sweagent}/` and `m3/legacy_v7/` so the
mixed-source provenance is explicit and auditable.

## Claim boundaries

- MB1 contributes 100 paired cases that grow the branch from one to ten turns
  at 16 operations per turn. MB2 adds 90 paired cases that move the fault from
  10% to 90% in a ten-turn branch. Both keep the invalid closure fixed at four
  operations. MB3 adds 210 dependency-value cases. Together, these same 400
  manifests establish recovery completeness and collateral loss.
- MB1--MB3 measure controlled recovery boundaries. Their token weights are
  deterministic work attribution, not billed model usage.
- The fixed 128-operation extreme case is retained only as an appendix
  regression check. Main-text aggregate claims use all 400 paired MB1--MB3
  manifests rather than this single endpoint.
- Native-turn counterfactuals are retained for audit but belong in Real-world
  Workload or Evaluation, not in the unified Motivation microbenchmark.
- The legacy M3 figure supports only its displayed within-backend mixed-source
  comparison. The new extreme-case table uses a shared protocol across
  backends, but absolute Codex-versus-SWEAgent token ranking is still not a
  recovery-policy claim because their agent loops differ.
- The older 20-trace/638-turn native audit remains under `intra_turn/` as
  supporting evidence. Its selected subset is not an AgentTX applicability
  estimate and is not Figure 1.
- `turn_checkpoint_proxy` is a recovery-boundary proxy, not a measurement of
  the Crab artifact.

Detailed phase definitions, policy semantics, metrics, and fairness constraints
are authoritative in `agent.md`. `plot_intra_turn_evidence.py` and its notebook
are retained only for historical audit; they are not the canonical figure
entry point. Exact result summaries are in the reports below each canonical
result directory.
