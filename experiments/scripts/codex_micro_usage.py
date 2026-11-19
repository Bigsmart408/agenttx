"""Meter Codex Responses traffic without changing the recovery runtime."""
from __future__ import annotations

from contextlib import contextmanager
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shlex
import tempfile
import threading
import time
import tomllib
from urllib.parse import urlsplit


class ResponseMeter:
    def __init__(self, path, upstream, token_budget):
        self.path = Path(path)
        self.upstream = urlsplit(upstream)
        if self.upstream.scheme not in ('http', 'https'):
            raise ValueError('Responses upstream must use HTTP or HTTPS')
        self.lock = threading.RLock()
        self.serial = threading.Lock()
        self.state = dict(source='api_response_usage', token_budget=token_budget,
                          requests=[], complete=True, budget_exhausted=False)
        self.persist()

    def persist(self):
        with self.lock:
            rows = self.state['requests']
            known = [r for r in rows if isinstance(r.get('total_tokens'), int)]
            self.state['known_total_tokens'] = sum(r['total_tokens'] for r in known)
            self.state['total_tokens'] = self.state['known_total_tokens'] if self.state['complete'] else None
            self.state['prompt_tokens'] = sum(r['prompt_tokens'] for r in known)
            self.state['completion_tokens'] = sum(r['completion_tokens'] for r in known)
            self.state['records'] = len(known)
            budget = self.state['token_budget']
            self.state['budget_exceeded'] = budget is not None and self.state['known_total_tokens'] > budget
            tmp = self.path.with_suffix('.tmp')
            tmp.write_text(json.dumps(self.state, indent=2) + '\n')
            tmp.replace(self.path)

    def reserve(self, body):
        with self.lock:
            if not self.state['complete']:
                raise RuntimeError('An earlier API request has unknown usage')
            reserve = len(json.dumps({k: body[k] for k in ('input', 'tools') if k in body},
                                     ensure_ascii=False).encode()) + 16384
            maximum = min(int(body.get('max_output_tokens') or 32768), 32768)
            budget = self.state['token_budget']
            if budget is not None:
                allowance = budget - self.state['known_total_tokens'] - reserve
                if allowance <= 0:
                    self.state['budget_exhausted'] = True
                    self.persist()
                    raise RuntimeError('Registered API token budget exhausted')
                maximum = min(maximum, allowance)
            body['max_output_tokens'] = maximum
            row = dict(index=len(self.state['requests']), started_at=time.time(),
                       input_reservation=reserve, max_completion_tokens=maximum,
                       total_tokens=None)
            self.state['requests'].append(row)
            self.state['complete'] = False
            self.persist()
            return row

    def usage(self, row, event):
        response = event.get('response', event)
        usage = response.get('usage') if isinstance(response, dict) else None
        if not isinstance(usage, dict):
            return
        values = [usage.get(k) for k in ('input_tokens', 'output_tokens', 'total_tokens')]
        if not all(type(v) is int and v >= 0 for v in values):
            return
        if values[0] + values[1] != values[2]:
            return
        with self.lock:
            row.update(prompt_tokens=values[0], completion_tokens=values[1],
                       total_tokens=values[2], response_id=response.get('id'),
                       response_status=response.get('status'), finished_at=time.time())
            details = usage.get('input_tokens_details') or {}
            row['cached_input_tokens'] = details.get('cached_tokens', 0)
            self.state['complete'] = all(isinstance(r.get('total_tokens'), int)
                                         for r in self.state['requests'])
            self.persist()

    def handler(self):
        meter = self
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.0'

            def log_message(self, *_args):
                pass

            def reject(self, message):
                payload = json.dumps({'error': {'message': message, 'type': 'experiment_budget_guard'}}).encode()
                self.send_response(429)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self):
                with meter.serial:
                    row = None
                    sent = False
                    try:
                        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
                        if not self.path.rstrip('/').endswith('/responses'):
                            self.reject('Only Responses requests are registered')
                            return
                        try:
                            row = meter.reserve(body)
                        except RuntimeError as exc:
                            self.reject(str(exc))
                            return
                        encoded = json.dumps(body, ensure_ascii=False).encode()
                        headers = {k: v for k, v in self.headers.items()
                                   if k.lower() not in ('host', 'content-length', 'content-type',
                                       'connection', 'accept-encoding', 'transfer-encoding',
                                       'expect', 'proxy-connection', 'te', 'trailer',
                                       'upgrade', 'keep-alive')}
                        headers['Content-Type'] = 'application/json'
                        headers['Accept-Encoding'] = 'identity'
                        cls = http.client.HTTPSConnection if meter.upstream.scheme == 'https' else http.client.HTTPConnection
                        connection = cls(meter.upstream.hostname, meter.upstream.port, timeout=3600)
                        endpoint = meter.upstream.path.rstrip('/') + '/responses'
                        connection.request('POST', endpoint, body=encoded, headers=headers)
                        upstream = connection.getresponse()
                        row['http_status'] = upstream.status
                        self.send_response(upstream.status)
                        for k, v in upstream.getheaders():
                            if k.lower() not in ('connection', 'content-length', 'transfer-encoding', 'content-encoding'):
                                self.send_header(k, v)
                        self.send_header('Connection', 'close')
                        self.end_headers()
                        sent = True
                        disconnected = False
                        if 'text/event-stream' in upstream.getheader('Content-Type', ''):
                            data = []
                            while True:
                                line = upstream.readline()
                                if not line:
                                    break
                                if line.startswith(b'data:'):
                                    data.append(line[5:].strip())
                                elif not line.strip() and data:
                                    try:
                                        meter.usage(row, json.loads(b'\n'.join(data)))
                                    except (ValueError, TypeError):
                                        pass
                                    data = []
                                if not disconnected:
                                    try:
                                        self.wfile.write(line)
                                        self.wfile.flush()
                                    except (BrokenPipeError, ConnectionResetError):
                                        disconnected = True
                            if data:
                                try:
                                    meter.usage(row, json.loads(b'\n'.join(data)))
                                except (ValueError, TypeError):
                                    pass
                        else:
                            payload = upstream.read()
                            try:
                                meter.usage(row, json.loads(payload))
                            except (ValueError, TypeError):
                                pass
                            try:
                                self.wfile.write(payload)
                            except (BrokenPipeError, ConnectionResetError):
                                pass
                        connection.close()
                    except Exception as exc:
                        if row is not None:
                            row['error_type'] = type(exc).__name__
                        if not sent:
                            try:
                                self.reject('Metered upstream request failed; usage retained as unknown')
                            except (BrokenPipeError, ConnectionResetError):
                                pass
                    finally:
                        if row is not None:
                            row['finished_at'] = time.time()
                        meter.persist()
        return Handler


@contextmanager
def metered_codex(harness, path, token_budget):
    import os
    profile = tomllib.loads(Path(os.environ['CODEX_CONFIG_FILE']).read_text())
    provider = profile['model_provider']
    upstream = profile['model_providers'][provider]['base_url']
    meter = ResponseMeter(path, upstream, token_budget)
    server = ThreadingHTTPServer(('127.0.0.1', 0), meter.handler())
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original = harness.binary
    with tempfile.TemporaryDirectory(prefix='agenttx-codex-meter-') as folder:
        shim = Path(folder) / 'codex'
        override = f'model_providers.{provider}.base_url="http://127.0.0.1:{server.server_port}/v1"'
        # Current Codex parses top-level and `exec` config arrays separately;
        # the harness supplies exec-level -c flags, so inject here as well.
        shim.write_text('#!/bin/sh\nsubcommand=$1\nshift\nexec ' + shlex.quote(str(original)) +
                        ' "$subcommand" -c ' + shlex.quote(override) + ' "$@"\n')
        shim.chmod(0o700)
        harness.binary = str(shim)
        try:
            yield meter.state
        finally:
            harness.binary = original
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            meter.persist()
