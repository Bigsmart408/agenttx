# GitHub-context multi-scale token recovery

The repositories are pinned public snapshots; task code lives under separate recovery directories and the benchmark charges only post-policy autonomous recovery tokens.

| task | mode | repeats | total mean | p50 | p95 | causal-minus-mode | success |
|---|---|---:|---:|---:|---:|---:|---:|
| short_requests_timeout | causal | 1 | 47977.0 | 47977.0 | 47977.0 | 0 | 1.00 |
| short_requests_timeout | temporal_checkpoint | 1 | 28209.0 | 28209.0 | 28209.0 | 19768.0 | 1.00 |
| short_requests_timeout | whole_branch_abort | 1 | 100939.0 | 100939.0 | 100939.0 | -52962.0 | 0.00 |
| medium_flask_config | causal | 1 | 41393.0 | 41393.0 | 41393.0 | 0 | 1.00 |
| medium_flask_config | temporal_checkpoint | 1 | 61313.0 | 61313.0 | 61313.0 | -19920.0 | 0.00 |
| medium_flask_config | whole_branch_abort | 1 | 68449.0 | 68449.0 | 68449.0 | -27056.0 | 0.00 |
| long_pytest_plugin_selection | causal | 1 | 41183.0 | 41183.0 | 41183.0 | 0 | 1.00 |
| long_pytest_plugin_selection | temporal_checkpoint | 1 | 69643.0 | 69643.0 | 69643.0 | -28460.0 | 0.00 |
| long_pytest_plugin_selection | whole_branch_abort | 1 | 65505.0 | 65505.0 | 65505.0 | -24322.0 | 0.00 |

`causal-minus-mode` is paired savings relative to the causal policy; positive values mean the coarse policy used more tokens.
The token count includes post-policy diagnosis, tool schemas/results, validation, and regenerated artifacts; pre-failure tokens are excluded.
