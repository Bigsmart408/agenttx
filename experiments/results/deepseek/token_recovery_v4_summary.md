# DeepSeek v4 Flash replay-token summary

All rows use `deepseek-v4-flash`, `strace`, and one repeat per cell.  Values are
post-recovery replay tokens only; causal has no replay.

| lines/doc | causal | temporal checkpoint | whole branch abort |
|---:|---:|---:|---:|
| 12 | 0 | 898 | 2,055 |
| 24 | 0 | 997 | 2,381 |
| 48 | 0 | 1,483 | 3,300 |

All nine cells passed their targeted tests, selected the expected policy targets,
and reported no host pollution before commit.
