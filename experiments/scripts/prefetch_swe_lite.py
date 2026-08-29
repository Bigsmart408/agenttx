#!/usr/bin/env python3
"""Prefetch SWE-Bench Lite git data onto this machine.

Downloads each unique GitHub repo once (via agentTX-clash), then creates
per-instance working copies with `git clone --local` hardlinks.
Never overwrites an instance tree that already exists.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path("/home/pengpeng/agenttx")
CACHE = ROOT / "experiments" / "cache" / "swe_bench"
MANIFEST = CACHE / "lite_test_manifest.json"
UPSTREAM = CACHE / "upstream"
REPOS = CACHE / "repos"
CLASH = Path("/home/pengpeng/.local/bin/agentTX-clash")
GIT_GITHUB = "https://github.com/{repo}.git"


def log(msg: str) -> None:
    print(msg, flush=True)


def run(cmd, cwd=None, check=True, capture=False):
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=check,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )


def clash_git(*git_args: str, cwd=None):
    cmd = [str(CLASH), "run", "--", "git", *git_args]
    log("+ " + " ".join(cmd if len(cmd) < 12 else cmd[:8] + ["..."]))
    return run(cmd, cwd=cwd)


def materialize_json(rows: list[dict]) -> int:
    written = 0
    for row in rows:
        path = CACHE / f"{row['instance_id']}.json"
        if path.exists():
            continue
        path.write_text(json.dumps(row, indent=2) + "\n", encoding="utf-8")
        written += 1
    return written


def unique_repos(rows: list[dict]) -> list[str]:
    seen = []
    for row in rows:
        repo = row["repo"]
        if repo not in seen:
            seen.append(repo)
    return seen


def bare_path(repo: str) -> Path:
    return UPSTREAM / (repo.replace("/", "__") + ".git")


def ensure_bare(repo: str) -> Path:
    dest = bare_path(repo)
    marker = dest / "HEAD"
    if marker.exists():
        log(f"skip bare {repo} (exists)")
        return dest
    partial = dest.with_name(dest.name + ".partial")
    if partial.exists():
        shutil.rmtree(partial)
    UPSTREAM.mkdir(parents=True, exist_ok=True)
    url = GIT_GITHUB.format(repo=repo)
    log(f"clone bare {repo} <- {url}")
    t0 = time.time()
    clash_git("clone", "--bare", url, str(partial))
    # Make sure we can resolve objects; unshallow is a no-op on full clones.
    run(["git", "--git-dir", str(partial), "rev-parse", "--is-bare-repository"], check=True, capture=True)
    partial.replace(dest)
    log(f"ok bare {repo} in {time.time() - t0:.0f}s  size={du(dest)}")
    return dest


def du(path: Path) -> str:
    proc = run(["du", "-sh", str(path)], capture=True, check=False)
    return (proc.stdout or "").split()[0] if proc.returncode == 0 else "?"


def commit_in_bare(bare: Path, commit: str) -> bool:
    proc = run(
        ["git", "--git-dir", str(bare), "cat-file", "-e", f"{commit}^{{commit}}"],
        capture=True,
        check=False,
    )
    return proc.returncode == 0


def fetch_commit(bare: Path, repo: str, commit: str) -> None:
    url = GIT_GITHUB.format(repo=repo)
    log(f"fetch missing commit {commit[:10]} for {repo}")
    clash_git("fetch", url, commit, cwd=bare)


def materialize_instance(row: dict) -> str:
    instance_id = row["instance_id"]
    repo = row["repo"]
    commit = row["base_commit"]
    dest = REPOS / instance_id
    if (dest / ".git").exists():
        return f"skip {instance_id}"
    bare = bare_path(repo)
    if not (bare / "HEAD").exists():
        raise RuntimeError(f"upstream missing for {repo}")
    if not commit_in_bare(bare, commit):
        fetch_commit(bare, repo, commit)
    partial = REPOS / f".partial-{instance_id}"
    if partial.exists():
        shutil.rmtree(partial)
    REPOS.mkdir(parents=True, exist_ok=True)
    run(["git", "clone", "--local", str(bare), str(partial)], check=True, capture=True)
    run(["git", "checkout", "--detach", "--force", commit], cwd=partial, check=True, capture=True)
    # Detached working copy does not need a remote that could later hit GitHub.
    run(["git", "remote", "remove", "origin"], cwd=partial, check=False, capture=True)
    partial.replace(dest)
    return f"ok {instance_id}"


def main() -> int:
    os.chdir(ROOT)
    os.environ.setdefault("GIT_TERMINAL_PROMPT", "0")
    os.environ.setdefault("GIT_ASKPASS", "true")
    if not MANIFEST.exists():
        raise SystemExit(f"missing {MANIFEST}")
    rows = json.loads(MANIFEST.read_text(encoding="utf-8"))
    log(f"manifest {len(rows)} instances")
    njson = materialize_json(rows)
    log(f"wrote {njson} instance json files")
    repos = unique_repos(rows)
    log(f"unique upstream repos: {len(repos)}")
    if not CLASH.exists():
        raise SystemExit(f"missing {CLASH}")
    status = run([str(CLASH), "status"], check=False, capture=True)
    if "running" not in (status.stdout or ""):
        log("starting clash")
        run([str(CLASH), "start"], check=True)

    for repo in repos:
        ensure_bare(repo)

    pending = [row for row in rows if not (REPOS / row["instance_id"] / ".git").exists()]
    log(f"materialize {len(pending)} instance trees ({len(rows) - len(pending)} already present)")
    ok = 0
    failed = []
    workers = 4
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(materialize_instance, row): row["instance_id"] for row in pending}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                msg = fut.result()
                ok += 1
                log(msg)
            except Exception as exc:  # noqa: BLE001
                failed.append((name, str(exc)))
                log(f"FAIL {name}: {exc}")
    log(f"done instances ok={ok} fail={len(failed)}")
    for name, err in failed:
        log(f"  {name}: {err}")
    log(f"repos dir {du(REPOS)}  upstream {du(UPSTREAM)}  swe_bench {du(CACHE)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
