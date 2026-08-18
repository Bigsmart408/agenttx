# Lower hard-link alias probe

| observation | value |
|---|---|
| same inode before transaction | True |
| expected alias read after writing sibling | `new` |
| observed alias read in overlay | `new` |
| reader dependency parents | `[0]` |
| same inode after selective commit | True |
| first / alias content after commit | `new` / `new` |
| matches POSIX hard-link semantics | True |

With TRY_OVERLAY_INDEX=on, the active overlay exposes the updated inode through both names. The selective-commit path expands the complete host hard-link group and updates its inode in place; an incomplete group fails closed instead of silently splitting aliases.
