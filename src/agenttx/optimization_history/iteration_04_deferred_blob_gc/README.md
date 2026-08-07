# Optimization history: iteration 04

This is the pre-image for the direct executable command-script optimization. It preserves the production implementation after deferring unreachable blob GC from each snapshot to rollback or retained-session cleanup.

A controlled pre-change 64-call measurement recorded 324.325 ms/step without read tracing and 397.104 ms/step with full tracing (two repeats); full causal recovery passed and the no-trace ablation remained intentionally incorrect.
