"""Static site and AgentStream proxy with encrypted browser sessions."""
import json
import re
import time
from http.cookies import SimpleCookie
from session_store import get_store, TTL
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = '/home/ubuntu/ax26-web'
ENDPOINT = 'https://api-gateway.jntp-ax.or.kr/agentstream/open/v1/chatbots/5a09d748-53e8-4260-9d24-5a64e5e52960/health'
class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)
    def session_token(self):
        cookie = SimpleCookie()
        try: cookie.load(self.headers.get('Cookie', ''))
        except Exception: return None
        return cookie['ax26_session'].value if 'ax26_session' in cookie else None
    def do_GET(self):
        if self.path.rstrip('/') in ['/ax26/api/session', '/i/Tfo3vbS3FbL3oe9y/ax26/api/session']:
            shared = get_store().shared_key()
            if shared: return self.reply(200, {'connected': True, 'mode': 'shared', 'expiresAt': None})
            record = get_store().get(self.session_token())
            return self.reply(200, {'connected': bool(record), 'expiresAt': record[1] if record else None})
        return super().do_GET()
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        if getattr(self, 'pending_cookie', None):
            self.send_header('Set-Cookie', self.pending_cookie)
            self.pending_cookie = None
        super().end_headers()
    def log_message(self, *args):
        pass
    def reply(self, status, data):
        payload = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
    def do_POST(self):
        if self.path.rstrip('/') not in [prefix + route for prefix in ['/ax26/api/', '/i/Tfo3vbS3FbL3oe9y/ax26/api/'] for route in ['health', 'chat', 'connect', 'disconnect']]:
            return self.reply(404, {'message': '요청 경로를 찾을 수 없습니다.'})
        if self.headers.get('Origin') != 'https://devopen.jntp-ax.or.kr':
            return self.reply(403, {'message': '허용되지 않은 요청입니다.'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 262144: raise ValueError()
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict): raise ValueError()
            shared = get_store().shared_key()
            if shared and self.path.rstrip('/').endswith(('/connect', '/disconnect')):
                return self.reply(403, {'message': '서버 공용 연결은 서버 관리자만 변경할 수 있습니다.'})
            if self.path.rstrip('/').endswith('/disconnect'):
                get_store().delete(self.session_token())
                self.pending_cookie = 'ax26_session=; Path=/i/Tfo3vbS3FbL3oe9y/ax26/; Max-Age=0; HttpOnly; Secure; SameSite=Strict'
                return self.reply(200, {'connected': False, 'message': '이 브라우저의 API 연결을 해제했습니다.'})
            record = get_store().get(self.session_token())
            key = shared or data.get('key') or (record[0] if record else '')
            if not key: return self.reply(401, {'message': '저장된 연결이 없거나 만료되었습니다. API 키를 한 번 등록해 주세요.'})
            if not isinstance(key, str) or not key or not key.isascii() or any(c.isspace() or ord(c) < 33 for c in key): raise ValueError()
        except (ValueError, TypeError, AttributeError):
            return self.reply(400, {'message': 'API 키 입력 형식을 확인하세요.'})
        if self.path.rstrip('/').endswith('/chat'):
            return self.chat(key, data)
        try:
            request = Request(ENDPOINT, headers={'X-API-Key': key, 'Accept': 'application/json'})
            with urlopen(request, timeout=20) as response:
                result = json.loads(response.read(65536))
            state = result.get('status', 'unknown') if isinstance(result, dict) else 'unknown'
            if state not in ['healthy', 'unhealthy', 'unknown']: state = 'unknown'
            if self.path.rstrip('/').endswith('/connect'):
                # Any successful authorized health response authenticates the key even during model outages.
                token, expires = get_store().save(key, self.session_token())
                self.pending_cookie = f'ax26_session={token}; Path=/i/Tfo3vbS3FbL3oe9y/ax26/; Max-Age={TTL}; HttpOnly; Secure; SameSite=Strict'
                return self.reply(200, {'connected': True, 'expiresAt': expires, 'status': state, 'message': 'API 연결을 저장했습니다. 같은 브라우저에서 30일 동안 유지됩니다.'})
            self.reply(200, {'status': state, 'message': {'healthy': '키 인증 및 챗봇 상태 확인 성공. 챗봇이 정상 상태입니다.', 'unhealthy': 'API 요청은 수락되었지만 챗봇이 비정상 상태입니다. AgentStream 설정을 확인하세요.', 'unknown': 'API 요청은 수락되었지만 챗봇 상태를 확인할 수 없습니다.'}[state]})
        except HTTPError as error:
            messages = {401: '키 인증 실패: 발급된 키를 확인하세요.', 403: '접근 권한 없음: 키 권한과 챗봇 공개·매핑 설정을 확인하세요.', 404: '챗봇 또는 API 경로를 찾을 수 없습니다.', 429: '요청 제한에 도달했습니다. 잠시 후 다시 시도하세요.'}
            self.reply(502, {'upstreamStatus': error.code, 'message': messages.get(error.code, f'AgentStream 응답 오류 (HTTP {error.code}).')})
        except (URLError, TimeoutError):
            self.reply(504, {'message': 'AgentStream 연결 시간이 초과되었거나 네트워크 연결에 실패했습니다.'})
        except (ValueError, OSError):
            self.reply(502, {'message': 'AgentStream 응답 형식을 확인하지 못했습니다.'})

    def chat(self, key, data):
        message = data.get('message')
        if not isinstance(message, str) or not message.strip() or len(message.encode('utf-8')) > 200000:
            return self.reply(400, {'message': '요청 내용은 1~200,000바이트로 입력하세요.'})
        started = False
        def emit(event):
            self.wfile.write((json.dumps(event, ensure_ascii=False) + '\n').encode())
            self.wfile.flush()
        def call(url, body=None):
            headers = {'X-API-Key': key, 'Accept': 'application/json, application/x-ndjson'}
            raw = None
            if body is not None:
                raw = json.dumps(body).encode(); headers['Content-Type'] = 'application/json'
            return urlopen(Request(url, data=raw, headers=headers), timeout=120)
        try:
            with call(ENDPOINT) as response:
                health = json.loads(response.read(65536))
            if health.get('status') != 'healthy':
                return self.reply(503, {'message': '챗봇 또는 연결 모델의 상태가 정상이 아닙니다. 연결 테스트 후 다시 시도하세요.'})
            # Each independent job owns a new upstream session; no client-supplied session IDs.
            with call(ENDPOINT.removesuffix('/health') + '/sessions', {'query': message}) as response:
                if response.status != 201: raise ValueError('session')
                session = json.loads(response.read(65536)).get('session_id', '')
            if not isinstance(session, str) or not re.fullmatch(r'[a-zA-Z0-9-]{1,100}', session): raise ValueError('session')
            base = ENDPOINT.split('/open/v1/')[0]
            with call(base + '/open/v1/chat-sessions/' + session + '/messages', {'message': message}) as response:
                if 'application/x-ndjson' not in response.headers.get('Content-Type', ''): raise ValueError('content-type')
                self.send_response(200)
                self.send_header('Content-Type', 'application/x-ndjson; charset=utf-8')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Accel-Buffering', 'no')
                self.end_headers(); started = True
                seen = set(); complete = False; failed = False; deadline = time.monotonic() + 180
                total = 0; answer_chars = 0
                while True:
                    line = response.readline(1048577)
                    if not line: break
                    total += len(line)
                    if len(line) > 1048576 or total > 8388608 or time.monotonic() > deadline: raise ValueError('limit')
                    if not line.strip(): continue
                    event = json.loads(line)
                    if not isinstance(event, dict): raise ValueError('event')
                    event_id = event.get('event_id')
                    if event_id:
                        if not isinstance(event_id, str): raise ValueError('event_id')
                        if event_id in seen: continue
                        seen.add(event_id)
                    kind = event.get('event') or event.get('type')
                    if kind == 'assistant.delta':
                        content = event.get('content', '')
                        if isinstance(content, list):
                            content = ''.join(block.get('text', '') for block in content if isinstance(block, dict) and block.get('type') == 'text' and isinstance(block.get('text'), str))
                        if isinstance(content, str) and content:
                            answer_chars += len(content)
                            emit({'type': 'delta', 'text': content})
                    elif kind in ['tool.failed', 'turn.failed']:
                        failed = True
                        emit({'type': 'error', 'message': '지식베이스 검색 또는 답변 생성이 실패했습니다. 수신된 일부 답변은 참고용으로만 확인하세요.'})
                        break
                    elif kind == 'turn.completed':
                        complete = True; emit({'type': 'completed'})
                        print(json.dumps({'chat_status': 'completed', 'answer_characters': answer_chars}), flush=True)
                        break
                if not complete and not failed: emit({'type': 'error', 'message': '완료 신호 없이 연결이 종료되었습니다. 일부 답변을 완료된 결과로 사용하지 마세요.'})
        except (BrokenPipeError, ConnectionResetError):
            return
        except (HTTPError, URLError, TimeoutError, ValueError, OSError) as error:
            code = error.code if isinstance(error, HTTPError) else None
            message = {401: 'API 키 인증 실패', 403: '챗봇 호출 권한이 없습니다.', 429: '요청 한도에 도달했습니다.', 503: 'AgentStream이 일시적으로 사용할 수 없는 상태입니다.'}.get(code, '답변을 완료하지 못했습니다. 연결 상태 또는 응답 형식을 확인하세요.')
            if started:
                try: emit({'type': 'error', 'message': message})
                except OSError: pass
            else: self.reply(502, {'message': message})

if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 8501), Handler).serve_forever()
