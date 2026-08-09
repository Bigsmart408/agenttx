# Lower hard-link alias probe

| observation | value |
|---|---|
| same inode before transaction | True |
| expected alias read after writing sibling | `new` |
| observed alias read in overlay | `old` |
| reader dependency parents | `[]` |
| same inode after selective commit | False |
| first / alias content after commit | `new` / `old` |
| matches POSIX hard-link semantics | False |

The lower hard link is split by OverlayFS copy-up. Adding an inode-based ledger edge alone would not repair the data visibility or link-topology divergence.
