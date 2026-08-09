# Step 21: Real-agent causal recovery

## Question

Steps 15 and 20 verified causal recovery with deterministic drivers, while the
existing real-agent robustness task verified isolation and final commit. The
missing end-to-end question was whether an LLM agent could inspect an AgentTX
ledger, identify the faulty producer, request the distinguishing recovery
operation, and finish the task without losing useful later work.

## Agent control plane

`LLMToolAgent` now exposes two recovery tools:

- `inspect_ledger`: returns step ids, tool names, status, dependency parents,
  effects, and the committed frontier;
- `rollback_causal(step_id)`: rolls back the selected step and its transitive
  descendants, then returns both the rollback targets and surviving active
  steps.

These are trusted transaction-control operations rather than filesystem tool
calls, so inspection does not create a spurious ledger step. The agent system
prompt recommends inspecting the ledger and preferring causal recovery when a
failed action has independent later work.

## Seeded recovery task

Each repeat starts from a fresh, passing Python package. Before the model is
invoked, the benchmark uses the normal AgentTX harness to create one protected
session with:

1. a faulty overwrite of `src/pipeline.py` (the rollback root);
2. an independent release note written later;
3. a derived artifact that reads the faulty module;
4. a failing pytest step that also reads the faulty module.

The benchmark verifies the injected dependency edges before contacting the
model. DeepSeek then receives the failing workspace and must inspect the
ledger, choose the earliest faulty producer, invoke causal rollback exactly
once, preserve the independent note, remove the invalid derived artifact, run
passing tests, write a short recovery note, and finish without committing.
The benchmark performs the final policy check and commit after confirming the
physical host remained unchanged.

## Metrics and success condition

A repeat succeeds only if all of the following hold:

- the model inspected the ledger before recovery;
- it selected the injected faulty root and called causal rollback once;
- the rollback target set contains the root, derived artifact, and failing test,
  but excludes the independent note;
- the host is unchanged before the controlled commit;
- the original correct module is restored;
- the independent note survives and the derived artifact is absent;
- the agent writes its recovery note and host-side pytest passes after commit.

## Result

Three fresh-workspace `deepseek-chat` repeats all succeeded. Faulty-root
selection, correct causal-target selection, independent-work retention,
invalid-derived removal, and post-commit test pass rates were all 100%; the
pre-commit host leak rate was 0%. End-to-end wall time was 29.0 seconds p50 and
30.8 seconds p95. The model used 17--22 tool calls and inspected the ledger one
or two times before choosing step 0 in every repeat.

This closes the narrow “LLM cannot request causal recovery” gap. It does not yet
establish behavior on multi-package repositories, adversarial prompts, or long
open-ended CI repair loops.

## Reproduce

```bash
cd /home/bfq/agenttx
export PYTHONPATH=src:.
/home/bfq/miniconda3/envs/agenttx/bin/python \
  experiments/scripts/bench_real_agent_recovery.py --repeats 3 --max-turns 30
```

The script reads model credentials from `~/.agenttx_llm.env` or the process
environment without serializing them. It writes:

- `experiments/results/real_agent_recovery.csv`;
- `experiments/results/real_agent_recovery.json`;
- `experiments/results/real_agent_recovery.md`.
