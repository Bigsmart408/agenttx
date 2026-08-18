# AgentTX motivation: optimization chain

## Problem exposed by the baseline

Each opaque tool call paid for tracing, shell/script setup, snapshot traversal, and try namespace setup.
Long trajectories multiply per-call costs; optimization must reduce overhead without weakening causal recovery.

The historical rows below are directional before/after measurements from the same VM. Iteration 06 is a snapshot-stage metric and intentionally does not claim an end-to-end speedup.

| iter | optimization | metric | before | after | improvement | correct |
|---:|---|---|---:|---:|---:|:---:|
| 0 | known write/delete trace bypass | full_ms_per_step | 495.843 | 500.906 | -1.021% | True |
| 1 | known read_file explicit READ/NEGATIVE effects + trace bypass | full_ms_per_step | 500.906 | 437.534 | 12.651% | True |
| 2 | persistent per-semisolate command script reuse | full_ms_per_step | 418.899 | 409.835 | 2.164% | True |
| 3 | defer unreachable blob GC from per-snapshot hot path | full_ms_per_step | 406.099 | 397.104 | 2.215% | True |
| 4 | execute reusable command script directly via shebang | full_ms_per_step | 397.104 | 393.631 | 0.875% | True |
| 5 | persistent try worker with framed command IPC | full_ms_per_step | 393.631 | 151.531 | 61.504% | True |
| 6 | incremental upperdir snapshot with hard-linked unchanged entries | snapshot_stage_s | 0.384 | 0.158 | 58.854% | True |

## Interpretation

1. Trusted harness effects remove avoidable tracing work while preserving explicit READ/NEGATIVE dependencies.
2. Reusing the command script and deferring blob GC remove repeated temporary-file and maintenance work.
3. Direct script execution removes an extra shell parse.
4. The persistent try worker removes per-call namespace/overlay setup and is the largest recorded endpoint reduction.
5. Incremental snapshots reduce snapshot-stage traversal by replaying only changed upperdir paths; boundary operations retain a full-copy fallback.
6. Worker crash injection, reloadable long sessions, concurrent agents, and real-agent repeats validate that the speed path remains recoverable and isolated.

## Runtime tail and real-agent evidence

Deterministic p50/p95 and real-agent p50/p95 are recorded in the robustness bundles; network/model latency is kept separate from runtime-only measurements.

Latest real-agent model: `deepseek-v4-flash`, wall p50/p95 16.564075/18.465274 s, success rate 1.0.
