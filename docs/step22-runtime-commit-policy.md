# Step 22: Runtime-enforced commit policy

## Problem

AgentTX previously enforced `CommitPolicy` in the coding harness and LLM agent,
but `AgentTX.commit()` itself did not check policy. The standalone CLI loads the
runtime directly, so `agenttx commit` could materialize a path such as `*.pem`
that the harness would reject. This made a safety property depend on which API
entry point the caller happened to use.

## Change

Commit policy is now owned by `AgentTX` and checked inside `commit_frontier`
before path planning, WAL preparation, or host materialization. All callers --
direct runtime users, the CLI, coding harnesses, and LLM agents -- therefore
cross the same fail-closed check.

`AgentTX.begin(..., commit_policy=...)` accepts a custom policy. Its allow and
deny globs are persisted in `agenttx.json`; `AgentTX.load()` reconstructs the
same policy by default, so a stricter session cannot silently become less
restricted after process restart or CLI handoff. Older session metadata without
a policy receives the standard default policy.

The coding harness passes its configured policy into the runtime and no longer
duplicates the check in `run_trajectory`. This leaves one authoritative commit
invariant while retaining the existing policy object for preview and explicit
agent-side validation.

### External helper files

Some black-box agents create helper scripts in `/tmp` or another directory outside
the protected workspace. These writes are still observed in the effect ledger, but
they are not a Linux permission failure. The default policy rejects them at
commit time so an untrusted command cannot publish arbitrary host paths. For a
controlled benchmark or deployment that intentionally grants the agent this
capability, set `AGENTTX_ALLOW_EXTERNAL_WRITES=1` or pass
`--allow-external-writes` to `bench_official_tasks.py`. This explicitly permits
external writes except paths in `DEFAULT_DENY` (system directories, credentials,
and key material); the hard deny list always wins. The setting is persisted in
`agenttx.json` so a resumed session keeps the same decision.


## Validation

New integration coverage verifies that:

1. a direct runtime write to `private.pem` remains speculative and raises
   `PermissionError` at commit;
2. an independent `python -m agenttx commit` process cannot bypass the same
   default deny rule;
3. a custom `*.txt` deny rule survives session close/load and still blocks
   commit;
4. a blocked commit leaves the host clean, the frontier unchanged, and the
   ledger step speculative.

The full suite passes after the change. This closes the entry-point bypass, but
does not expand the policy language beyond path globs; progressive permissions,
capability classes, and human approval remain under G8.
