from pathlib import Path
import pytest
from experiments.workloads.terminal_bench_suite import prepare_official_test_script


def binary(path,version):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text('#!/bin/sh\nprintf "uv '+version+'\\n"\n');path.chmod(0o755)


def test_explicit_uv_version_is_not_replaced_by_incompatible_cached_version(tmp_path,monkeypatch):
    cache=tmp_path/'cache';source=tmp_path/'task';source.mkdir()
    script='curl -LsSf https://astral.sh/uv/0.8.5/install.sh | sh\nsource $HOME/.local/bin/env\nuv pip install pytest==8.3.5\nuv run pytest tests/test_outputs.py\n'
    (source/'run-tests.sh').write_text(script)
    binary(cache/'terminal_bench/uv/installed/bin/uv','0.7.13')
    correct=cache/'terminal_bench/uv/0.8.5/bin/uv';binary(correct,'0.8.5')
    monkeypatch.setenv('AGENTTX_DOMESTIC_ONLY','1')
    effective,selected=prepare_official_test_script(source,cache)
    try:
        assert selected==correct
        text=effective.read_text()
        assert 'astral.sh' not in text and 'pytest==8.3.5' in text
        assert 'uv run pytest tests/test_outputs.py' in text
        assert (source/'run-tests.sh').read_text()==script
    finally:effective.unlink()


def test_domestic_only_bootstrap_fails_before_models_if_pinned_uv_missing(tmp_path,monkeypatch):
    source=tmp_path/'task';source.mkdir()
    (source/'run-tests.sh').write_text('curl -LsSf https://astral.sh/uv/0.8.5/install.sh | sh')
    binary(tmp_path/'cache/terminal_bench/uv/installed/bin/uv','0.7.13')
    monkeypatch.setenv('AGENTTX_DOMESTIC_ONLY','1')
    with pytest.raises(FileNotFoundError,match='0.8.5'):
        prepare_official_test_script(source,tmp_path/'cache')


def test_mobilesam_local_source_keeps_dependency_pins_and_test_command():
    from experiments.scripts.validate_tb_bootstrap_batch import effective_script
    script=('uv pip install "torch==2.5.1+cpu" '
            '"git+https://github.com/ChaoningZhang/MobileSAM.git@${COMMIT_HASH}"\n'
            'curl -L -o mobile_sam.pt https://github.com/ChaoningZhang/MobileSAM/raw/master/weights/mobile_sam.pt\n'
            '# Copy test data\nuv run pytest /tests/test_outputs.py -rA\n')
    text,_=effective_script(script,'sam-cell-seg','https://gh-proxy.com/',True)
    assert '"torch==2.5.1+cpu"' in text
    assert 'uv run pytest /tests/test_outputs.py -rA' in text
    assert '"/var/tmp/agenttx-mobilesam-source"' in text
    assert text.index('sha256sum')<text.index('uv run pytest')


def test_c4_transport_preserves_test_logic_and_rejects_unexpected_source():
    import ast
    from experiments.scripts.validate_tb_bootstrap_batch import c4_local_test_transport
    original='''def test_data():
    data = load_dataset("allenai/c4", data_files={"train": "en/c4-train.00009-of-01024.json.gz"})
    assert len(data["train"]) > 10000
    assert evaluate(data) == expected
'''
    effective=c4_local_test_transport(original)
    before=ast.parse(original);after=ast.parse(effective)
    # Undo only the two transport constants: the entire AST must then match.
    replacements={'json':'allenai/c4',
        '/results/cache/verifier-assets/c4-heldout-00009.json.gz':'en/c4-train.00009-of-01024.json.gz'}
    for node in ast.walk(after):
        if isinstance(node,ast.Constant) and isinstance(node.value,str):
            node.value=replacements.get(node.value,node.value)
    assert ast.dump(before)==ast.dump(after)
    with pytest.raises(AssertionError):c4_local_test_transport(original.replace('allenai/c4','other/data'))


def test_python36_compatibility_is_task_specific_and_keeps_original_interpreter():
    from experiments.scripts.validate_tb_bootstrap_batch import effective_script
    original='pip3 install pytest==8.4.1\nCUDA_VISIBLE_DEVICES="" python3 -m pytest $TEST_DIR/test_outputs.py -rA'
    effective,_=effective_script(original,'accelerate-maximal-square')
    assert effective==original.replace('pytest==8.4.1','pytest==6.2.5')
    assert effective_script(original,'other-task')[0]==original


