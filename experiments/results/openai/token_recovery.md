# Real-agent replay-token cost after recovery

Actual API usage from controlled DeepSeek `write_file` replay calls. Common deterministic validation and AgentTX runtime work are excluded, so the metric isolates LLM work that must be regenerated only because a recovery policy discarded an otherwise valid artifact.

`temporal_checkpoint` is an optimistic immediate pre-fault checkpoint policy; `whole_branch_abort` represents coarse leaf/session abort. These are native recovery-granularity emulations, not executions of external artifacts.

| lines/doc | mode | repeats | success | regenerated docs | prompt mean | completion mean | total mean | AgentTX total saved | saved (%) |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 12 | causal | 3 | 1.000 | 0.00 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0% |
| 12 | temporal_checkpoint | 3 | 0.000 | 0.00 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0% |
| 12 | whole_branch_abort | 3 | 0.000 | 0.00 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0% |

Token saving means avoided post-recovery replay tokens. Tokens already spent before failure, common validation work, and runtime latency are outside this metric and must be reported separately.
