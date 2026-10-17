"""Exercise streamed pulls against a real subprocess, without a registry."""
import json
import subprocess
import sys

from experiments.scripts.docker_pull_progress import consume_pull


def run_stream(tmp_path, events, *, idle=.25, wall=3):
    script = '''import json, sys, time
for delay, event in json.loads(sys.argv[1]):
    time.sleep(delay)
    print(json.dumps(event), flush=True)
'''
    process = subprocess.Popen([sys.executable, '-u', '-c', script, json.dumps(events)], stdout=subprocess.PIPE)
    notices = []
    with (tmp_path/'pull.log').open('wb') as log:
        result = consume_pull(process, log, idle_timeout=idle, wall_timeout=wall, observe=notices.append)
    assert process.poll() is not None
    assert notices[-1] == result
    return result


def progress(current, status='Downloading'):
    return dict(id='layer', status=status, progressDetail=dict(current=current, total=1000))


def test_active_download_can_outlast_idle_window(tmp_path):
    events = [(0, progress(1))]+[(.08, progress(i)) for i in range(2, 10)]
    events += [(0, dict(status='Status: Downloaded newer image for test'))]
    r = run_stream(tmp_path, events)
    assert r['reason'] == 'complete' and r['returncode'] == 0
    assert r['wall_s'] > .5 and r['downloaded_bytes'] == 9


def test_waiting_and_duplicate_bytes_do_not_hide_stall(tmp_path):
    events = [(0, progress(10))]
    events += [(.06, progress(10)), (.06, dict(status='Waiting', id='layer'))]*6
    r = run_stream(tmp_path, events)
    assert r['reason'] == 'idle_timeout' and r['returncode'] == 124
    assert r['downloaded_bytes'] == 10 and r['wall_s'] < .7


def test_extraction_counts_as_progress(tmp_path):
    events = [(0, progress(100))]+[(.08, progress(i, 'Extracting')) for i in range(1, 8)]
    events += [(0, dict(status='Status: Image is up to date for test'))]
    r = run_stream(tmp_path, events)
    assert r['reason'] == 'complete' and r['extracted_bytes'] == 7


def test_active_download_still_has_total_deadline(tmp_path):
    r = run_stream(tmp_path, [(.06, progress(i)) for i in range(1, 30)], wall=.45)
    assert r['reason'] == 'wall_timeout' and r['downloaded_bytes'] > 1


def test_json_registry_error_is_failure_even_with_exit_zero(tmp_path):
    r = run_stream(tmp_path, [(0, dict(error='registry unavailable', errorDetail=dict(message='unavailable')))])
    assert r['reason'] == 'registry_error' and r['returncode'] == 1
    assert r['error'] == 'registry unavailable'


def test_clean_eof_without_success_is_failure(tmp_path):
    r = run_stream(tmp_path, [(0, progress(100))])
    assert r['reason'] == 'incomplete_stream' and r['returncode'] == 1


def test_layer_completion_is_progress(tmp_path):
    r = run_stream(tmp_path, [(0, progress(10)), (.18, dict(id='layer', status='Download complete')),
                              (.18, dict(status='Downloaded newer image for test'))])
    assert r['reason'] == 'complete'
