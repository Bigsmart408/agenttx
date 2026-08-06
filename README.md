# AgentTX

Transactional side-effect control for multi-step agentic workflows.

## Problem

Semisolates (`try`) capture filesystem effects of a *single* opaque command.
Agents issue *multi-step, cross-tool* trajectories whose side effects have
causal dependencies. AgentTX elevates effect capture into **effect
transactions**: shared/incremental semisolates, a causal effect ledger,
speculative execution, and cascade rollback / selective commit.

## Positioning vs `try`

| | `try` | AgentTX |
|---|---|---|
| Unit | one command | agent trajectory |
| Visibility | single overlay | stacked / frontiered |
| Commit | all / none / path filter | causal selective commit |
| Rollback | discard upperdir | cascade along ledger edges |

## Repo layout

- `docs/` — design notes, threat/correctness invariants
- `src/agenttx/` — runtime (ledger, semisolate pool, tool interceptor)
- `experiments/` — baselines and workloads
- `scripts/` — setup / repro
- `third_party/` — vendored or cloned deps (e.g. binpash/try)

## Status

Working v0 prototype for OSDI-oriented systems work (Problem A), including a causal ledger, shared semisolate, surgical temporal rollback, and path-selective commit.

See [`docs/STATUS.md`](docs/STATUS.md) for completed work, evidence artifacts, and remaining gaps.

