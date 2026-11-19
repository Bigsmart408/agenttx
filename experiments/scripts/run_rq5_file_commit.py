#!/usr/bin/env python3
"""Standalone RQ5/E3: paired buffered file operations, history and FS commit.

Run as root inside the dedicated VM, never on the host. No model requests.
All timers use perf_counter_ns; validation and inventory scans are untimed.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
import os
from pathlib import Path
import random
import re
import shutil
import stat
import subprocess
import sys
import time
import traceback

BACKENDS = ("native", "overlayfs", "yolofs", "agenttx")
ROOT = Path("/var/lib/atx5")
OUT = ROOT / "results"
MIRROR = Path("/hostshare/results")
SELF = Path(__file__).resolve()


def command(argv, cwd=None, check=True):
    cp = subprocess.run([str(a) for a in argv], cwd=cwd, text=True,
                        capture_output=True, timeout=1800)
    if check and cp.returncode:
        raise RuntimeError(f"{argv[0]} rc={cp.returncode}: {cp.stderr[-5000:]} {cp.stdout[-2000:]}")
    return cp


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def publish(name, value):
    atomic_json(OUT / name, value)
    atomic_json(MIRROR / name, value)


def relative(i, layout, renamed=False):
    name = f"{'renamed' if renamed else 'file'}-{i:06d}.dat"
    return Path(f"d-{i // 100:06d}") / name if layout == "sharded100" else Path(name)


def worker(dest, operation, start, stop, layout):
    """Same open/truncate, write, close / rename / unlink sequence as upstream."""
    os.umask(0o022)
    buf = bytes([255 if operation == "overwrite" else 0]) * 4096
    t = time.perf_counter_ns()
    for i in range(start, stop):
        p = dest / relative(i, layout)
        if operation in ("create", "overwrite"):
            with p.open("wb", buffering=0) as f:
                assert f.write(buf) == 4096
        elif operation == "rename":
            p.rename(dest / relative(i, layout, True))
        else:
            p.unlink()
    print(json.dumps({"syscall_loop_ns": time.perf_counter_ns() - t,
                      "operations": stop - start}))


def validate(dest, cfg, completed):
    expected = {}
    for i in range(cfg["files"]):
        done = i < completed
        op = cfg["operation"]
        if (op == "create" and not done) or (op == "unlink" and done):
            continue
        expected[relative(i, cfg["layout"], op == "rename" and done).as_posix()] = (
            b"\xff" * 4096 if op == "overwrite" and done else b"\0" * 4096)
    found = {p.relative_to(dest).as_posix(): p for p in dest.rglob("*") if not p.is_dir()}
    if found.keys() != expected.keys():
        raise AssertionError(f"paths: missing={list(expected.keys() - found.keys())[:5]} "
                             f"extra={list(found.keys() - expected.keys())[:5]}")
    for rel, p in found.items():
        st = p.lstat()
        if not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) != 0o644:
            raise AssertionError(f"file type or mode: {rel}: {st.st_mode:o}")
        if p.read_bytes() != expected[rel]:
            raise AssertionError(f"content: {rel}")
    return {"validated_files": len(found), "validated_bytes": len(found) * 4096,
            "validated_absent_paths": cfg["files"] if cfg["operation"] in ("rename", "unlink") else 0}


def inventory(roots):
    seen = set()
    counts = Counter()
    for root in roots:
        if not root.exists():
            continue
        for p in [root, *root.rglob("*")]:
            st = p.lstat()
            counts["references"] += 1
            key = (st.st_dev, st.st_ino)
            if key in seen:
                continue
            seen.add(key)
            counts["allocated_bytes"] += st.st_blocks * 512
            if stat.S_ISREG(st.st_mode):
                counts["regular_files"] += 1
                counts["logical_bytes"] += st.st_size
            elif stat.S_ISDIR(st.st_mode):
                counts["directories"] += 1
    return dict(counts), seen


def remove_any(p):
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p)
    else:
        p.unlink(missing_ok=True)


def mount_view(lowers, view, upper=None, work=None):
    view.mkdir(parents=True, exist_ok=True)
    if upper is None and len(lowers) == 1:
        # Empty AgentTX before-image: the historical view is exactly the base.
        command(["mount", "--bind", lowers[0], view])
        return
    spec = "lowerdir=" + ":".join(str(p) for p in lowers)
    if upper is not None:
        upper.mkdir(exist_ok=True)
        work.mkdir(exist_ok=True)
        spec += f",upperdir={upper},workdir={work}"
    command(["mount", "-t", "overlay", "overlay", "-o", spec, view])


def replay_layer(upper, lower, counts):
    """Upstream oldest-to-newest flatten, including whiteouts and opaque dirs."""
    lower.mkdir(exist_ok=True)
    for src in upper.iterdir():
        dst = lower / src.name
        st = src.lstat()
        if stat.S_ISCHR(st.st_mode) and st.st_rdev == 0:
            remove_any(dst)
            counts["whiteouts"] += 1
        elif src.is_dir():
            opaque = False
            for attr in ("trusted.overlay.opaque", "user.overlay.opaque"):
                try:
                    opaque |= os.getxattr(src, attr) == b"y"
                except OSError:
                    pass
            if opaque:
                remove_any(dst)
            if dst.exists() and not dst.is_dir():
                remove_any(dst)
            dst.mkdir(exist_ok=True)
            replay_layer(src, dst, counts)
        elif src.is_symlink():
            remove_any(dst)
            dst.symlink_to(os.readlink(src))
            counts["symlinks"] += 1
        else:
            if dst.is_dir():
                remove_any(dst)
            try:
                src.rename(dst)
                counts["renamed_files"] += 1
                counts["renamed_bytes"] += st.st_size
            except OSError as exc:
                if exc.errno != 18:  # EXDEV, never hide unrelated commit errors.
                    raise
                shutil.copyfile(src, dst)
                src.unlink()
                counts["copied_files"] += 1
                counts["copied_bytes"] += st.st_size


class Session:
    def __init__(self, backend, cfg, root):
        self.backend, self.cfg, self.root = backend, cfg, root
        self.base = root / "b"
        self.view = root / "m"
        self.base.mkdir(parents=True)
        self.data = self.base / "data"
        self.data.mkdir()
        if cfg["layout"] == "sharded100":
            for i in range(0, cfg["files"], 100):
                (self.data / relative(i, cfg["layout"]).parent).mkdir(exist_ok=True)
        if cfg["operation"] != "create":
            worker(self.data, "create", 0, cfg["files"], cfg["layout"])
        # Preparation and inode inventory excluded from setup and operation timers.
        self.base_inodes = {(p.stat().st_dev, p.stat().st_ino)
                            for p in self.data.rglob("*.dat")}
        self.layers, self.generations = [], []
        self.pool = None
        self.metrics = Counter()
        self.phase = "setup"
        start = time.perf_counter_ns()
        if backend == "overlayfs":
            self.upper = root / "u0"
            mount_view([self.base], self.view, self.upper, root / "w0")
        elif backend == "yolofs":
            (self.base / "yolofs.toml").write_text(
                'permission = true\nstaging = true\nauto_snapshot = false\nprompt_timeout = 5\n'
                '[rules]\n"." = "allow"\n"/" = "allow"\n')
            command(["yolo", "mount"], self.base)
            self.view = self.base / ".yolofs/mnt" / self.base.relative_to("/")
        elif backend == "agenttx":
            from agenttx.semisolate import SharedSemisolate
            self.pool = SharedSemisolate(
                self.data, sandbox_dir=root / "a", trace_reads=False,
                try_bin=Path("/opt/rq5/agenttx/third_party/try/try"))
            self.upper = self.pool.sandbox_dir / "upperdir"
            original_snapshot = self.pool.layers.snapshot_before
            original_detect = self.pool.upperdir_digests

            def snapshot(*args, **kwargs):
                t = time.perf_counter_ns()
                result = original_snapshot(*args, **kwargs)
                if self.phase == "work":
                    self.metrics["snapshot_ns"] += time.perf_counter_ns() - t
                    self.metrics["snapshot_requests"] += 1
                return result

            def detect():
                t = time.perf_counter_ns()
                result = original_detect()
                if self.phase == "work":
                    self.metrics["detect_ns"] += time.perf_counter_ns() - t
                return result

            self.pool.layers.snapshot_before = snapshot
            self.pool.upperdir_digests = detect
        else:
            self.view = self.base
        self.setup_ns = time.perf_counter_ns() - start

    def boundary(self, index):
        if self.backend == "overlayfs":
            command(["umount", self.view])
            self.layers.append(self.upper)
            self.upper = self.root / f"u{len(self.layers)}"
            mount_view([*reversed(self.layers), self.base], self.view,
                       self.upper, self.root / f"w{len(self.layers)}")
        elif self.backend == "yolofs":
            cp = command(["yolo", "snapshot", f"rq5-{index}"], self.base)
            match = re.search(r"snapshot (\d+)", cp.stdout + cp.stderr)
            if not match:
                raise RuntimeError("snapshot generation missing: " + cp.stdout + cp.stderr)
            self.generations.append(int(match[1]))

    def execute(self, index):
        cfg = self.cfg
        start = index * cfg["files"] // cfg["batches"]
        stop = (index + 1) * cfg["files"] // cfg["batches"]
        self.phase = "work"
        before = self.metrics.copy()
        dest = self.data if self.pool else self.view / "data"
        argv = [sys.executable, SELF, "--worker", dest, cfg["operation"],
                start, stop, cfg["layout"]]
        t = time.perf_counter_ns()
        if self.pool:
            cp = self.pool.run([str(a) for a in argv], trace_reads=False, timeout_s=1800)
            if cp.returncode:
                raise RuntimeError(f"agenttx command: {cp.stderr[-3000:]}")
        else:
            cp = command(argv)
        elapsed = time.perf_counter_ns() - t
        delta = self.metrics - before
        raw = json.loads(cp.stdout.strip().splitlines()[-1])
        step = {"index": index, "command_and_internal_record_ns": elapsed,
                "detect_ns": delta["detect_ns"], "snapshot_ns": delta["snapshot_ns"],
                "command_ns": elapsed - delta["detect_ns"] - delta["snapshot_ns"], **raw}
        if not self.pool and self.backend != "native":
            t = time.perf_counter_ns()
            self.boundary(index + 1)
            step["snapshot_ns"] = time.perf_counter_ns() - t
        return step

    def seal(self):
        if self.pool:
            t = time.perf_counter_ns()
            self.pool.layers.snapshot_before(
                self.pool._step_count, self.upper,
                fingerprints=self.pool._cached_digests,
                changed_paths=self.pool._pending_snapshot_changes)
            return time.perf_counter_ns() - t
        return 0

    def check_history(self):
        cfg = self.cfg
        if self.backend == "native":
            return []
        results = []
        for index in sorted({0, 1, cfg["batches"] // 2, cfg["batches"]}):
            completed = index * cfg["files"] // cfg["batches"]
            if self.backend == "yolofs":
                command(["yolo", "travel", str(self.generations[index])], self.base)
                dest = self.view / "data"
            else:
                view = self.root / "h"
                if self.pool:
                    snapshot = self.pool.layers.resolve_before(index)
                    staged = snapshot / self.data.relative_to("/")
                    lowers = [staged, self.data] if staged.exists() else [self.data]
                else:
                    lowers = [*reversed(self.layers[:index + 1]), self.base]
                mount_view(lowers, view)
                dest = view if self.pool else view / "data"
            try:
                results.append({"boundary": index, **validate(dest, cfg, completed)})
            finally:
                if self.backend != "yolofs":
                    command(["umount", self.root / "h"])
        return results

    def staging_roots(self):
        if self.pool:
            return [self.upper, self.pool.layers.root]
        if self.backend == "overlayfs":
            return [*self.layers, self.upper]
        if self.backend == "yolofs":
            return [self.base / ".yolofs/inodes", self.base / ".yolofs/journal"]
        return []

    def commit(self):
        self.phase = "commit"
        counts = Counter()
        if self.backend == "native":
            return None, {}, 0
        # Upstream OverlayFS excludes pre-commit unmount from flatten timing;
        # retain it separately and include it in the total workflow time.
        prepare_ns = 0
        if self.backend == "overlayfs":
            t = time.perf_counter_ns()
            command(["umount", self.view])
            prepare_ns = time.perf_counter_ns() - t
        t = time.perf_counter_ns()
        if self.pool:
            cp = self.pool.commit()
            if cp.returncode:
                raise RuntimeError(f"agenttx commit: {cp.stderr[-5000:]} {cp.stdout[-2000:]}")
        elif self.backend == "overlayfs":
            for layer in [*self.layers, self.upper]:
                replay_layer(layer, self.base, counts)
        else:
            command(["yolo", "commit"], self.base)
        return time.perf_counter_ns() - t, dict(counts), prepare_ns

    def cleanup(self):
        self.phase = "cleanup"
        if self.pool:
            self.pool.close()
        elif self.backend == "overlayfs":
            command(["umount", self.view], check=False)
        elif self.backend == "yolofs":
            command(["yolo", "unmount"], self.base)
        shutil.rmtree(self.root)


def run_session(backend, cfg, index, kind, repeat):
    ident = f"{index:06d}"
    result = {"id": ident, "backend": backend, "kind": kind, "repeat": repeat, **cfg,
              "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    session = None
    try:
        session = Session(backend, cfg, ROOT / "s" / ident)
        result["setup_ns"] = session.setup_ns
        # AgentTX's initial before-image is included in its first execute call.
        initial_ns = 0
        if backend in ("overlayfs", "yolofs"):
            t = time.perf_counter_ns()
            session.boundary(0)
            initial_ns = time.perf_counter_ns() - t
        steps = [session.execute(i) for i in range(cfg["batches"])]
        seal_ns = session.seal()
        result["steps"] = steps
        result["initial_snapshot_ns"] = initial_ns
        result["seal_ns"] = seal_ns
        result["command_ns"] = sum(x["command_ns"] for x in steps)
        result["detect_ns"] = sum(x["detect_ns"] for x in steps)
        result["snapshot_ns"] = initial_ns + seal_ns + sum(x["snapshot_ns"] for x in steps)
        result["snapshot_requests"] = cfg["batches"] + 1 if backend != "native" else 0
        result["record_ns"] = 0  # file-recording path; no dependency/context ledger
        result["restore_ns"] = None  # history validation is untimed, canary only
        if session.pool:
            snapshots = [session.pool.layers.root / f"before_{i:04d}"
                         for i in range(cfg["batches"] + 1)]
            result["materialized_snapshots"] = sum(p.is_dir() and not p.is_symlink() for p in snapshots)
            result["reused_snapshots"] = sum(p.is_symlink() for p in snapshots)
            result["blob_storage"], _ = inventory([session.pool.layers.root / "blobs"])
        else:
            result["materialized_snapshots"] = result["snapshot_requests"]
            result["reused_snapshots"] = 0
        result["history_checks"] = session.check_history() if kind == "canary" else []
        physical, backing_inodes = inventory(session.staging_roots())
        result["staging_history_before_commit"] = physical
        commit_ns, counts, prepare_ns = session.commit()
        result.update(commit_ns=commit_ns, commit_prepare_ns=prepare_ns, commit_operations=counts)
        result["validation"] = validate(session.data, cfg, cfg["files"])
        observed = Counter()
        for p in session.data.rglob("*.dat"):
            st = p.stat()
            key = (st.st_dev, st.st_ino)
            origin = ("staging" if key in backing_inodes else
                      "base" if key in session.base_inodes else "new")
            observed[f"output_files_from_{origin}"] += 1
            observed[f"output_bytes_from_{origin}"] += st.st_size
        result["commit_inode_audit"] = dict(observed)
        t = time.perf_counter_ns()
        session.cleanup()
        result["cleanup_ns"] = time.perf_counter_ns() - t
        result["workflow_ns"] = (result["setup_ns"] + result["command_ns"] +
                                 result["detect_ns"] + result["snapshot_ns"] +
                                 (commit_ns or 0) + prepare_ns)
        result["status"] = "passed"
    except Exception:
        result["status"] = "failed"
        result["error"] = traceback.format_exc()
        # Preserve failed FS and all measurements for diagnosis. Stop live mounts.
        if session is not None:
            try:
                if session.pool:
                    session.pool.close(destroy=False)
                elif backend == "overlayfs":
                    command(["umount", session.view], check=False)
                elif backend == "yolofs":
                    command(["yolo", "unmount"], session.base, check=False)
            except Exception:
                result["cleanup_error"] = traceback.format_exc()
    publish(f"sessions/{ident}.json", result)
    for root in (OUT, MIRROR):
        root.mkdir(parents=True, exist_ok=True)
        with (root / "steps.jsonl").open("a") as f:
            for step in result.get("steps", []):
                f.write(json.dumps({"session_id": ident, "backend": backend,
                                    "kind": kind, **cfg, **step}, sort_keys=True) + "\n")
    return result


def source_manifest():
    roots = [Path("/opt/rq5/agenttx/src"), Path("/opt/rq5/agenttx/third_party/try"), SELF]
    files = []
    for root in roots:
        paths = [root] if root.is_file() else sorted(root.rglob("*"))
        for p in paths:
            if not p.is_file() or ".git" in p.parts or "__pycache__" in p.parts:
                continue
            files.append({"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
    return {"yolofs_commit": "8848c243ba28ff91432c1da868671d6ed8910ed6",
            "perf_eval_commit": "bc32d0835ed8b9d28a799e59574f31855999a89a", "files": files,
            "try_commit": command(["sha256sum", "/usr/local/bin/try-commit"]).stdout.strip()}


def main():
    global ROOT, OUT, MIRROR
    os.umask(0o022)
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", nargs=5)
    parser.add_argument("--run-root", type=Path, default=ROOT)
    parser.add_argument("--mirror", type=Path, default=MIRROR)
    parser.add_argument("--resume-from", type=Path)
    parser.add_argument("--remeasure-backend", action="append", choices=BACKENDS, default=[])
    args = parser.parse_args()
    if args.worker:
        dest, op, start, stop, layout = args.worker
        worker(Path(dest), op, int(start), int(stop), layout)
        return
    if os.geteuid() != 0 or not Path("/hostshare/guest-build-ready").exists():
        raise SystemExit("run only inside the prepared dedicated RQ5 VM")
    ROOT, MIRROR = args.run_root, args.mirror
    OUT = ROOT / "results"
    if args.resume_from and MIRROR.resolve() == args.resume_from.resolve():
        raise SystemExit("continuation must use a new output directory")
    if (OUT / "workload-manifest.json").exists():
        raise SystemExit("existing cohort: do not overwrite or silently resume")
    sys.path.insert(0, "/opt/rq5/agenttx/src")
    os.environ["PYTHONPATH"] = "/opt/rq5/agenttx/src"
    ROOT.mkdir(parents=True, exist_ok=True)
    environment = {"uname": command(["uname", "-a"]).stdout,
                   "cpu": command(["lscpu"]).stdout,
                   "memory": Path("/proc/meminfo").read_text(),
                   "mount": command(["findmnt", "-J", "/"]).stdout,
                   "filesystem": command(["lsblk", "-f"]).stdout,
                   "rust": command(["/root/.cargo/bin/rustc", "--version"]).stdout,
                   "packages": command(["dpkg-query", "-W"]).stdout,
                   "overlay_parameters": {p.name: p.read_text() for p in
                                          Path("/sys/module/overlay/parameters").glob("*")},
                   "timing": "buffered, warm-cache, monotonic ns; preparation, validation, inventories excluded",
                   "agenttx": "SharedSemisolate(trace_reads=False), real digest detection and snapshots; upstream try C helpers installed"}
    publish("environment.json", environment)
    sources = source_manifest()
    publish("source-manifest.json", sources)
    configs = [{"files": n, "batches": b, "operation": op, "layout": layout}
               for n in (1000, 10000, 100000)
               for layout in (("flat", "sharded100") if n == 100000 else ("flat",))
               for op in ("create", "overwrite", "rename", "unlink") for b in (1, 10)]
    rng = random.Random(20261113)
    queue = []
    for cfg in configs:
        for repeat in range(11):
            backends = list(BACKENDS)
            rng.shuffle(backends)
            for backend in backends:
                queue.append({"backend": backend, "cfg": cfg,
                              "kind": "warmup" if repeat == 0 else "measured", "repeat": repeat})
    canaries = [{"backend": backend, "cfg": {"files": 20, "batches": b,
                 "operation": op, "layout": layout}, "kind": "canary", "repeat": 0}
                for layout in ("flat", "sharded100") for op in ("create", "overwrite", "rename", "unlink")
                for b in (1, 10) for backend in BACKENDS]
    manifest = {"seed": 20261113, "file_bytes": 4096,
             "configs": configs, "repetitions": 10, "warmups_per_config_backend": 1,
             "measured_sessions": sum(q["kind"] == "measured" for q in queue),
             "queue": canaries + queue, "worker": "shared Python syscall-equivalent to pinned perf-eval"}
    skipped, remeasured = {}, {}
    if args.resume_from:
        prior = args.resume_from
        previous_manifest = json.loads((prior / "workload-manifest.json").read_text())
        if previous_manifest != manifest:
            raise SystemExit("continuation workload differs from the original registration")
        old_sources = {r["path"]: r["sha256"] for r in
                       json.loads((prior / "source-manifest.json").read_text())["files"]}
        new_sources = {r["path"]: r["sha256"] for r in sources["files"]}
        allowed = {str(SELF), "/opt/rq5/agenttx/src/agenttx/layers.py"}
        changed = {p for p in old_sources.keys() | new_sources.keys()
                   if old_sources.get(p) != new_sources.get(p)}
        if not changed <= allowed:
            raise SystemExit("unregistered runtime changes: " + str(changed - allowed))
        for index, item in enumerate(manifest["queue"]):
            path = prior / "sessions" / f"{index:06d}.json"
            if not path.exists() or item["kind"] == "canary":
                continue
            result = json.loads(path.read_text())
            if result["status"] != "passed":
                continue
            expected = {"id": f"{index:06d}", "backend": item["backend"],
                        "kind": item["kind"], "repeat": item["repeat"], **item["cfg"]}
            if any(result.get(k) != v for k, v in expected.items()):
                raise SystemExit("prior result does not match registration: " + str(path))
            record = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                      "kind": item["kind"]}
            if item["backend"] in args.remeasure_backend:
                remeasured[index] = record
            else:
                skipped[index] = record
        publish("continuation.json", {"parent": str(prior), "changed_source_paths": sorted(changed),
                "preserved_passed_sessions": list(skipped.values()),
                "remeasured_backends": args.remeasure_backend,
                "remeasured_prior_sessions": list(remeasured.values()),
                "fresh_canaries": len(canaries), "failure_records_preserved": True})
    publish("workload-manifest.json", manifest)
    rows, counts = [], Counter()
    for index, item in enumerate(canaries + queue):
        if index in skipped:
            continue
        publish("progress.json", {"state": "running", "current": {"index": index, **item},
                "completed": dict(counts), "total_sessions": len(canaries + queue),
                "preserved_measured": sum(r["kind"] == "measured" for r in skipped.values()),
                "preserved_warmups": sum(r["kind"] == "warmup" for r in skipped.values()),
                "updated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        result = run_session(item["backend"], item["cfg"], index, item["kind"], item["repeat"])
        counts[item["kind"] + "_" + result["status"]] += 1
        flat = {k: v for k, v in result.items() if not isinstance(v, (dict, list))}
        rows.append(flat)
        fields = sorted(set().union(*(r.keys() for r in rows)))
        for root in (OUT, MIRROR):
            with (root / "summary.csv").open("w") as f:
                w = csv.DictWriter(f, fields)
                w.writeheader()
                w.writerows(rows)
        print(json.dumps({"index": index, "backend": item["backend"], "kind": item["kind"],
                          "status": result["status"], "counts": dict(counts)}), flush=True)
        if result["status"] != "passed":
            publish("progress.json", {"state": "failed", "failed_session": index,
                    "completed": dict(counts), "error": result["error"]})
            raise SystemExit(1)
    publish("progress.json", {"state": "complete", "completed": dict(counts),
                             "preserved_measured": sum(r["kind"] == "measured" for r in skipped.values()),
                             "preserved_warmups": sum(r["kind"] == "warmup" for r in skipped.values()),
                             "total_sessions": len(canaries + queue)})


if __name__ == "__main__":
    main()
