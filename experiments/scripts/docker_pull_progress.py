"""Pull public images through the dedicated Docker API with measurable deadlines."""
import json
import os
from pathlib import Path
import selectors
import subprocess
import time
from urllib.parse import urlencode

IDLE_TIMEOUT_S = 900
WALL_TIMEOUT_S = 14400


def consume_pull(process, log, *, idle_timeout, wall_timeout, observe=None):
    started = last_progress = time.monotonic()
    maxima, milestones = {}, set()
    downloaded = extracted = 0
    last_event = None
    reason, error, complete = None, None, False
    buffer = b''
    last_notice = 0

    def snapshot():
        now = time.monotonic()
        return dict(wall_s=round(now-started, 3), idle_s=round(now-last_progress, 3),
                    downloaded_bytes=downloaded, extracted_bytes=extracted,
                    last_event=last_event, reason=reason, error=error)

    def event(raw):
        nonlocal last_progress, downloaded, extracted, last_event, error, complete, reason
        try:
            row = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            reason, error = 'invalid_stream', 'Docker returned invalid JSON'
            return
        if row.get('error') or row.get('errorDetail'):
            reason = 'registry_error'
            error = row.get('error') or str(row['errorDetail'])
            return
        status, layer = row.get('status', ''), row.get('id', '')
        last_event = dict(status=status, layer=layer)
        current = row.get('progressDetail', {}).get('current', 0)
        key = (layer, status)
        if status in ('Downloading', 'Extracting') and current > maxima.get(key, 0):
            delta = current-maxima.get(key, 0)
            maxima[key] = current
            if status == 'Downloading':
                downloaded += delta
            else:
                extracted += delta
            last_progress = time.monotonic()
        # Repeated Waiting/Retrying lines are not useful progress.
        if status in ('Download complete', 'Pull complete', 'Already exists', 'Verifying Checksum') and key not in milestones:
            milestones.add(key)
            last_progress = time.monotonic()
        terminal_status = status.removeprefix('Status: ')
        if terminal_status.startswith(('Downloaded newer image', 'Image is up to date')):
            complete = True
            last_progress = time.monotonic()

    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        while True:
            now = time.monotonic()
            if now-started >= wall_timeout:
                reason = 'wall_timeout'
                break
            if now-last_progress >= idle_timeout:
                reason = 'idle_timeout'
                break
            if observe and now-last_notice >= 10:
                observe(snapshot())
                last_notice = now
            ready = selector.select(min(1, idle_timeout-(now-last_progress), wall_timeout-(now-started)))
            if not ready:
                continue
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                if buffer.strip():
                    event(buffer)
                break
            log.write(chunk)
            log.flush()
            buffer += chunk
            while b'\n' in buffer:
                line, buffer = buffer.split(b'\n', 1)
                if line.strip():
                    event(line)
                if reason:
                    break
            if reason:
                break
        if reason:
            process.terminate()
        try:
            rc = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            rc = process.wait(timeout=5)
        if not reason:
            reason = 'complete' if rc == 0 and complete else 'transport_error' if rc else 'incomplete_stream'
        result = dict(snapshot(), returncode=0 if reason=='complete' else 124 if reason.endswith('_timeout') else 1,
                      transport_returncode=rc)
        if observe:
            observe(result)
        return result
    finally:
        selector.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()


def pull_image(image, log_path, *, env, observe=None,
               idle_timeout=IDLE_TIMEOUT_S, wall_timeout=WALL_TIMEOUT_S):
    host = env.get('DOCKER_HOST', '')
    if host != 'unix:///run/agenttx-docker/docker.sock':
        raise ValueError('Only the dedicated local Docker socket is allowed')
    url = 'http://localhost/v1.44/images/create?' + urlencode(dict(fromImage=image, platform='linux/amd64'))
    with Path(log_path).open('wb') as log, Path(str(log_path)+'.stderr').open('wb') as err:
        process = subprocess.Popen(['curl', '--silent', '--show-error', '--fail-with-body',
                                    '--no-buffer', '--noproxy', '*', '--unix-socket', host[7:],
                                    '--request', 'POST', url], env=env, stdout=subprocess.PIPE, stderr=err)
        result = consume_pull(process, log, idle_timeout=idle_timeout,
                              wall_timeout=wall_timeout, observe=observe)
        return dict(result, image=image, idle_timeout_s=idle_timeout, wall_timeout_s=wall_timeout)