@pytest.mark.parametrize('task',['qemu-startup','qemu-alpine-ssh'])
def test_qemu_adds_missing_distutils_without_changing_test_command(task):
    from experiments.scripts.validate_tb_bootstrap_batch import effective_script
    original='apt-get install -y curl\nuv venv\nuv pip install pytest==8.4.1\nuv run pytest /tests/test_outputs.py -rA'
    effective,_=effective_script(original,task)
    assert effective==original.replace('apt-get install -y curl','apt-get install -y python3-distutils curl')


def test_lc0_cpu_mirror_keeps_original_versions_and_cpu_variant():
    from experiments.scripts.validate_tb_bootstrap_batch import effective_script
    original='uv pip install torch==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cpu\nuv pip install onnx==1.18.0\n'
    effective,_=effective_script(original,'leelachess0-pytorch-conversion')
    assert effective==original.replace('https://download.pytorch.org/whl/cpu','https://mirrors.nju.edu.cn/pytorch/whl/cpu')
    offline,_=effective_script(original,'leelachess0-pytorch-conversion',lc0_wheels=True)
    assert '/probe/cpu-wheels/torch-2.8.0+cpu-cp313-cp313-manylinux_2_28_x86_64.whl' in offline
    assert '/probe/cpu-wheels/torchvision-0.23.0+cpu-cp313-cp313-manylinux_2_28_x86_64.whl' in offline
    assert 'uv pip install onnx==1.18.0' in offline


def test_pinned_source_verification_rejects_tampering_and_extra_files(tmp_path):
    from experiments.scripts.repair_tb_mobilesam_transport import verify_source,blob_hash
    source=tmp_path/'source';source.mkdir()
    (source/'setup.py').write_bytes(b'original')
    (source/'link').symlink_to('setup.py')
    tree=dict(truncated=False,tree=[dict(path='setup.py',type='blob',mode='100644',sha=blob_hash(b'original')),
                                   dict(path='link',type='blob',mode='120000',sha=blob_hash(b'setup.py'))])
    assert verify_source(source,tree)==2
    (source/'setup.py').write_bytes(b'changed')
    with pytest.raises(AssertionError,match='Git blob mismatch'):verify_source(source,tree)
    (source/'setup.py').write_bytes(b'original')
    (source/'unexpected').touch()
    with pytest.raises(AssertionError,match='inventory mismatch'):verify_source(source,tree)


@pytest.mark.parametrize('kind',['batch','native'])
def test_explicit_package_index_wins_over_domestic_default(tmp_path,monkeypatch,kind):
    import http.server
    import os
    import subprocess
    import sys
    import threading
    uv=Path('/mnt/data/agenttx-runtime/validation/tb-validation-tools-20261018/bin/uv')
    if not uv.exists():pytest.skip('Local pinned uv integration tool unavailable')
    if kind=='batch':
        from experiments.scripts.validate_tb_bootstrap_batch import SETUP
        text=SETUP
    else:
        source=tmp_path/'task';source.mkdir()
        (source/'run-tests.sh').write_text('curl -LsSf https://astral.sh/uv/0.7.13/install.sh | sh\n')
        cache=tmp_path/'cache';binary(cache/'terminal_bench/uv/installed/bin/uv','0.7.13')
        monkeypatch.setenv('AGENTTX_DOMESTIC_ONLY','1')
        effective,_=prepare_official_test_script(source,cache)
        try:text=effective.read_text()
        finally:effective.unlink()
    seen=[]
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path);self.send_response(200)
            self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(b'<html></html>')
        def log_message(self,*args):pass
    server=http.server.HTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base='http://127.0.0.1:'+str(server.server_port)
    env=os.environ.copy();env['UV_DEFAULT_INDEX']=base+'/inherited'
    env['AGENTTX_TEST_UV_CACHE']=str(tmp_path/'uv-cache')
    exports='\n'.join(line for line in text.splitlines() if line.startswith(('export UV_','unset UV_')))
    exports=exports.replace('https://pypi.tuna.tsinghua.edu.cn/simple',base+'/mirror')
    try:
        result=subprocess.run(['bash','-c',exports+'\nexport UV_CACHE_DIR="$AGENTTX_TEST_UV_CACHE"\nexec "$@"','index-check',str(uv),'pip','install',
            '--dry-run','--python',sys.executable,'--index-url',base+'/explicit',
            'agenttx-no-such-package==1.0'],env=env,capture_output=True,text=True,timeout=15)
        assert result.returncode!=0
        assert seen==['/explicit/agenttx-no-such-package/'],result.stderr
    finally:server.shutdown();server.server_close();thread.join()
