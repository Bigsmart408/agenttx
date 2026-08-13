# Autonomous recovery token comparison

Actual API usage from the complete post-policy LLM recovery loop. Unlike Step 24, this includes diagnosis, tool schemas/results, validation, planning, and regenerated content.

| lines/doc | mode | repeats | success | regenerated docs | prompt mean | completion mean | total mean | AgentTX total saved | saved (%) | recovery p95 (s) |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 12 | causal | 1 | 1.000 | 0.00 | 30635.0 | 1847.0 | 32482.0 | 0.0 | 0.0% | 16.988 |
| 12 | temporal_checkpoint | 1 | 0.000 | 1.00 | 45036.0 | 2306.0 | 47342.0 | 14860.0 | 31.4% | 19.256 |
| 12 | whole_branch_abort | 1 | 0.000 | 2.00 | 42300.0 | 3909.0 | 46209.0 | 13727.0 | 29.7% | 31.137 |

Token saving is the coarse policy's full recovery-loop usage minus AgentTX causal recovery usage. Pre-failure tokens remain sunk cost and are not relabeled as saved.
