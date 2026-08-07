# Optimization history: iteration 06

This is the pre-image for incremental upperdir snapshots. It preserves the production implementation with a persistent try worker and framed command IPC.

A controlled pre-change 64-call measurement recorded 66.975 ms/step without read tracing and 151.531 ms/step with full tracing (two repeats); full causal recovery and the evidence suite passed.

The following implementation clones `before_{step-1}` and hard-links unchanged
regular files/whiteouts, then replays only the current step's write/delete paths
from the upperdir. A stage-level profile reduced cumulative snapshot time from
0.384 s to 0.158 s over 63 incremental calls (about 59%). The paired endpoint
measurement was noisy (162.104 ms/step after, standard deviation 18.839), so
this iteration makes no end-to-end speedup claim.
