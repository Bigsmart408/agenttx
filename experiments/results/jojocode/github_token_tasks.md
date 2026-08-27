# GitHub-context multi-scale token recovery

The repositories are pinned public snapshots; task code lives under separate recovery directories and the benchmark charges only post-policy autonomous recovery tokens.

| task | mode | repeats | total mean | p50 | p95 | causal-minus-mode | success |
|---|---|---:|---:|---:|---:|---:|---:|
| short_requests_timeout | causal | 3 | 18767.0 | 18061.0 | 20605.3 | 0 | 1.00 |
| short_requests_timeout | temporal_checkpoint | 3 | 17267.0 | 17063.0 | 18129.5 | 85.0 | 0.33 |
| short_requests_timeout | whole_branch_abort | 3 | 21182.667 | 20659.0 | 26713.3 | -3830.667 | 1.00 |
| medium_flask_config | causal | 3 | 22551.333 | 25732.0 | 27774.1 | 0 | 1.00 |
| medium_flask_config | temporal_checkpoint | 3 | 13521.0 | 13854.0 | 14141.1 | 14480.0 | 1.00 |
| medium_flask_config | whole_branch_abort | 3 | 26515.0 | 19015.0 | 44908.9 | 1486.0 | 0.67 |
| long_pytest_plugin_selection | causal | 3 | 22643.333 | 15941.0 | 36980.3 | 0 | 1.00 |
| long_pytest_plugin_selection | temporal_checkpoint | 3 | 32487.667 | 26212.0 | 44570.2 | -16546.667 | 1.00 |
| long_pytest_plugin_selection | whole_branch_abort | 3 | 39778.333 | 35506.0 | 63326.8 | -23837.333 | 0.67 |

`causal-minus-mode` is paired savings relative to the causal policy; positive values mean the coarse policy used more tokens.
The token count includes post-policy diagnosis, tool schemas/results, validation, and regenerated artifacts; pre-failure tokens are excluded.
