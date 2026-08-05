# Problem A — Agent Effect Transactions

**One-liner.** Single-command semisolation cannot express causal side-effect
dependencies across a multi-step agent trajectory; we lack a first-class
effect transaction with speculation, rollback, and selective commit.

## Contributions (planned)

1. **AET abstraction** — effect ledger with R/W / negative deps; isolation levels; commit frontier semantics.
2. **Runtime** — shared/incremental semisolate pool; tool-boundary interception; cascade rollback.
3. **Evidence** — coding/ops agent workloads; zero unapproved dangerous host writes; utility and perf vs bare / session-try / per-call-try.

## Non-goals (v0)

- Full adversarial mediation (see Problem B later)
- Non-filesystem effects (network/cloud) beyond coarse disable
