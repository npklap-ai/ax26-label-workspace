"""Operator-run, real first/follow-up request through the deployed service.

Prints only status metadata. This creates real AgentStream messages and may incur
usage. Never accepts an API key: the application server owns the credential.
"""
import argparse
import http.cookiejar
import json
import sys
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPCookieProcessor, HTTPRedirectHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url', help='Application page URL, including its path prefix')
    args = parser.parse_args()
    base = args.url.rstrip('/') + '/'
    parsed = urlparse(base)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.query or parsed.fragment or parsed.username:
        parser.error('Use the application URL without credentials, query, or fragment.')
    origin = f'{parsed.scheme}://{parsed.netloc}'
    opener = build_opener(NoRedirect(), HTTPCookieProcessor(http.cookiejar.CookieJar()))
    csrf = ''

    def call(path, body=None):
        headers = {'Accept': 'application/json, application/x-ndjson'}
        raw = None
        if body is not None:
            raw = json.dumps(body, ensure_ascii=False).encode('utf-8')
            headers.update({'Content-Type': 'application/json', 'Origin': origin, 'X-CSRF-Token': csrf})
        return opener.open(Request(base + 'api/' + path, data=raw, headers=headers), timeout=240)

    def read_json(response):
        if response.headers.get_content_type() != 'application/json':
            raise ValueError('JSON 대신 다른 응답이 왔습니다. 사이트 로그인·프록시 경로를 확인하세요.')
        raw = response.read(1048577)
        if len(raw) > 1048576:
            raise ValueError('JSON 응답 크기 제한 초과')
        return json.loads(raw)

    with call('session') as response:
        session = read_json(response)
    csrf = session.get('csrfToken', '')
    if not session.get('connected') or not csrf:
        raise ValueError('서버 키 설정 또는 방문자 세션을 확인하세요.')
    with call('health', {}) as response:
        health = read_json(response)
    print(json.dumps({'stage': 'health', 'status': health.get('status'),
                      'checked_at_present': bool(health.get('checked_at'))}, ensure_ascii=False))
    if health.get('status') != 'healthy':
        raise ValueError('챗봇 상태가 healthy가 아닙니다.')
    prompts = [
        '실제 연동 검증용 질문입니다. 연결된 지식베이스에서 건강기능식품 표시사항에 관한 근거 한 항목을 찾아 문서명과 조항 또는 페이지, 적용 범위를 알려주세요. 찾지 못하면 확인 불가로 답해주세요. 실제 제품의 적법성 판단은 요청하지 않습니다.',
        '방금 답변에서 직접 확인한 문서 근거와 아직 확인하지 못한 내용을 구분하고, 제품 라벨을 검토하려면 어떤 정보가 더 필요한지 알려주세요.',
    ]
    conversation = None
    for number, message in enumerate(prompts, 1):
        body = {'message': message, 'requestId': str(uuid.uuid4())}
        if conversation:
            body['conversationId'] = conversation
        complete = False
        characters = 0
        evidence = 0
        total = 0
        with call('chat', body) as response:
            if response.headers.get_content_type() != 'application/x-ndjson':
                raise ValueError('NDJSON 응답이 아닙니다.')
            while True:
                raw = response.readline(1048577)
                if not raw:
                    break
                total += len(raw)
                if len(raw) > 1048576 or total > 8388608:
                    raise ValueError('스트림 크기 제한 초과')
                if not raw.strip():
                    continue
                event = json.loads(raw)
                kind = event.get('type')
                if kind == 'session':
                    received = event.get('conversationId')
                    if conversation and received != conversation:
                        raise ValueError('후속 질문의 자체 대화 ID가 달라졌습니다.')
                    conversation = received
                elif kind == 'delta':
                    characters += len(event.get('text', ''))
                elif kind == 'evidence':
                    evidence += 1
                elif kind == 'error':
                    raise ValueError('대화 실패: ' + str(event.get('code', 'STREAM_FAILED')))
                elif kind == 'completed':
                    complete = True
        if not complete or not conversation or not characters:
            raise ValueError('답변과 완료 이벤트를 모두 확인하지 못했습니다.')
        print(json.dumps({'stage': 'turn', 'number': number, 'completed': True,
                          'answer_characters': characters, 'evidence_events': evidence}, ensure_ascii=False))
    print('첫 질문과 후속 질문의 전체 응답 완료를 확인했습니다. 법령 근거의 정확성과 적용 여부는 화면에서 별도로 검토하세요.')


if __name__ == '__main__':
    try:
        main()
    except HTTPError as exc:
        print(json.dumps({'error': 'HTTP_ERROR', 'status': exc.code,
                          'content_type': exc.headers.get('Content-Type', ''),
                          'retry_after': exc.headers.get('Retry-After')}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    except (ValueError, URLError, OSError) as exc:
        # Do not print arbitrary upstream error bodies or user content.
        print('검증 실패: ' + (str(exc) if isinstance(exc, ValueError) else type(exc).__name__), file=sys.stderr)
        sys.exit(1)
