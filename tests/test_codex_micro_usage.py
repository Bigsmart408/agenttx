"""Offline integration checks for billed usage and pre-request budget guards."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from experiments.scripts.codex_micro_usage import ResponseMeter, metered_codex


def exercise(tmp_path, payloads, budget=100000):
    seen = []
    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_POST(self):
            seen.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            seen[-1]['_test_content_types'] = self.headers.get_all('Content-Type', [])
            payload = payloads[len(seen)-1]
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            self.wfile.write(payload)
    upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
    meter = ResponseMeter(tmp_path / 'usage.json', f'http://127.0.0.1:{upstream.server_port}/v1', budget)
    server = ThreadingHTTPServer(('127.0.0.1', 0), meter.handler())
    for instance in (upstream, server):
        instance.daemon_threads = True
        threading.Thread(target=instance.serve_forever, daemon=True).start()
    def request():
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        connection.request('POST', '/v1/responses', json.dumps({'model': 'test', 'input': 'hello', 'stream': True}),
                           {'content-type': 'application/json'})
        response = connection.getresponse()
        status, body = response.status, response.read()
        connection.close()
        return status, body
    return meter, seen, request, (upstream, server)


def completed(input_tokens, output_tokens):
    event = {'type': 'response.completed', 'response': {'id': 'test', 'status': 'completed',
             'usage': {'input_tokens': input_tokens, 'output_tokens': output_tokens,
                       'total_tokens': input_tokens + output_tokens}}}
    return b'event: response.completed\ndata: ' + json.dumps(event).encode() + b'\n\n'


def close(servers):
    for server in servers:
        server.shutdown()
        server.server_close()


def test_streamed_provider_usage_and_output_bound(tmp_path):
    event = completed(120, 20)
    meter, seen, request, servers = exercise(tmp_path, [event + event])
    try:
        status, body = request()
        assert status == 200 and body == event + event
        assert seen[0]['max_output_tokens'] == 32768
        assert seen[0]['_test_content_types'] == ['application/json']
        assert meter.state['complete'] and meter.state['total_tokens'] == 140
        assert meter.state['records'] == 1
        assert json.loads((tmp_path / 'usage.json').read_text())['total_tokens'] == 140
    finally:
        close(servers)


def test_exhausted_budget_blocks_before_upstream(tmp_path):
    meter, seen, request, servers = exercise(tmp_path, [completed(19000, 500)], budget=20000)
    try:
        assert request()[0] == 200
        assert 0 < seen[0]['max_output_tokens'] < 3616
        assert request()[0] == 429
        assert len(seen) == 1
        assert meter.state['budget_exhausted'] and meter.state['complete']
        assert meter.state['total_tokens'] == 19500
    finally:
        close(servers)


def test_missing_usage_stays_unknown_and_blocks_further_spend(tmp_path):
    meter, seen, request, servers = exercise(tmp_path, [b'event: error\ndata: {"error":"lost"}\n\n'])
    try:
        assert request()[0] == 200
        assert not meter.state['complete'] and meter.state['total_tokens'] is None
        assert request()[0] == 429
        assert len(seen) == 1
    finally:
        close(servers)


def test_native_codex_exec_config_routes_through_meter(tmp_path, monkeypatch):
    binary = shutil.which('codex')
    if not binary:
        pytest.skip('Native Codex CLI is not installed')
    seen = []
    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_POST(self):
            seen.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            payload = b'{"error":{"message":"offline test","type":"invalid_request_error"}}'
            self.send_response(400)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
    upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    profile = tmp_path / 'config.toml'
    profile.write_text('model="offline"\nmodel_provider="offline"\n'
                       '[model_providers.offline]\nname="offline"\nwire_api="responses"\n'
                       f'base_url="http://127.0.0.1:{upstream.server_port}/v1"\n')
    monkeypatch.setenv('CODEX_CONFIG_FILE', str(profile))
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    for key in ('CODEX_API_KEY', 'OPENAI_API_KEY'):
        monkeypatch.delenv(key, raising=False)
    harness = SimpleNamespace(binary=binary)
    try:
        with metered_codex(harness, tmp_path / 'usage.json', 100000) as usage:
            result = subprocess.run([harness.binary, 'exec', '-c', 'features.plugins=false',
                '--skip-git-repo-check', '--dangerously-bypass-approvals-and-sandbox',
                '--json', 'Say OK.'], cwd=tmp_path, env=os.environ.copy(), input='',
                capture_output=True, text=True, timeout=25)
        assert result.returncode != 0  # Deliberate local HTTP 400, no model call.
        assert len(seen) == len(usage['requests']) == 1
        assert seen[0]['max_output_tokens'] == 32768
        assert usage['requests'][0]['http_status'] == 400
    finally:
        upstream.shutdown()
        upstream.server_close()
