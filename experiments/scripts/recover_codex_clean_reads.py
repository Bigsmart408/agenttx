#!/usr/bin/env python3
"""Recover native Codex command reads from traced process trees.

Match each exported shell command to its actual execve arguments, then parse
that process and its descendants. This avoids using JSON delivery times as
syscall execution boundaries. Original records and reads stay unchanged.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
import csv
import hashlib
import json
from pathlib import Path
import re
import shlex

from agenttx.trace import _decode_string, _split_args, parse_strace_effects

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'review/native-clean-campaign-20261104/capture_inventory.csv'
OUT = ROOT / 'review/native-clean-metric-audit-20261104/process-reads'
LINE = re.compile(r'^\s*(\d+)\s+(\d+\.\d+)\s+(.*)$')
SPAWN = re.compile(r'^(?:clone3?|fork|vfork)\(.*\)\s+=\s+(\d+)\b')


def command_text(command: str) -> str:
    try:
        argv = shlex.split(command)
    except ValueError:
        return command
    if argv and Path(argv[0]).name in {'bash', 'sh', 'zsh', 'dash'}:
        for index, arg in enumerate(argv[1:], 1):
            if arg.startswith('-') and 'c' in arg and index + 1 < len(argv):
                return argv[index + 1]
    return command


def shell_exec(body: str):
    if not body.startswith('execve(') or not body.rstrip().endswith('= 0'):
        return None
    args = _split_args(body[len('execve('):], limit=2)
    if len(args) < 2:
        return None
    executable = _decode_string(args[0])
    if not executable or Path(executable).name not in {'bash', 'sh', 'zsh', 'dash'}:
        return None
    array = args[1].strip()
    if not (array.startswith('[') and array.endswith(']')):
        return None
    tokens = _split_args(array[1:-1])
    values = [_decode_string(token) for token in tokens]
    for index, value in enumerate(values):
        if index and value and value.startswith('-') and 'c' in value and index + 1 < len(values):
            text = values[index + 1]
            if text is not None:
                return text, tokens[index + 1].rstrip().endswith('...')
    return None


def recover(row):
    export = Path(row['trace_path'])
    dest = OUT / row['suite'] / f"{row['task']}.json"
    if dest.exists():
        cached = json.loads(dest.read_bytes())
        if cached['trace_sha256'] == row['trace_sha256']:
            return {k: cached[k] for k in ('suite', 'task', 'command_calls', 'matched_commands', 'unmatched_step_ids', 'new_read_effects')}
    raw = export.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == row['trace_sha256']
    data = json.loads(raw)
    trace = export.parent / 'codex/codex.strace'
    calls = {int(c['step_id']): c for t in data['conversation']['turns'] for c in t.get('calls', [])}
    windows = data['event_windows']
    commands = []
    for index, step in enumerate(data['ledger']['steps']):
        if step['tool_name'] != 'codex:command_execution':
            continue
        item = json.loads(calls[step['step_id']]['arguments'])
        commands.append(dict(step_id=step['step_id'], text=command_text(item['command']),
                             start=windows[index]['start'], end=windows[index]['end']))
    candidates = []
    children = defaultdict(set)
    unfinished = {}
    sha = hashlib.sha256()
    with trace.open('rb') as handle:
        for raw_line in handle:
            sha.update(raw_line)
            # Process ancestry and shell execs suffice for matching; reads are
            # parsed in a second streaming pass after ownership is established.
            if not any(token in raw_line for token in (b'execve(', b'clone(', b'clone3(', b'fork(', b'vfork(', b'resumed>')):
                continue
            match = LINE.match(raw_line.decode('utf-8', errors='replace'))
            if not match:
                continue
            pid, stamp, body = int(match[1]), float(match[2]), match[3]
            if '<unfinished ...>' in body:
                unfinished[pid] = (stamp, body.split('<unfinished ...>', 1)[0])
                continue
            if '<... ' in body and ' resumed>' in body:
                original_stamp, prefix = unfinished.pop(pid, (stamp, ''))
                body = prefix + body.split(' resumed>', 1)[1]
                stamp = original_stamp
            spawn = SPAWN.match(body)
            if spawn:
                children[pid].add(int(spawn[1]))
            if parsed := shell_exec(body):
                candidates.append(dict(pid=pid, stamp=stamp, text=parsed[0], truncated=parsed[1]))
    exact = defaultdict(list)
    truncated = []
    for c in candidates:
        (truncated if c['truncated'] else exact[c['text']]).append(c)
    owners, matches, unmatched = {}, [], []
    used = set()
    for command in commands:
        eligible = [c for c in exact.get(command['text'], [])
                    if c['pid'] not in used and c['stamp'] <= command['end']]
        eligible += [c for c in truncated if c['pid'] not in used
                     and c['stamp'] <= command['end'] and command['text'].startswith(c['text'])]
        if not eligible:
            unmatched.append(command['step_id'])
            continue
        candidate = min(eligible, key=lambda c: abs(command['start']-c['stamp']))
        used.add(candidate['pid'])
        frontier = [candidate['pid']]
        found = set(frontier)
        while frontier:
            for child in children.get(frontier.pop(), ()):
                if child not in found:
                    found.add(child); frontier.append(child)
        for pid in found:
            assert pid not in owners, (export, pid)
            owners[pid] = (command['step_id'], candidate['stamp'])
        matches.append(dict(step_id=command['step_id'], pid=candidate['pid'],
                            exec_time=candidate['stamp'], delivered_start=command['start'],
                            start_delivery_delay_s=command['start']-candidate['stamp'],
                            descendant_pids=len(found), truncated_prefix=candidate['truncated'],
                            command_sha256=hashlib.sha256(command['text'].encode()).hexdigest()))
    buffers = defaultdict(list)
    states = defaultdict(dict)
    effects = defaultdict(set)

    def flush(step):
        for effect in parse_strace_effects('\n'.join(buffers[step]), Path(data['workspace_root']), state=states[step]):
            effects[step].add((effect.path, effect.kind.value))
        buffers[step].clear()

    with trace.open(encoding='utf-8', errors='replace') as handle:
        for line in handle:
            match = LINE.match(line)
            if not match:
                continue
            owner = owners.get(int(match[1]))
            if owner is None or float(match[2]) < owner[1]:
                continue
            step = owner[0]
            buffers[step].append(f'{match[1]} {match[3]}')
            if len(buffers[step]) >= 10000:
                flush(step)
    for step in list(buffers):
        if buffers[step]:
            flush(step)
    added = 0
    recovered = {}
    for step in data['ledger']['steps']:
        sid = step['step_id']
        original = {(e['path'], e['kind']) for e in step['effects']}
        new = effects.get(sid, set())
        added += len(new - original)
        recovered[str(sid)] = [dict(path=p, kind=k) for p, k in sorted(new)]
    report = dict(schema='agenttx.codex_process_reads/v1', suite=data['suite'], task=data['task'],
                  trace_path=str(export), trace_sha256=row['trace_sha256'],
                  strace_path=str(trace), strace_sha256=sha.hexdigest(),
                  command_calls=len(commands), matched_commands=len(matches),
                  unmatched_step_ids=unmatched, new_read_effects=added,
                  matches=matches, reads_by_step=recovered)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, indent=2)+'\n')
    return {k: report[k] for k in ('suite', 'task', 'command_calls', 'matched_commands', 'unmatched_step_ids', 'new_read_effects')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--task')
    args = parser.parse_args()
    rows = [r for r in csv.DictReader(SOURCE.open()) if r['harness']=='codex' and r['trace_quality']=='complete'
            and (not args.task or r['task']==args.task)]
    OUT.mkdir(parents=True, exist_ok=True)
    reports=[]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for report in pool.map(recover, rows):
            reports.append(report)
            if len(reports) % 10 == 0:
                print(f'Recovered {len(reports)}/{len(rows)} Codex syscall traces', flush=True)
    (OUT / ('summary.json' if not args.task else 'sample-summary.json')).write_text(json.dumps(reports, indent=2)+'\n')
    print(json.dumps(dict(traces=len(reports), commands=sum(r['command_calls'] for r in reports),
                         matched=sum(r['matched_commands'] for r in reports),
                         unmatched=sum(len(r['unmatched_step_ids']) for r in reports),
                         added_effects=sum(r['new_read_effects'] for r in reports)), indent=2))


if __name__ == '__main__':
    main()
