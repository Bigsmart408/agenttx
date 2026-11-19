"""Exercise installer recovery without retrying solver errors or changing pins."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from experiments.scripts import tb_verifier_download_fallback as fallback
from experiments.scripts import run_tb_dual_local as runner


def invoke(tmp_path, mode, arguments='pip install pytest==8.4.1', index='https://mirrors.aliyun.com/pypi/simple', error='Caused by: operation timed out'):
    binary = tmp_path/'uv'
    binary.write_text('''#!/bin/bash
printf '%s %s\\n' "$UV_INDEX_URL" "$*" >> "$CALLS"
if [ "$MODE" = unsatisfiable ]; then echo 'No solution found' >&2; exit 3; fi
if [ "$MODE" = always ] || [ "$UV_INDEX_URL" = https://mirrors.aliyun.com/pypi/simple ]; then
 echo 'error: Failed to fetch: index' >&2
 printf '%s\n' "$NETWORK_ERROR" >&2
 exit 2
fi
echo installed
''')
    binary.chmod(0o755)
    code = fallback.SHELL
    code += '\nset -e\nbash -ec '+repr('uv '+arguments)+'\n'
    env = dict(os.environ, PATH=str(tmp_path)+':'+os.environ['PATH'], UV_INDEX_URL=index,
               MODE=mode, CALLS=str(tmp_path/'calls'), NETWORK_ERROR=error)
    result = subprocess.run(['bash','-ec',code],env=env,capture_output=True,text=True,timeout=10)
    calls = (tmp_path/'calls').read_text().splitlines()
    return result,calls,[json.loads(line) for line in result.stderr.splitlines() if line.startswith('{"contract":')]


def test_failed_domestic_download_retries_once_and_preserves_arguments(tmp_path):
    result,calls,logs=invoke(tmp_path,'recover')
    assert result.returncode==0,result.stderr
    assert calls==['https://mirrors.aliyun.com/pypi/simple pip install pytest==8.4.1',
                  'https://repo.huaweicloud.com/repository/pypi/simple pip install pytest==8.4.1']
    assert len(logs)==1 and logs[0]['prior_returncode']==2
    assert 'operation timed out' in result.stderr and result.stdout.strip()=='installed'


def test_uv_wheel_stream_network_timeout_retries_without_changing_packages(tmp_path):
    arguments='pip install pytest requests psutil easyocr==1.7.2'
    error='Failed to download distribution due to network timeout. Try increasing UV_HTTP_TIMEOUT (current value: 180s).'
    result,calls,logs=invoke(tmp_path,'recover',arguments,error=error)
    assert result.returncode==0,result.stderr
    assert calls==['https://mirrors.aliyun.com/pypi/simple '+arguments,
                  'https://repo.huaweicloud.com/repository/pypi/simple '+arguments]
    assert len(logs)==1 and logs[0]['contract']==fallback.CONTRACT
    assert error in result.stderr


def test_archive_failure_without_network_cause_does_not_retry(tmp_path):
    result,calls,logs=invoke(tmp_path,'recover',error='Failed to extract archive: invalid zip header')
    assert result.returncode==2 and len(calls)==1 and not logs


@pytest.mark.parametrize('arguments',[
    'pip install pytest==8.4.1 --index-url https://download.pytorch.org/whl/cpu',
    'pip install pytest==8.4.1 --offline',
    'run pytest /tests/test_outputs.py',
])
def test_explicit_indexes_offline_and_test_execution_are_never_retried(tmp_path,arguments):
    result,calls,logs=invoke(tmp_path,'always',arguments)
    assert result.returncode==2 and len(calls)==1 and not logs


def test_dependency_solver_failure_is_not_a_network_retry(tmp_path):
    result,calls,logs=invoke(tmp_path,'unsatisfiable')
    assert result.returncode==3 and len(calls)==1 and not logs


def test_network_retries_are_bounded_and_domestic_indexes_precede_official(tmp_path):
    result,calls,logs=invoke(tmp_path,'always')
    assert result.returncode==2 and len(calls)==3 and len(logs)==2
    assert logs[-1]['index']=='https://pypi.org/simple'


def test_custom_index_is_not_replaced(tmp_path):
    result,calls,logs=invoke(tmp_path,'always',index='https://custom.example/simple')
    assert result.returncode==2 and len(calls)==1 and not logs


def test_transport_contract_separates_result_configurations(tmp_path):
    old=runner.result_configuration({},tmp_path/'host-result.json')
    new=runner.result_configuration(dict(verifier_download_contract=fallback.CONTRACT),tmp_path/'host-result.json')
    assert runner.configuration_id(old)!=runner.configuration_id(new)


def test_slow_mirror_and_replacement_transport_histories_are_separate(tmp_path):
    old=runner.result_configuration(dict(verifier_download_contract='verifier-uv-network-error-domestic-index-fallback/v2'),tmp_path/'host-result.json')
    new=runner.result_configuration(dict(verifier_download_contract=fallback.CONTRACT),tmp_path/'host-result.json')
    assert runner.configuration_id(old)!=runner.configuration_id(new)
