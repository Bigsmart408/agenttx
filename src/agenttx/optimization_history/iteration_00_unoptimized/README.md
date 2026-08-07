# Optimization history: iteration 00

This directory is an exact source snapshot of the unoptimized hot path before the first performance change. It is intentionally not imported by the AgentTX package.

Captured from commit `d55d6e8` before the known-tool tracing bypass. The baseline is the fresh Step 17 long-workload scaling point at 64 tool calls: Bare 52.663 ms/step, AgentTX without read tracing 361.008 ms/step, and AgentTX full 495.843 ms/step (two repeats).

The snapshot preserves the prior implementations of `AgentTX.run_tool`, `SharedSemisolate.run`, `LayerStore.snapshot_before`, and `CodingAgentHarness`. Future optimization rounds must add a new `iteration_NN_*` directory before editing production code, plus a manifest with the old/new benchmark and correctness results.
