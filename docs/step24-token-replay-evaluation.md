# Step 24 — Token savings from retaining valid agent work

## Research question

AgentTX claims that causal rollback avoids discarding causally independent work.
The systems result is already measured by retained files and effect-DAG utility,
but the user-visible consequence was missing: **how many LLM tokens does retained
work avoid compared with checkpoint or whole-session recovery?**

This experiment measures post-recovery *replay tokens*: actual prompt and
completion tokens charged by DeepSeek only because a recovery policy discarded
an otherwise valid agent artifact. It does not relabel runtime latency or tokens
spent before the fault as savings.

## Controlled trajectory

Every sample creates the same five-step speculative trajectory:

| step | effect | role |
|---:|---|---|
| 0 | write `docs/design.md` | valid prefix work |
| 1 | install a faulty pipeline | rollback root |
| 2 | write `docs/changelog.md` | valid, later, independent work |
| 3 | render an artifact from the faulty pipeline | invalid descendant |
| 4 | run failing tests | invalid descendant |

The two documents contain 12, 24, or 48 distinct repository decisions. The
fault position and dependency graph remain fixed so that document size changes
replay cost without changing recovery semantics.

## Recovery policies and related-work mapping

| experiment mode | actual rollback target | retained valid documents | interpretation |
|---|---|---:|---|
| `causal` | `{1, 3, 4}` | 2 | AgentTX causal rollback |
| `temporal_checkpoint` | `{1, 2, 3, 4}` | 1 | optimistic checkpoint immediately before the fault |
| `whole_branch_abort` | `{0, 1, 2, 3, 4}` | 0 | discard the speculative leaf/session |

The latter two modes are **native recovery-granularity emulations**, not claims
that external artifacts were installed on this VM. The temporal policy is an
optimistic lower bound for time-travel/checkpoint systems such as
[Waypoint](https://daplab.cs.columbia.edu/_projects/waypoint/waypoint.html) and
[YoloFS](https://arxiv.org/abs/2604.13536): it assumes the best checkpoint is
available immediately before the faulty step. The whole-branch policy models
the replay consequence of coarse leaf/session abort, corresponding to the
branch-level commit/abort boundary described by
[BranchFS](https://arxiv.org/abs/2602.08199).

This mapping compares the consequence of recovery granularity under one common
workload. It is not an end-to-end performance comparison with those systems.

## Real-agent replay protocol

After applying each policy, the benchmark knows which valid documents were
discarded. For each missing document, `deepseek-v4-flash` receives the same
structured regeneration task and must issue a real `write_file` tool call. API
usage is read from the response's `usage` object. Generated content must contain
every numbered item in order; an invalid response may be retried up to three
times, and all failed-attempt tokens remain charged to that sample.

AgentTX invokes no replay call when both valid documents survive. Common
deterministic tests, filesystem commit work, and runtime latency are measured
but excluded from the replay-token numerator. This isolation is deliberate:
an exploratory open-ended repair loop showed that stochastic planning and
repeated inspection can dominate a small regeneration delta and even reverse a
single-run ordering. Open-loop end-to-end token cost therefore remains a
separate evaluation rather than being used as evidence for the retention claim.

## Results

Model: `deepseek-v4-flash`; three fresh samples per cell; 27 total samples.
All 27 recovered workspaces passed tests, all rollback target sets matched the
policy, and no sample modified the host workspace before commit. Every accepted
document required one model call in the final run.

| lines per document | policy | documents replayed | total tokens mean | p50 | p95 | tokens saved by AgentTX |
|---:|---|---:|---:|---:|---:|---:|
| 12 | AgentTX causal | 0 | 0.0 | 0.0 | 0.0 | — |
| 12 | optimistic checkpoint | 1 | 864.3 | 850.0 | 892.3 | 864.3 |
| 12 | whole branch/session abort | 2 | 1,797.3 | 1,825.0 | 1,846.6 | 1,797.3 |
| 24 | AgentTX causal | 0 | 0.0 | 0.0 | 0.0 | — |
| 24 | optimistic checkpoint | 1 | 1,060.3 | 1,033.0 | 1,113.1 | 1,060.3 |
| 24 | whole branch/session abort | 2 | 2,231.7 | 2,232.0 | 2,238.3 | 2,231.7 |
| 48 | AgentTX causal | 0 | 0.0 | 0.0 | 0.0 | — |
| 48 | optimistic checkpoint | 1 | 1,424.7 | 1,407.0 | 1,497.9 | 1,424.7 |
| 48 | whole branch/session abort | 2 | 3,340.3 | 3,084.0 | 4,009.2 | 3,340.3 |

On this controlled workload, AgentTX avoids 100% of *avoidable replay tokens*
because it retains both valid documents. The more useful paper number is the
absolute saving: 864–1,425 tokens versus the optimistic temporal policy and
1,797–3,340 tokens versus whole branch/session abort across the tested sizes.
The roughly increasing curve connects the systems property (retained work) to
an economic property (avoided model context and generation).

## What the result does not claim

- It does not claim that total recovery costs zero tokens. It reports only
  tokens caused by replaying discarded valid work.
- It does not recover tokens already spent before failure; those are sunk cost.
- It does not include a full autonomous planner's inspection/reasoning tokens.
- It does not execute Waypoint, YoloFS, or BranchFS artifacts. Their recovery
  granularity is emulated using AgentTX's real overlay state.
- It uses one model and synthetic document artifacts. A paper-ready extension
  should add real multi-package tasks, more models, independence ratios, and
  artifact-native external runs where environments permit.

## Validity fix discovered while building the experiment

The first pilot exposed a runtime isolation bug: upstream `try` derives its
chroot start directory from the shell's `$PWD`, while Python's `subprocess`
`cwd=` changes the kernel working directory without updating the inherited
environment variable. Temporary real-agent tasks could therefore start in the
benchmark repository. `SharedSemisolate` now synchronizes `PWD` for both the
one-shot and persistent-worker launch paths, and
`test_try_starts_in_workspace_when_inherited_pwd_is_stale` prevents regression.
All reported results were collected after this fix; pilot values were discarded.

## Reproduction and artifacts

```bash
cd /home/pengpeng/agenttx
source ~/.agenttx_llm.env
PYTHONPATH=src:. /home/pengpeng/miniconda3/envs/agenttx/bin/python \
  experiments/scripts/bench_token_recovery.py \
  --document-lines 12 24 48 --repeats 3
```

Artifacts:

- `experiments/results/token_recovery.csv` — aggregated mean/p50/p95
- `experiments/results/token_recovery_raw.csv` — per-sample token/tool data
- `experiments/results/token_recovery.json` — full machine-readable bundle
- `experiments/results/token_recovery.md` — compact generated table
