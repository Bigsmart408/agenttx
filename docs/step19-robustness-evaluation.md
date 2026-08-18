# Step 19 — robustness evaluation

The AgentTX prototype now includes one reproducible robustness bundle:

```bash
PYTHONPATH=src:. python3 experiments/scripts/bench_robustness.py \
  --tail-length 64 --tail-repeats 3 \
  --long-steps 256 --long-resume-at 128 \
  --agents 4 --concurrent-steps 16
```

The script writes `experiments/results/robustness.{csv,json,md}`.

## Tail latency

For each selected mode, the script measures end-to-end wall time around every
tool call, including AgentTX ledger/session persistence. It reports per-call and
per-run p50/p95 rather than only an arithmetic mean. The deterministic 64-call
coding workload is repeated independently; expected workload failures are
reported as a failure rate and are not silently removed from the sample.

## Worker crash injection

`SharedSemisolate.inject_worker_crash_once()` is an evaluation-only hook. It kills
the persistent worker before the next request is dispatched. `run()` must then
use the original one-shot `try` path, count the fallback, and start a fresh
persistent worker on the following call. The experiment verifies all three calls
complete and that the files materialize after commit.

## Long-running session

The long-session experiment performs 256 write calls, closes the session without
destroying it at the midpoint, reloads it with `AgentTX.load()`, executes the
remaining calls, and commits the final frontier. It checks that all 256 files are
materialized and reports step p50/p95 plus failures. This exercises worker
shutdown/restart, session metadata persistence, snapshot resume, and final commit.

## Concurrent agents

Four agents run in parallel using a `ThreadPoolExecutor`. Each agent has its own
workspace subdirectory and persistent overlay session, writes 16 files, and
commits independently. The result checks every agent's frontier, file count, and
contents, and reports cross-contamination separately from throughput.

These are VM-local engineering measurements. They provide tail-latency and
failure-isolation evidence; they are not yet a claim about production-scale
multi-tenant scheduling.

## Latest VM run

The checked-in result bundle was produced with the command above (three 64-call
tail-latency repeats, a 256-step session, and four 16-step concurrent agents):

| experiment | result |
|---|---|
| no-trace per-call p50 / p95 | 17.114 / 334.112 ms |
| full-trace per-call p50 / p95 | 22.761 / 743.230 ms |
| worker crash | fallback and restart passed; one fallback recorded |
| long session | 256/256 files committed; step p50/p95 36.286 / 72.312 ms |
| concurrent agents | 4/4 committed; cross-contamination false; wall 3.105 s |

The long-workload tail includes the intentionally missing architecture read and
injected CI failure; the reported failure rate is therefore an expected-workload
signal, not an infrastructure failure rate.

## Real LLM agent extension

The runtime benchmark can be extended with the actual OpenAI-compatible
`LLMToolAgent` using the project’s existing provider configuration:

```bash
source ~/.agenttx_llm.env
PYTHONPATH=src:. /home/pengpeng/miniconda3/envs/agenttx/bin/python \
  experiments/scripts/bench_real_agent.py --repeats 3 --max-turns 35
```

Each repeat gives the real agent a fresh seeded multi-file refactor repository.
The agent independently chooses tools through the OpenAI-compatible API; the
tools still pass through AgentTX. The benchmark validates that the host remains
unchanged before commit, commits the resulting ledger frontier, and runs the
tests after commit. It writes `real_agent_robustness.{csv,json,md}` without
serializing the API key or full conversation.

The latest three-repeat run used `deepseek-v4-flash`: wall p50/p95 were 16.564/18.465
seconds, tool-call p50/p95 were 15.0/16.8, finished rate was 1.0, success rate
was 1.0, tests passed in all repeats, and host leak rate before commit was 0.0.
These numbers include network/model latency and are not directly comparable to
the deterministic runtime-only p50/p95 measurements.
