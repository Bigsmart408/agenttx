"""AgentTX causal selection and conversation rewind over a real YoloFS mount."""

from __future__ import annotations

import json
import os
from pathlib import Path
import select
import shlex
import signal
import shutil
import subprocess
import tempfile
import time
from types import SimpleNamespace

from agenttx.conversation import ConversationLog, record_tool_record
from agenttx.ledger import Effect, EffectKind, Ledger
from agenttx.faults import VerifierSpec
from agenttx.object_identity import HardlinkCatalog
from agenttx.policy import CommitPolicy
from agenttx.runtime import AgentTX, _atomic_write_json
from agenttx.trace import parse_strace_effects

from .history import ObjectHistory, overlaps
from .storage import YoloSession


class YoloPool:
    def __init__(self, workspace, state_dir, trace_reads=True):
        self.storage = YoloSession(workspace, state_dir)
        self.workspace = self.storage.workspace
        self.session_dir = self.sandbox_dir = self.storage.state_dir
        self.trace_reads = trace_reads
        self.history = ObjectHistory(self.storage)
        self._step_count = len(self.history.data["points"])
        self._groups = []
        self._closed = False

    def run(self, argv, *, trace_reads=None, timeout_s=None):
        if self._closed:
            raise RuntimeError("session is closed")
        if timeout_s is not None and timeout_s <= 0:
            raise ValueError("timeout must be positive")
        step = self._step_count
        self.history.point(step)
        trace = self.session_dir / f"trace-{step}.log"
        stdout = self.session_dir / f"stdout-{step}.log"
        stderr = self.session_dir / f"stderr-{step}.log"
        read_fd, write_fd = os.pipe()
        wrapper = ('cd -- "$1" || exit 125; shift; "$@"; '
                   f'_atx_status=$?; printf "%s\\n" "$_atx_status" >&{write_fd}; '
                   'exit "$_atx_status"')
        args = ["chroot", str(self.storage.root), "/bin/bash", "-c", wrapper,
                "agenttx-yolofs", str(self.workspace), *[str(arg) for arg in argv]]
        capture_reads = self.trace_reads if trace_reads is None else trace_reads
        if capture_reads:
            args = ["strace", "-I", "1", "-f", "-qq", "-yy", "-s", "4096", "-e",
                    "trace=%file,%process", "-o", str(trace), *args]
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PWD=str(self.workspace))
        started = time.monotonic()
        try:
            with stdout.open("wb") as out, stderr.open("wb") as err:
                proc = subprocess.Popen(args, stdout=out, stderr=err, pass_fds=(write_fd,),
                                        start_new_session=True, env=env)
                self._groups.append(proc.pid)
                os.close(write_fd)
                write_fd = None
                status = b""
                timed_out = False
                deadline = started + (timeout_s or 300)
                while b"\n" not in status:
                    if time.monotonic() >= deadline:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
                        timed_out = True
                        break
                    ready, _, _ = select.select([read_fd], [], [], min(.05, max(0, deadline-time.monotonic())))
                    if ready:
                        chunk = os.read(read_fd, 128)
                        if not chunk:
                            break
                        status += chunk
                    if proc.poll() is not None and not ready:
                        break
                if timed_out:
                    returncode = 124
                elif status.strip():
                    returncode = int(status.strip().splitlines()[0])
                    if proc.poll() is None:
                        # Detach strace from background descendants rather than
                        # destroying the process state at a command boundary.
                        try:
                            proc.wait(timeout=.15)
                        except subprocess.TimeoutExpired:
                            proc.terminate()
                    proc.wait(timeout=10)
                else:
                    returncode = proc.wait(timeout=10)
        finally:
            os.close(read_fd)
            if write_fd is not None:
                os.close(write_fd)
        # strace resolves fd paths from outside the chroot. Convert the mounted
        # root prefix back to the logical paths used by the causal ledger.
        text = trace.read_text().replace(str(self.storage.root) + "/", "/") if capture_reads else ""
        effects = parse_strace_effects(text, self.workspace) if capture_reads else []
        delta = self.history.record(kind="tool", step=step)
        for relative, value in delta.items():
            effects.append(Effect(str(self.workspace / relative),
                                  EffectKind.DELETE if value is None else EffectKind.WRITE))
        self._step_count += 1
        return SimpleNamespace(step_index=step, returncode=returncode, duration_s=time.monotonic()-started,
                               stdout=stdout.read_text(errors="replace")[:8_000_000],
                               stderr=stderr.read_text(errors="replace")[:8_000_000], effects=effects)

    def rollback_causal_groups(self, step_ids, groups):
        self.history.restore(groups)

    def rollback_steps(self, step_ids):
        if step_ids:
            self.history.restore([(min(step_ids), [str(self.workspace / name)
                for name in sorted(self.history.current.keys() | self.history.before(min(step_ids)).keys())])])

    def close(self, destroy=False):
        if destroy:
            for group in self._groups:
                try:
                    os.killpg(group, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        self.storage.close(destroy=destroy)
        self._closed = True


class YoloAgentTX(AgentTX):
    """Reuse the causal ledger and context closure, adapt filesystem operations."""

    @classmethod
    def begin(cls, workdir, session_dir, *, trace_reads=True, **kwargs):
        if kwargs:
            raise TypeError(f"unsupported YoloFS settings: {sorted(kwargs)}")
        pool = YoloPool(workdir, session_dir, trace_reads=trace_reads)
        tx = cls(workspace=Path(workdir), pool=pool, trace_reads=trace_reads)
        tx.object_catalog.refresh(tx.workspace)
        tx._catalog_initialized = True
        tx._persist()
        return tx

    @classmethod
    def load(cls, session_dir):
        session_dir = Path(session_dir)
        data = json.loads((session_dir / "agenttx.json").read_text())
        if data.get("storage_backend") != "yolofs":
            raise ValueError("not a YoloFS AgentTX session")
        tx = cls(workspace=Path(data["workspace"]), ledger=Ledger.from_dict(data["ledger"]),
                 conversation=ConversationLog.from_dict(data["conversation"]),
                 object_catalog=HardlinkCatalog.from_dict(data["object_catalog"]),
                 verifiers={name: VerifierSpec.from_dict(spec)
                            for name, spec in data.get("verifiers", {}).items()},
                 trace_reads=data["trace_reads"])
        tx.pool = YoloPool(tx.workspace, session_dir, trace_reads=tx.trace_reads)
        tx.pool._step_count = len(tx.ledger.steps)
        tx._catalog_initialized = True
        intent = session_dir / "recovery-intent.json"
        if intent.exists():
            recovery = json.loads(intent.read_text())
            tx.pool.rollback_causal_groups(recovery["targets"], recovery["groups"])
            tx.ledger.mark_rolled_back(recovery["targets"])
            tx._rewind_conversation(recovery["targets"], mode=recovery["mode"])
            tx._persist()
            intent.unlink()
        return tx

    def _persist(self):
        super()._persist()
        path = self.pool.session_dir / "agenttx.json"
        data = json.loads(path.read_text())
        data["storage_backend"] = "yolofs"
        _atomic_write_json(path, data)

    def _overlay_entry(self, logical):
        try:
            entry = self.pool.storage.path(logical)
        except ValueError:
            return None
        return entry if os.path.lexists(entry) else None

    def path_exists(self, path):
        return self._overlay_entry(Path(path)) is not None

    def call(self, argv, *, tool_name="run_shell", **kwargs):
        record = self.run_tool(tool_name, argv, **kwargs)
        record_tool_record(self.conversation, record, {"argv": list(argv)})
        self._persist()
        return record

    def fork(self, session_dir=None):
        """Fork into another real YoloFS mount over the same unpublished base."""
        self._persist()
        destination = (Path(session_dir) if session_dir is not None else
                       Path(tempfile.mkdtemp(prefix="agenttx-yolofs-fork-",
                                             dir=self.pool.session_dir.parent)))
        if destination.exists() and any(destination.iterdir()):
            raise ValueError("fork destination must be empty")
        branch = None
        try:
            branch = type(self).begin(self.workspace, destination, trace_reads=self.trace_reads)
            history, original = branch.pool.history, self.pool.history
            for blob in original.blobs.iterdir():
                target = history.blobs / blob.name
                if not target.exists():
                    try:
                        os.link(blob, target)
                    except OSError:
                        shutil.copyfile(blob, target)
            history.data = json.loads(json.dumps(original.data))
            history.data["points"]["-1"] = len(history.data["events"])
            changed = [str(self.workspace / name) for name in history.current.keys() | original.current.keys()
                       if history.current.get(name) != original.current.get(name)]
            if changed:
                history.restore([(-1, changed)])
            history.data["points"].pop("-1")
            history.save()
            branch.ledger = Ledger.from_dict(self.ledger.to_dict())
            branch.conversation = ConversationLog.from_dict(self.conversation.to_dict())
            branch.object_catalog = HardlinkCatalog.from_dict(self.object_catalog.to_dict())
            branch.verifiers = {name: VerifierSpec.from_dict(spec.to_dict())
                                for name, spec in self.verifiers.items()}
            branch.pool._step_count = len(branch.ledger.steps)
            branch._persist()
            return branch
        except BaseException:
            if branch is not None:
                branch.close(destroy=True)
            raise

    def _rollback_selected_targets(self, targets, *, mode):
        if any(step <= self.ledger.committed_frontier for step in targets):
            raise ValueError("rollback crosses committed frontier")
        selected = set(targets)
        writers = [(step.step_id, effect.path) for step in self.ledger.steps
                   if step.step_id in selected for effect in step.effects
                   if effect.kind in (EffectKind.WRITE, EffectKind.DELETE)]
        remaining = {path for _, path in writers}
        groups = []
        while remaining:
            paths = {remaining.pop()}
            while True:
                found = {path for path in remaining if any(overlaps(path, old) for old in paths)}
                if not found:
                    break
                remaining -= found
                paths |= found
            before = min(step for step, path in writers if any(overlaps(path, old) for old in paths))
            # Earlier retained writers are the state we are restoring. Only
            # later retained writers would conflict with this reconstruction.
            for step in self.ledger.steps:
                if step.step_id < before or step.step_id in selected or step.status == "rolled_back":
                    continue
                if any(effect.kind in (EffectKind.WRITE, EffectKind.DELETE)
                       and any(overlaps(effect.path, path) for path in paths) for effect in step.effects):
                    raise ValueError("recovery would overwrite a later retained writer")
            groups.append((before, sorted(paths)))
        intent = self.pool.session_dir / "recovery-intent.json"
        _atomic_write_json(intent, dict(targets=targets, mode=mode, groups=groups))
        self.pool.rollback_causal_groups(targets, sorted(groups))
        self.ledger.mark_rolled_back(targets)
        self._rewind_conversation(targets, mode=mode)
        self._persist()
        intent.unlink()
        return targets

    def commit(self, up_to=None):
        active = self.ledger.active_uncommitted()
        latest = max((step.step_id for step in active), default=self.ledger.committed_frontier)
        if up_to is not None and up_to != latest:
            raise ValueError("YoloFS port commits the full active session")
        self.pool.storage.commit()
        for step in active:
            step.status = "committed"
        self.ledger.committed_frontier = latest
        self._persist()
        return latest

    def commit_frontier(self, up_to=None):
        return self.commit(up_to)

