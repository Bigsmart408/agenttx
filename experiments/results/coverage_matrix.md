# Syscall / object-identity coverage matrix

Repeats per case: 3. Supported cases expect correctness; unsupported cases expect fail-closed.

| case | expected | pass rate | status | detail |
|---|---|---:|---|---|
| ordinary_path | correct | 1.00 | measured | `EffectKind.WRITE:/tmp/agenttx-cov-iv9pk0fs/ws/data.txt EffectKind.READ:/tmp/agen` |
| openat_at_fdcwd | correct | 1.00 | measured | `EffectKind.WRITE:/tmp/agenttx-cov-wyi03wvv/ws/x.txt EffectKind.READ:/tmp/agenttx` |
| negative_lookup | correct | 1.00 | measured | `EffectKind.NEGATIVE:/tmp/agenttx-cov-4qx000s2/ws/missing.txt` |
| symlink_alias | correct | 1.00 | measured | `aborted=[0, 1]; EffectKind.WRITE:/tmp/agenttx-cov-noht24tl/ws/real EffectKind.WR` |
| rename_delete_create | correct | 1.00 | measured | `rename materialized` |
| preexisting_hardlink | correct | 1.00 | measured | `same_inode=True` |
| upper_created_hardlink | correct | 1.00 | measured | `same_inode=True` |
| external_alias | fail_closed | 1.00 | measured | `HardlinkTopologyError: cannot prove complete hard-link group for /tmp/agenttx-co` |
| bind_mount | fail_closed_or_unavailable | 1.00 | unavailable | `unavailable: mount: only root can use "--bind" option
` |
| fd_relative_dirfd | correct_or_fail_closed | 1.00 | measured | `EffectKind.READ:/tmp/agenttx-cov-ibdrqfuv/ws/subdir EffectKind.READ:/tmp/agenttx` |

