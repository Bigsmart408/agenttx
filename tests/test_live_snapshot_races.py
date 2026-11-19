import errno
from pathlib import Path

import pytest

from agenttx import layers
from agenttx.layers import LayerStore
from agenttx.semisolate import SharedSemisolate, _iter_upper_entries


def test_upperdir_walk_handles_file_removed_after_scandir(tmp_path, monkeypatch):
    upper=tmp_path/'upper';upper.mkdir()
    removed=upper/'chromium-fetch';removed.write_text('temporary')
    kept=upper/'kept';kept.write_text('preserve')
    scandir=layers.os.scandir
    class DelayedScan:
        def __enter__(self):
            with scandir(upper) as scan:
                self.entries=list(scan)
            removed.unlink()
            return iter(self.entries)
        def __exit__(self,*args):pass
    monkeypatch.setattr(layers.os,'scandir',lambda path:DelayedScan())
    pool=object.__new__(SharedSemisolate);pool.sandbox_dir=tmp_path;pool.workspace=tmp_path
    # Walk used by ledger fingerprinting; losing a live temporary path must
    # not hide any surviving file or turn a deletion into an infrastructure exit.
    assert list(_iter_upper_entries(upper))==[kept]


def test_snapshot_retries_full_capture_without_publishing_partial_tree(tmp_path, monkeypatch):
    upper=tmp_path/'upper';upper.mkdir()
    kept=upper/'kept';kept.write_text('preserve')
    vanished=upper/'chromium-fetch';vanished.write_text('temporary')
    store=LayerStore(tmp_path/'layers')
    real=layers._copy_snapshot_entry
    visited=[]
    def racing_copy(source,*args,**kwargs):
        visited.append(source.name)
        if source==vanished and vanished.exists():
            vanished.unlink()
        return real(source,*args,**kwargs)
    monkeypatch.setattr(layers,'_copy_snapshot_entry',racing_copy)
    saved=store.snapshot_before(0,upper)
    assert (saved/'kept').read_text()=='preserve'
    assert not (saved/'chromium-fetch').exists()
    assert visited.count('chromium-fetch')==1
    assert list(store.root.glob('before_*'))==[saved]
    assert kept.read_text()=='preserve'


def test_snapshot_retries_incremental_capture_on_source_race(tmp_path, monkeypatch):
    upper=tmp_path/'upper';upper.mkdir()
    (upper/'kept').write_text('preserve')
    gone=upper/'gone';gone.write_text('before')
    store=LayerStore(tmp_path/'layers');store.snapshot_before(0,upper)
    gone.write_text('after')
    real=layers._copy_snapshot_entry
    def racing_copy(source,*args,**kwargs):
        if source==gone and gone.exists():gone.unlink()
        return real(source,*args,**kwargs)
    monkeypatch.setattr(layers,'_copy_snapshot_entry',racing_copy)
    saved=store.snapshot_before(1,upper,changed_paths=['/gone'])
    assert (saved/'kept').read_text()=='preserve'
    assert not (saved/'gone').exists()
    assert (store.resolve_before(0)/'gone').read_text()=='before'


def test_continuous_source_race_is_bounded_and_leaves_no_partial_snapshot(tmp_path, monkeypatch):
    upper=tmp_path/'upper';upper.mkdir();(upper/'kept').write_text('preserve')
    store=LayerStore(tmp_path/'layers');calls=[]
    real=layers._copy_snapshot_tree
    def unstable(*args,**kwargs):
        calls.append(1)
        real(*args,**kwargs)
        raise FileNotFoundError(errno.ENOENT,'source disappeared',str(upper/'unstable'))
    monkeypatch.setattr(layers,'_copy_snapshot_tree',unstable)
    with pytest.raises(FileNotFoundError):store.snapshot_before(0,upper)
    assert len(calls)==3
    assert not (store.root/'before_0000').exists()
    assert (upper/'kept').read_text()=='preserve'


def test_immutable_source_failure_not_retried_or_hidden(tmp_path, monkeypatch):
    upper=tmp_path/'upper';upper.mkdir();(upper/'kept').write_text('preserve')
    store=LayerStore(tmp_path/'layers');store.snapshot_before(0,upper)
    real=layers._clone_snapshot_tree;calls=[]
    def corrupted(source,destination):
        calls.append(1);real(source,destination)
        raise FileNotFoundError(errno.ENOENT,'immutable snapshot missing',str(source/'missing'))
    monkeypatch.setattr(layers,'_clone_snapshot_tree',corrupted)
    with pytest.raises(FileNotFoundError):store.snapshot_before(1,upper,changed_paths=['/kept'])
    assert len(calls)==1
    assert not (store.root/'before_0001').exists()
    assert (store.resolve_before(0)/'kept').read_text()=='preserve'


def test_non_enoent_errors_are_not_retried(tmp_path, monkeypatch):
    upper=tmp_path/'upper';upper.mkdir();store=LayerStore(tmp_path/'layers');calls=[]
    def denied(*args,**kwargs):
        calls.append(1)
        raise PermissionError(errno.EACCES,'denied',str(upper/'protected'))
    monkeypatch.setattr(layers,'_copy_snapshot_tree',denied)
    with pytest.raises(PermissionError):store.snapshot_before(0,upper)
    assert len(calls)==1
