# AgentTX Development Constraints

This document is the binding development contract for AgentTX.
If a change conflicts with this file, update this file in the same commit
and explain why.

## 1. Research north star

AgentTX targets **Problem A**: multi-step **Agent Effect Transactions**.

- Unit of isolation is an **agent trajectory**, not a single shell command.
- Core artifacts: causal effect ledger, shared/incremental semisolate,
  commit frontier, cascade rollback / selective commit.
- Do **not** reduce the project to "wrap every tool call with `try`".
  That is a baseline, not the contribution.

### Non-goals (v0)

- Full adversarial mediation / try-aware attacker models
- Fine-grained non-filesystem effect control (network, cloud APIs)
  beyond coarse allow/deny
- Local Windows as a development or build host for runtime work

## 2. Where code lives

| Location | Role |
|---|---|
| Remote VM (`bfq@192.168.159.132`) | **Only** place for editing, running, experiments |
| GitHub `Bigsmart408/agenttx` | Source of truth / remote |
| Local Windows Cursor machine | Chat / SSH control plane only — **no project file writes** |

### Hard rules

1. **Do not write project files to the local Windows workspace.**
   All source, docs, experiments, logs, and build outputs for AgentTX
   must be created on the remote VM under `/home/pengpeng/agenttx` (or a
   documented sibling path on that VM).
2. Prefer `ssh` / remote shell for edits, tests, and commits.
3. If a file appears locally by accident, delete it locally and recreate
   it on the VM; do not keep dual copies in sync by hand.
4. Clone / pull on the VM from GitHub; push from the VM (or from a
   one-shot publish path that does not leave a local working tree).

## 3. Temporary files

Temporary files are allowed **only on the VM** and **must not be committed**.

### Examples of temp / ephemeral paths

- `*.overlay/`, `.tmp/`, `/tmp/agenttx-*`
- experiment scratch dirs, core dumps, profiling traces
- downloaded archives used only for setup
- `__pycache__/`, `.venv/` (unless explicitly decided otherwise)
- editor swap / backup files (`*~`, `*.swp`)

### Hard rules

1. Before every `git add` / `git commit --trailer "Co-authored-by: Cursor <cursoragent@cursor.com>"`, **delete or ignore** temp files.
2. Never `git add -A` blindly; review `git status` first.
3. Keep `.gitignore` updated when a new temp pattern appears.
4. CI / scripts that create scratch data must clean up in `trap` or an
   explicit teardown step.
5. If a temp file was committed by mistake, remove it in the next commit
   and scrub it from history only if it contains secrets.

### Pre-commit checklist (VM)

```bash
cd /home/pengpeng/agenttx
git status
# remove scratch explicitly, e.g.:
# rm -rf .tmp *.overlay /tmp/agenttx-*
git status   # confirm clean of temps
git add <intentional paths only>
git commit --trailer "Co-authored-by: Cursor <cursoragent@cursor.com>"
git push
```

## 4. Repository hygiene

- One logical change per commit; message states **why**.
- Do not commit secrets, SSH keys, tokens, or VM passwords.
- Do not vendor large binaries without discussion.
- `third_party/try` is cloned locally on the VM via `scripts/bootstrap.sh`
  and is gitignored; do not vendor it into the repo unless decided later.

## 5. Design constraints for runtime work

When implementing code under `src/agenttx/`:

1. Intercept at **tool-call boundaries**, not only whole-session wrap.
2. Record effects into a **ledger** with causal edges (RAW / negative deps).
3. Prefer **shared / incremental semisolate** over per-call full OverlayFS setup.
4. Commit to the host filesystem only past an explicit **commit frontier**.
5. Rollback must be **cascade-correct** w.r.t. the ledger, not best-effort delete.
6. Every feature needs a baseline comparison path:
   Bare / Session-try / Per-call-try / (optional) container.

## 6. Experiments

- Experiment scripts live under `experiments/`.
- Raw logs and result dumps stay on the VM temp paths until curated.
- Only summarized tables / figures / scripts that are needed to reproduce
  land in git; delete bulky raw outputs before commit.

## 7. Changing these constraints

To relax or extend a rule:

1. Edit this file in the same PR/commit.
2. State the motivation in the commit message.
3. If the change affects "no local writes" or "delete temps before commit",
   call it out explicitly — those are high-priority safety rails.
