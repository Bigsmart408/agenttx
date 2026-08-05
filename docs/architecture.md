# Architecture (v0 sketch)

```
Agent tool loop
    |
    v
Tool Interceptor --> Effect Ledger (DAG)
    |                      |
    v                      v
Semisolate Pool      Commit / Rollback
(shared overlay)     (frontier + cascade)
    |
    v
Host filesystem (only after commit)
```

## Baselines

- Bare
- Session-`try`
- Per-call-`try`
- Full container isolation
