# Real-agent replay-token cost after recovery

Actual API usage from controlled `deepseek` `write_file` replay calls. Common deterministic validation and AgentTX runtime work are excluded, so the metric isolates LLM work that must be regenerated only because a recovery policy discarded an otherwise valid artifact.

`temporal_checkpoint` is an optimistic immediate pre-fault checkpoint policy; `whole_branch_abort` represents coarse leaf/session abort. These are native recovery-granularity emulations, not executions of external artifacts.

| lines/doc | mode | repeats | success | regenerated docs | prompt mean | completion mean | total mean | AgentTX total saved | saved (%) |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 12 | causal | 3 | 1.000 | 0.00 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0% |
| 12 | temporal_checkpoint | 3 | 1.000 | 1.00 | 491.0 | 381.3 | 872.3 | 872.3 | 100.0% |
| 12 | whole_branch_abort | 3 | 1.000 | 2.00 | 982.0 | 758.7 | 1740.7 | 1740.7 | 100.0% |
| 24 | causal | 3 | 1.000 | 0.00 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0% |
| 24 | temporal_checkpoint | 3 | 1.000 | 1.00 | 491.0 | 604.3 | 1095.3 | 1095.3 | 100.0% |
| 24 | whole_branch_abort | 3 | 1.000 | 2.00 | 982.0 | 1224.3 | 2206.3 | 2206.3 | 100.0% |
| 48 | causal | 3 | 1.000 | 0.00 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0% |
| 48 | temporal_checkpoint | 3 | 1.000 | 1.00 | 491.0 | 958.0 | 1449.0 | 1449.0 | 100.0% |
| 48 | whole_branch_abort | 3 | 1.000 | 2.00 | 982.0 | 2278.7 | 3260.7 | 3260.7 | 100.0% |

Token saving means avoided post-recovery replay tokens. Tokens already spent before failure, common validation work, and runtime latency are outside this metric and must be reported separately.
