#!/usr/bin/env python3
"""Unmodified try CLI adapter: retain -N environments and layer them with -L."""
from __future__ import annotations

import os
from pathlib import Path
import re
import selectors
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace


def descendants(pid):
    result = []
    pending = [pid]
    while pending:
        current = pending.pop()
        result.append(current)
        path = Path(f'/proc/{current}/task/{current}/children')
        try:
            pending.extend(int(value) for value in path.read_text().split())
        except FileNotFoundError:
            pass
    return result


class TrySession:
    def __init__(self, binary, workspace, state, log, *, lowers=()):
        self.binary, self.workspace, self.state_dir = binary, workspace, state
        state.mkdir()
        self.log_path = log
        self.log = log.open('ab')
        self.tmp = state/'tmp'
        self.tmp.mkdir()
        self.env = dict(os.environ, TMPDIR=str(self.tmp), PWD=str(workspace), SHELL='/bin/sh')
        self.completed, self.snapshots, self.probes = [], [], []
        self.initial_lowers = list(lowers)
        self.process = None
        self.worker_pid = None
        self.closed = False
        self.commands = []
        self.workload_mounts = []
        try:
            self.start()
        except BaseException:
            self.close()
            raise

    def start(self):
        self.active_sandbox = self.state_dir/f'l{len(self.completed):02}'
        self.active_sandbox.mkdir()
        command = [str(self.binary), '-N', str(self.active_sandbox)]
        lowers = [*reversed(self.completed), *self.initial_lowers]
        if lowers:
            command += ['-L', ':'.join(map(str, lowers))]
        marker = str(Path(__file__).with_name('rq5_try_wait.py'))
        command += ['--', sys.executable, '-B', '-u', marker, str(self.active_sandbox)]
        self.commands.append(command)
        self.process = subprocess.Popen(command, cwd=self.workspace, env=self.env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, text=True)
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            deadline = time.monotonic()+30
            while True:
                if time.monotonic() > deadline:
                    raise TimeoutError('Native try did not expose a writable environment')
                if not selector.select(timeout=.1):
                    continue
                line = self.process.stdout.readline()
                if line == 'RQ5_TRY_READY\n':
                    break
                if not line:
                    raise RuntimeError('Native try failed: '+self.log_path.read_text())
        matches = []
        for pid in descendants(self.process.pid):
            try:
                arguments = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
            except FileNotFoundError:
                continue
            if (arguments and Path(os.fsdecode(arguments[0])).name.startswith('python')
                    and marker.encode() in arguments and str(self.active_sandbox).encode() in arguments):
                matches.append(pid)
        assert len(matches) == 1, matches
        self.worker_pid = matches[0]
        self.view = Path(f'/proc/{self.worker_pid}/root')/self.workspace.relative_to('/')
        assert self.view.is_dir()
        self.mountinfo = Path(f'/proc/{self.worker_pid}/mountinfo').read_text()
        mount = next(line for line in self.mountinfo.splitlines()
                     if line.split()[4] == str(self.workspace))
        assert ' - overlay overlay ' in mount and 'mergerdir' not in mount
        self.workload_mounts.append(mount)

    def stop(self):
        if self.process is None:
            return
        process = self.process
        self.process = None
        if process.poll() is None:
            process.stdin.write('exit\n')
            process.stdin.flush()
        output, _ = process.communicate(timeout=30)
        if process.returncode:
            raise RuntimeError(f'Native try exit {process.returncode}: {output}\n'+self.log_path.read_text())
        self.worker_pid = None

    def pids(self):
        return descendants(self.process.pid) if self.process else []

    def snapshot(self):
        self.stop()
        self.completed.append(self.active_sandbox)
        point = SimpleNamespace(generation=len(self.snapshots)+1,
                                layers=tuple(reversed(self.completed)))
        self.start()
        self.snapshots.append(point)
        return point

    def history_view(self, point):
        probe = TrySession(self.binary, self.workspace,
            self.state_dir/f'probe-{len(self.probes):02}', self.log_path,
            lowers=(*point.layers, *self.initial_lowers))
        self.probes.append(probe)
        return probe.view

    def commit(self):
        self.stop()
        for probe in self.probes:
            probe.close()
        layers = [*reversed(self.initial_lowers), *self.completed, self.active_sandbox]
        selected = '^'+re.escape(str(self.workspace))+'(/|$)'
        include, exclude = self.state_dir/'include', self.state_dir/'exclude'
        include.write_text(selected+'\n')
        exclude.write_text('/run/mount\n/run/try_trace.log\n')
        helper = shutil.which('try-commit')
        assert helper is not None
        for layer in layers:
            command = [helper, '-E', str(exclude), '-I', str(include), str(layer)]
            self.commands.append(command)
            subprocess.run(command, cwd=self.workspace, env=self.env,
                           stdout=self.log, stderr=self.log, check=True, timeout=90)

    def close(self):
        if self.closed:
            return
        errors = []
        for stop in [self.stop, *(probe.close for probe in self.probes)]:
            try:
                stop()
            except BaseException as error:
                errors.append(error)
        self.log.close()
        self.closed = True
        if not errors:
            shutil.rmtree(self.state_dir)
        else:
            raise errors[0]
