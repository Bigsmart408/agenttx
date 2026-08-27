# GitHub-context multi-scale token recovery

The repositories are pinned public snapshots; task code lives under separate recovery directories and the benchmark charges only post-policy autonomous recovery tokens.

| task | mode | repeats | total mean | p50 | p95 | causal-minus-mode | success |
|---|---|---:|---:|---:|---:|---:|---:|
| short_requests_timeout | causal | 3 | 0.0 | 0 | 0 | 0 | 0.00 |
| short_requests_timeout | temporal_checkpoint | 3 | 0.0 | 0 | 0 | 0 | 0.00 |
| short_requests_timeout | whole_branch_abort | 3 | 0.0 | 0 | 0 | 0 | 0.00 |
| medium_flask_config | causal | 3 | 0.0 | 0 | 0 | 0 | 0.00 |
| medium_flask_config | temporal_checkpoint | 3 | 0.0 | 0 | 0 | 0 | 0.00 |
| medium_flask_config | whole_branch_abort | 3 | 0.0 | 0 | 0 | 0 | 0.00 |
| long_pytest_plugin_selection | causal | 3 | 0.0 | 0 | 0 | 0 | 0.00 |
| long_pytest_plugin_selection | temporal_checkpoint | 3 | 0.0 | 0 | 0 | 0 | 0.00 |
| long_pytest_plugin_selection | whole_branch_abort | 3 | 0.0 | 0 | 0 | 0 | 0.00 |

`causal-minus-mode` is paired savings relative to the causal policy; positive values mean the coarse policy used more tokens.
The token count includes post-policy diagnosis, tool schemas/results, validation, and regenerated artifacts; pre-failure tokens are excluded.
