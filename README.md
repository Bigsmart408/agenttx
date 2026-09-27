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

Working v0 prototype for OSDI-oriented systems work (Problem A), including automatic read/negative-lookup dependencies, a shared semisolate, durable session resume, whiteout-safe rollback snapshots, explicit causal rollback, metadata-aware filesystem effects, crash-recoverable commits, historical same-path frontier commits, content-addressed snapshots, hierarchical causal dependencies, symlink-alias tracking, path-selective commit, a reproducible baseline comparison matrix, and a parameterized 64-call coding-agent workload with failing-CI recovery.

See [`docs/STATUS.md`](docs/STATUS.md) for completed work, evidence artifacts, and remaining gaps.

## Runtime requirement

Automatic workspace dependency tracing is enabled by default and requires
either Linux strace or a working eBPF tracer (root + bpftrace). The CLI
fails closed when neither is available; the backend is selectable with
`agenttx begin --trace-backend {auto,strace,bpf}` — `auto` prefers eBPF when
the host can attach BPF programs and falls back to strace. Experiments that
intentionally measure the untraced mode can start a session with
agenttx begin --no-trace-reads.

## Recovery from a failure signal

Register a trusted check before the agent works. The recovery caller then
reports only that the current state is suspect; it does not supply a fault
step or path:

```python
tx.register_verifier(["python", "-m", "pytest", "-q"], name="tests")
outcome = tx.recover_from_failure_signal(verifier_name="tests")
```

The runtime records the check's file dependencies, probes causal rollback
candidates in isolated branches, and applies a plan only if the same check
passes there. `outcome.status` is `recovered`, `not_needed`, `inconclusive`,
or `verification_failed` (the final live-state recheck failed). The registered
check and its last passing step survive session reloads. Without a trusted
verifier, a crash signal alone is insufficient to identify a faulty step.
