"""Bounded public-registry checks on the actual blocked SWE image, without pulls."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request

ACCEPT = ', '.join(['application/vnd.oci.image.index.v1+json', 'application/vnd.oci.image.manifest.v1+json',
                    'application/vnd.docker.distribution.manifest.list.v2+json', 'application/vnd.docker.distribution.manifest.v2+json'])
REPO = 'swebench/sweb.eval.x86_64.matplotlib_1776_matplotlib-24149'
HOSTS = ['docker.1ms.run', 'docker.1panel.live', 'docker.m.daocloud.io', 'docker.xuanyuan.me',
         'dockerproxy.net', 'docker.1panelproxy.com', 'hub.rat.dev', 'dockerhub.icu', 'docker.amingg.com', 'dockerpull.cn']


def check(host):
    started = time.monotonic()
    row = dict(host=host, repository=REPO)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    token = None

    def fetch(path, headers=None):
        nonlocal token
        request_headers = {'Accept': ACCEPT, 'User-Agent': 'agenttx-local-registry-check', **(headers or {})}
        if token:
            request_headers['Authorization'] = 'Bearer ' + token
        url = 'https://' + host + path
        try:
            return opener.open(urllib.request.Request(url, headers=request_headers), timeout=8)
        except urllib.error.HTTPError as exc:
            challenge = exc.headers.get('WWW-Authenticate', '')
            if exc.code != 401 or not challenge.lower().startswith('bearer'):
                raise
            fields = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
            realm = fields['realm']
            query = urllib.parse.urlencode({k: fields[k] for k in ('service', 'scope') if k in fields})
            with opener.open(realm + ('&' if '?' in realm else '?') + query, timeout=8) as response:
                body = json.load(response)
            token = body.get('token') or body['access_token']
            request_headers['Authorization'] = 'Bearer ' + token
            return opener.open(urllib.request.Request(url, headers=request_headers), timeout=8)

    try:
        with fetch('/v2/' + REPO + '/manifests/latest') as response:
            raw = response.read(2*1024**2)
            manifest = json.loads(raw)
        if 'manifests' in manifest:
            selected = next(r for r in manifest['manifests'] if r.get('platform', {}).get('os') == 'linux' and r['platform'].get('architecture') == 'amd64')
            with fetch('/v2/' + REPO + '/manifests/' + selected['digest']) as response:
                raw = response.read(2*1024**2)
                assert 'sha256:' + hashlib.sha256(raw).hexdigest() == selected['digest']
                manifest = json.loads(raw)
        row['manifest_digest'] = 'sha256:' + hashlib.sha256(raw).hexdigest()
        row['config_digest'] = manifest['config']['digest']
        with fetch('/v2/' + REPO + '/blobs/' + manifest['config']['digest']) as response:
            raw = response.read(2*1024**2)
            assert 'sha256:' + hashlib.sha256(raw).hexdigest() == manifest['config']['digest']
        row['config_verified'] = True
        layer = max(manifest['layers'], key=lambda r: r['size'])
        row['largest_layer_bytes'] = layer['size']
        started_blob = time.monotonic()
        with fetch('/v2/' + REPO + '/blobs/' + layer['digest'], {'Range': 'bytes=0-1048575'}) as response:
            raw = response.read(1024**2)
            row.update(layer_http_status=response.status, sample_bytes=len(raw))
        row['sample_wall_s'] = round(time.monotonic() - started_blob, 3)
        row['sample_MiB_s'] = round(len(raw) / 1024**2 / max(time.monotonic()-started_blob, .001), 3)
        row['layer_reachable'] = len(raw) > 0
    except Exception as exc:
        row.update(error=type(exc).__name__ + ': ' + str(exc)[:200])
    row['wall_s'] = round(time.monotonic() - started, 3)
    print(json.dumps(row), flush=True)
    return row


def main():
    with ThreadPoolExecutor(max_workers=5) as pool:
        rows = list(pool.map(check, HOSTS))
    out = Path('/mnt/data/agenttx-runtime/validation/mirror-recheck-20261017/probes.json')
    out.write_text(json.dumps(dict(checked_at=datetime.now(timezone.utc).isoformat(), rows=rows), indent=2)+'\n')


if __name__ == '__main__':
    main()
