# Optimization history: iteration 01

This directory preserves the production source after the first optimization and before the next one. Iteration 01 bypasses syscall tracing for harness tools whose writes are already captured by the overlay digest (`write_file`, `append_file`, and `delete_file`).

The single 64-call measurement after that change was 385.438 ms/step without tracing and 500.906 ms/step with full tracing; causal recovery remained correct for full AgentTX. Because this is one noisy VM run, the next iteration adds explicit effects for `read_file` before disabling its strace.
