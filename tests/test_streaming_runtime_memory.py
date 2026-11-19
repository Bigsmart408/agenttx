import hashlib
import os
from pathlib import Path

from agenttx.ledger import Effect, EffectKind
from agenttx.semisolate import SharedSemisolate, _hash_regular_preserving_mode


def test_trace_reader_bounds_reads_deduplicates_and_keeps_partial_line(tmp_path, monkeypatch):
    path=tmp_path/'trace.log'
    line=b'10 openat(AT_FDCWD, "input", O_RDONLY) = 3\n'
    partial=b'10 openat(AT_FDCWD, "later", O_RDONLY)'
    with path.open('wb') as stream:
        for _ in range(20000):stream.write(line)
        stream.write(partial)
    pool=object.__new__(SharedSemisolate)
    pool.workspace=tmp_path;pool._worker_strace_path=path;pool._worker_strace_offset=0;pool._worker_strace_state={}
    original=Path.open;reads=[]
    class GuardedFile:
        def __init__(self,stream):self.stream=stream
        def __enter__(self):return self
        def __exit__(self,*args):return self.stream.__exit__(*args)
        def __getattr__(self,name):return getattr(self.stream,name)
        def read(self,size=-1):
            assert 0<size<=256*1024
            reads.append(size)
            return self.stream.read(size)
    def guarded_open(source,*args,**kwargs):
        stream=original(source,*args,**kwargs)
        return GuardedFile(stream) if source==path and args==('rb',) else stream
    monkeypatch.setattr(Path,'open',guarded_open)
    assert pool._read_worker_strace()==[Effect(str(tmp_path/'input'),EffectKind.READ)]
    assert len(reads)>1 and pool._worker_strace_offset==len(line)*20000
    with path.open('ab') as stream:stream.write(b' = 4\n')
    assert pool._read_worker_strace()==[Effect(str(tmp_path/'later'),EffectKind.READ)]
    assert not pool._read_worker_strace()
    assert pool._worker_strace_offset==path.stat().st_size


def test_streamed_trace_preserves_child_cwd_across_batches(tmp_path):
    path=tmp_path/'trace.log'
    padding=b'1 stat("outside", {}) = -1 ENOENT (No such file)\n'
    with path.open('wb') as stream:
        stream.write(b'10 chdir("sub") = 0\n10 clone(child_stack=NULL, flags=SIGCHLD) = 11\n')
        for _ in range(6000):stream.write(padding)
        stream.write(b'11 openat(AT_FDCWD, "read", O_RDONLY <unfinished ...>\n')
    pool=object.__new__(SharedSemisolate);pool.workspace=tmp_path
    pool._worker_strace_path=path;pool._worker_strace_offset=0;pool._worker_strace_state={}
    pool._read_worker_strace()
    with path.open('ab') as stream:stream.write(b'11 <... openat resumed>) = 4\n')
    assert pool._read_worker_strace()==[Effect(str(tmp_path/'sub/read'),EffectKind.READ)]


def test_regular_file_hash_is_identical_and_restores_read_permission(tmp_path, monkeypatch):
    path=tmp_path/'large';payload=b'abcd'*(1024*1024)
    path.write_bytes(payload);path.chmod(0)
    prefix=b'file\0metadata\0';expected=hashlib.sha256(prefix+payload).hexdigest()
    def forbidden(*args,**kwargs):raise AssertionError('unbounded Path.read_bytes')
    monkeypatch.setattr(Path,'read_bytes',forbidden)
    assert _hash_regular_preserving_mode(path,path.stat().st_mode,prefix)==expected
    assert path.stat().st_mode&0o777==0
