# Autonomous recovery token comparison

Actual API usage from the complete post-policy LLM recovery loop. Unlike Step 24, this includes diagnosis, tool schemas/results, validation, planning, and regenerated content.

| lines/doc | mode | repeats | success | regenerated docs | prompt mean | completion mean | total mean | AgentTX total saved | saved (%) | recovery p95 (s) |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 12 | causal | 2 | 1.000 | 0.00 | 26481.0 | 1961.0 | 28442.0 | 0.0 | 0.0% | 24.337 |
| 12 | temporal_checkpoint | 2 | 0.000 | 1.00 | 68202.5 | 3255.5 | 71458.0 | 43016.0 | 60.2% | 41.095 |
| 12 | whole_branch_abort | 2 | 0.000 | 2.00 | 100678.5 | 5451.5 | 106130.0 | 77688.0 | 73.2% | 64.786 |
| 24 | causal | 2 | 1.000 | 0.00 | 41604.5 | 2782.5 | 44387.0 | 0.0 | 0.0% | 31.039 |
| 24 | temporal_checkpoint | 2 | 0.000 | 1.00 | 37431.5 | 2127.0 | 39558.5 | -4828.5 | -12.2% | 22.740 |
| 24 | whole_branch_abort | 2 | 0.000 | 2.00 | 96926.5 | 5081.0 | 102007.5 | 57620.5 | 56.5% | 57.639 |
| 48 | causal | 2 | 1.000 | 0.00 | 58581.0 | 2925.0 | 61506.0 | 0.0 | 0.0% | 34.870 |
| 48 | temporal_checkpoint | 2 | 0.000 | 1.50 | 52544.5 | 3966.0 | 56510.5 | -4995.5 | -8.8% | 37.013 |
| 48 | whole_branch_abort | 2 | 0.000 | 2.00 | 101801.0 | 5736.5 | 107537.5 | 46031.5 | 42.8% | 50.957 |

Token saving is the coarse policy's full recovery-loop usage minus AgentTX causal recovery usage. Pre-failure tokens remain sunk cost and are not relabeled as saved.
