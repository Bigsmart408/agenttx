# Optimization history: iteration 03

This is the pre-image for the deferred blob-GC optimization. It preserves the production implementation after persistent per-semisolate command-script reuse.

A controlled pre-change 64-call measurement recorded 325.434 ms/step without read tracing and 406.099 ms/step with full tracing (two repeats); full causal recovery passed and the no-trace ablation remained intentionally incorrect.
