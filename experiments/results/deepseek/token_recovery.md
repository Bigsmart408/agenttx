# DeepSeek v4 Flash replay-token summary

All rows use `deepseek-v4-flash`, `strace`, and three repeats per cell.  Values are
post-recovery replay tokens only; causal has no replay.

| lines/doc | causal | temporal checkpoint | whole branch abort |
|---:|---:|---:|---:|
| 12 | 0 | 864.3 | 1,797.3 |
| 24 | 0 | 1,060.3 | 2,231.7 |
| 48 | 0 | 1,424.7 | 3,340.3 |

All nine cells passed their targeted tests, selected the expected policy targets,
and reported no host pollution before commit.
