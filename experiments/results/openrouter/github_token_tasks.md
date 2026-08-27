# GitHub-context multi-scale token recovery

The repositories are pinned public snapshots; task code lives under separate recovery directories and the benchmark charges only post-policy autonomous recovery tokens.

| task | mode | repeats | total mean | p50 | p95 | causal-minus-mode | success |
|---|---|---:|---:|---:|---:|---:|---:|
| short_requests_timeout | causal | 11 | 30151.091 | 23467.0 | 60568.0 | 0 | 1.00 |
| short_requests_timeout | temporal_checkpoint | 11 | 57765.091 | 51769.0 | 117527.5 | -27281.091 | 0.73 |
| short_requests_timeout | whole_branch_abort | 11 | 91821.636 | 77139.0 | 215825.5 | -61337.636 | 0.45 |
| medium_flask_config | causal | 11 | 41689.0 | 29689.0 | 102904.0 | 0 | 1.00 |
| medium_flask_config | temporal_checkpoint | 11 | 65143.364 | 59801.0 | 124399.5 | 6812.636 | 0.82 |
| medium_flask_config | whole_branch_abort | 11 | 96963.0 | 80224.0 | 226711.5 | -25007.0 | 0.73 |
| long_pytest_plugin_selection | causal | 11 | 74456.636 | 54910.0 | 176432.5 | 0 | 0.91 |
| long_pytest_plugin_selection | temporal_checkpoint | 11 | 65185.455 | 50071.0 | 142043.0 | 2967.545 | 0.91 |
| long_pytest_plugin_selection | whole_branch_abort | 11 | 171679.545 | 91069.0 | 416278.5 | -103526.545 | 0.91 |

`causal-minus-mode` is paired savings relative to the causal policy; positive values mean the coarse policy used more tokens.
The token count includes post-policy diagnosis, tool schemas/results, validation, and regenerated artifacts; pre-failure tokens are excluded.
