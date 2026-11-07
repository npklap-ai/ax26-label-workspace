"""Real local HTTP tests with an injected mock upstream (no model calls)."""
import http.client
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid

from agentstream_client import UpstreamError
from server import Config, create_server


class FakeClient:
    def __init__(self):
        self.calls = []
        self.started = threading.Event()
        self.health_status = 'healthy'
        self.mode = 'normal'

    def health(self, cancel):
        self.calls.append(('health',))
        return {'status': self.health_status, 'message': 'mock health',
                'checked_at': '2026-11-07T00:00:00Z', 'embedding_models': []}

    def create_session(self, query, cancel):
        self.calls.append(('create', query))
        return str(uuid.uuid4())

    def message(self, session, message, cancel):
        self.calls.append(('message', session, message))
        yield {'type': 'delta', 'text': '연동 검증용 대역 답변입니다.'}
        if self.mode == 'slow':
            self.started.set()
            cancel.event.wait(4)
            cancel.remaining('message')
        if self.mode == 'fail':
            raise UpstreamError('TURN_FAILED', 'message')
        yield {'type': 'evidence', 'data': {'sources': [{'title': '가상 시험 문서'}]}}
        yield {'type': 'completed'}


class HTTPIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fake = FakeClient()
        root = Path(__file__).resolve().parents[1]
        self.config = Config(root=root, port=0, private_dir=Path(self.tmp.name), api_key='test-only')
        self.server = create_server(self.config, client=self.fake)
        self.port = self.server.server_address[1]
        self.config.public_url = f'http://127.0.0.1:{self.port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.tmp.cleanup()

    def request(self, method, path, data=None, visitor=None, headers=None):
        h = {}
        if visitor:
            h.update({'Cookie': visitor['cookie'], 'X-CSRF-Token': visitor['csrfToken']})
        if method == 'POST':
            h.update({'Origin': self.config.origin, 'Content-Type': 'application/json'})
        h.update(headers or {})
        raw = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=6)
        connection.request(method, path, body=raw, headers=h)
        response = connection.getresponse()
        status, metadata, content = response.status, dict(response.getheaders()), response.read()
        connection.close()
        if 'application/x-ndjson' in metadata.get('Content-Type', ''):
            result = [json.loads(line) for line in content.splitlines() if line.strip()]
        elif 'application/json' in metadata.get('Content-Type', ''):
            result = json.loads(content)
        else:
            result = content
        return status, metadata, result

    def visitor(self, prefix=''):
        status, headers, result = self.request('GET', prefix + '/api/session')
        self.assertEqual(status, 200)
        self.assertTrue(result['connected'])
        return {**result, 'cookie': headers['Set-Cookie'].split(';')[0]}

    def turn(self, visitor, message='첫 질문', conversation=None, request_id=None):
        data = {'message': message, 'requestId': request_id or str(uuid.uuid4())}
        if conversation:
            data['conversationId'] = conversation
        return self.request('POST', '/api/chat', data, visitor)

    def test_first_followup_and_visitor_isolation(self):
        owner = self.visitor()
        status, _, events = self.turn(owner)
        self.assertEqual(status, 200)
        conversation = events[0]['conversationId']
        self.assertEqual(events[-1]['type'], 'completed')
        self.assertNotIn('session_id', json.dumps(events))
        status, _, next_events = self.turn(owner, '추가 질문', conversation)
        self.assertEqual(status, 200)
        self.assertEqual(next_events[0]['conversationId'], conversation)
        messages = [call for call in self.fake.calls if call[0] == 'message']
        self.assertEqual(messages[0][1], messages[1][1])
        self.assertEqual(len([call for call in self.fake.calls if call[0] == 'create']), 1)
        visitor_b = self.visitor()
        status, _, _ = self.turn(visitor_b, conversation=conversation)
        self.assertEqual(status, 404)
        self.assertEqual(len([call for call in self.fake.calls if call[0] == 'message']), 2)

    def test_csrf_origin_cookie_and_credential_rejection(self):
        owner = self.visitor()
        self.assertEqual(self.request('POST', '/api/health', {}, owner,
                                     {'X-CSRF-Token': 'wrong'})[0], 403)
        self.assertEqual(self.request('POST', '/api/health', {}, owner,
                                     {'Origin': 'https://unrelated.invalid'})[0], 403)
        self.assertEqual(self.request('POST', '/api/health', {})[0], 403)
        self.assertEqual(self.request('POST', '/api/chat', {'key': 'not-a-real-key'}, owner)[0], 400)
        self.assertEqual(self.request('POST', '/api/connect', {'key': 'not-a-real-key'}, owner)[0], 404)
        self.assertEqual(self.fake.calls, [])

    def test_static_allowlist_and_proxy_prefixes(self):
        for prefix in ['', '/ax26', '/i/Tfo3vbS3FbL3oe9y/ax26']:
            self.assertEqual(self.request('GET', prefix + '/')[0], 200)
            self.assertEqual(self.request('GET', prefix + '/app.js')[0], 200)
            self.assertEqual(self.request('GET', prefix + '/stream.js')[0], 200)
            for path in ['.env', 'server.py', '../README.md', '%2e%2e/session_store.py']:
                self.assertEqual(self.request('GET', prefix + '/' + path)[0], 404)

    def test_slashless_prefix_redirect_preserves_relative_assets(self):
        for prefix in ['/ax26', '/i/Tfo3vbS3FbL3oe9y/ax26']:
            status, headers, _ = self.request('GET', prefix)
            self.assertEqual(status, 308)
            self.assertEqual(headers['Location'], prefix + '/')

    def test_replay_no_second_upstream_post(self):
        visitor = self.visitor()
        request_id = str(uuid.uuid4())
        self.assertEqual(self.turn(visitor, request_id=request_id)[0], 200)
        self.assertEqual(self.turn(visitor, request_id=request_id)[0], 409)
        self.assertEqual(len([call for call in self.fake.calls if call[0] == 'create']), 1)

    def test_fail_keeps_partial_and_blocks_followup(self):
        visitor = self.visitor()
        self.fake.mode = 'fail'
        status, _, events = self.turn(visitor)
        self.assertEqual(status, 200)
        self.assertEqual(events[1]['type'], 'delta')
        self.assertEqual(events[-1]['type'], 'error')
        self.assertNotIn('completed', [e['type'] for e in events])
        self.assertEqual(self.turn(visitor, conversation=events[0]['conversationId'])[0], 409)

    def test_unknown_health_blocks_session_creation(self):
        self.fake.health_status = 'unknown'
        visitor = self.visitor()
        status, _, result = self.turn(visitor)
        self.assertEqual(status, 503)
        self.assertEqual(result['health']['status'], 'unknown')
        self.assertFalse(any(call[0] == 'create' for call in self.fake.calls))

    def test_cancel_before_request_blocks_race(self):
        visitor = self.visitor()
        request_id = str(uuid.uuid4())
        status, _, result = self.request('POST', '/api/cancel', {'requestId': request_id}, visitor)
        self.assertEqual(status, 200)
        self.assertTrue(result['cancelled'])
        self.assertEqual(self.turn(visitor, request_id=request_id)[0], 409)
        self.assertEqual(self.fake.calls, [])

    def test_cancel_active_request_preserves_partial_and_owner(self):
        self.fake.mode = 'slow'
        visitor = self.visitor()
        visitor_b = self.visitor()
        request_id = str(uuid.uuid4())
        result = []
        thread = threading.Thread(target=lambda: result.append(self.turn(visitor, request_id=request_id)))
        thread.start()
        self.assertTrue(self.fake.started.wait(2))
        self.assertEqual(self.request('POST', '/api/cancel', {'requestId': request_id}, visitor_b)[0], 404)
        self.assertEqual(self.request('POST', '/api/cancel', {'requestId': request_id}, visitor)[0], 200)
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0][2][1]['type'], 'delta')
        self.assertEqual(result[0][2][-1]['code'], 'CANCELLED')

    def test_live_smoke_script_against_mock_service(self):
        script = Path(__file__).resolve().parents[1] / 'scripts' / 'live_smoke.py'
        result = subprocess.run([sys.executable, str(script), self.config.public_url],
                                capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count('"completed": true'), 2)
        self.assertNotIn('연동 검증용 대역 답변입니다.', result.stdout)
        self.assertEqual(len([call for call in self.fake.calls if call[0] == 'create']), 1)


if __name__ == '__main__':
    unittest.main()
