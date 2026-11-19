"""Direct publication must retain filtering, errors and custom backends."""
from pathlib import Path
import subprocess

from agenttx import semisolate
from agenttx.semisolate import SharedSemisolate


def pool(tmp_path):
    result = object.__new__(SharedSemisolate)
    result.sandbox_dir = tmp_path/'sandbox'
    result.sandbox_dir.mkdir()
    result.workspace = tmp_path
    result.try_bin = Path(semisolate.__file__).resolve().parents[2]/'scripts/try-wrapper.sh'
    return result


def test_native_commit_keeps_exact_filters_and_failure(tmp_path,monkeypatch):
    instance = pool(tmp_path)
    monkeypatch.setattr(semisolate.shutil,'which',lambda name:'/usr/bin/try-commit')
    calls=[]
    def execute(argv,**kwargs):
        calls.append(argv)
        assert argv[0]=='/usr/bin/try-commit'
        assert Path(argv[argv.index('-E')+1]).read_text()=='/run/mount\n/run/try_trace.log\n'
        include=Path(argv[argv.index('-I')+1]).read_text().splitlines()
        assert include==instance._include_patterns(['/app/path with spaces/file.txt'])
        return subprocess.CompletedProcess(argv,1,'partial publication','failed')
    monkeypatch.setattr(semisolate.subprocess,'run',execute)
    result=instance._run_storage_commit(['/app/path with spaces/file.txt'])
    assert result.returncode==1
    assert len(calls)==1
    assert not list(instance.sandbox_dir.iterdir())


def test_custom_try_command_is_retained(tmp_path,monkeypatch):
    instance=pool(tmp_path)
    instance.try_bin=tmp_path/'custom-try'
    monkeypatch.setattr(semisolate.shutil,'which',lambda name:'/usr/bin/try-commit')
    seen=[]
    def execute(argv,**kwargs):
        seen.append(argv)
        assert kwargs['input']=='y\n'
        return subprocess.CompletedProcess(argv,0,'','')
    monkeypatch.setattr(semisolate.subprocess,'run',execute)
    instance._run_storage_commit(None)
    assert seen==[[str(instance.try_bin),'commit',str(instance.sandbox_dir)]]


def test_missing_native_helper_keeps_shell_fallback(tmp_path,monkeypatch):
    instance=pool(tmp_path)
    monkeypatch.setattr(semisolate.shutil,'which',lambda name:None)
    seen=[]
    def execute(argv,**kwargs):
        seen.append(argv)
        return subprocess.CompletedProcess(argv,0,'','')
    monkeypatch.setattr(semisolate.subprocess,'run',execute)
    instance._run_storage_commit(['/app/file'])
    assert seen[0][0]==str(instance.try_bin)
    assert seen[0][-2:]==['commit',str(instance.sandbox_dir)]
