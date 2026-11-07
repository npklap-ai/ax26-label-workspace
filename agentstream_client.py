"""Fixed AgentStream transport. No automatic retries or credential persistence."""
import json
import logging
import socket
import threading
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler

BASE_URL = 'https://api-gateway.jntp-ax.or.kr/agentstream'
CHATBOT_ID = '5a09d748-53e8-4260-9d24-5a64e5e52960'
WORKSPACE_ID = 'a35a47e3-876f-43c2-ac7f-7a96bdff83e2'

class UpstreamError(Exception):
    def __init__(self, code, stage, status=None, message=None, retry_after=None):
        self.code, self.stage, self.status, self.retry_after = code, stage, status, retry_after
        phase = {'health':'연결 상태 확인','session':'대화 생성','message':'답변 수신'}.get(stage,'요청')
        super().__init__(message or f'{phase}에 실패했습니다' + (f' (HTTP {status})' if status else '') + '. 연결 상태를 확인해 주세요.')

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def close_response(response):
    # Closing a buffered HTTPResponse alone can wait for another thread's read lock.
    sock = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
    if sock is not None:
        try: sock.shutdown(socket.SHUT_RDWR)
        except OSError: pass
    try: response.close()
    except (OSError, ValueError): pass

class Cancellation:
    """A turn-wide deadline and response cancellation shared by all HTTP phases."""
    def __init__(self, timeout):
        self.deadline = time.monotonic() + timeout
        self.event = threading.Event()
        self.code = 'CANCELLED'
        self.lock = threading.Lock()
        self.response = None
        self.timer = threading.Timer(timeout, lambda: self.cancel('TIMEOUT'))
        self.timer.daemon = True
        self.timer.start()
    def remaining(self, stage):
        if self.event.is_set() or time.monotonic() >= self.deadline:
            if not self.event.is_set(): self.cancel('TIMEOUT')
            raise UpstreamError(self.code, stage, message='요청 시간이 초과되었습니다.' if self.code == 'TIMEOUT' else '요청이 취소되었습니다.')
        return self.deadline - time.monotonic()
    def attach(self, response, stage):
        with self.lock:
            self.response = response
            stopped = self.event.is_set()
        if stopped: close_response(response)
        self.remaining(stage)
    def detach(self, response):
        with self.lock:
            if self.response is response: self.response = None
    def cancel(self, code='CANCELLED'):
        with self.lock:
            if self.event.is_set(): return
            self.code = code
            self.event.set()
            response = self.response
        if response is not None: close_response(response)
    def close(self):
        self.timer.cancel()
        with self.lock:
            response, self.response = self.response, None
        if response is not None: close_response(response)

class AgentStreamClient:
    def __init__(self, api_key, *, opener=None, log=None):
        self.api_key = api_key
        self.opener = opener or build_opener(NoRedirect())
        self.log = log or (lambda entry: logging.getLogger('agentstream').info(json.dumps(entry, ensure_ascii=False)))
    def safe(self, value, limit=500):
        if not isinstance(value,str): return None
        if self.api_key: value = value.replace(self.api_key,'[REDACTED]')
        return ''.join(c if ord(c)>=32 else ' ' for c in value)[:limit]
    def sanitize(self, value):
        if isinstance(value,str): return value.replace(self.api_key,'[REDACTED]') if self.api_key else value
        if isinstance(value,list): return [self.sanitize(item) for item in value]
        if isinstance(value,dict): return {self.sanitize(key):self.sanitize(item) for key,item in value.items()}
        return value
    def error(self, code, stage, response=None, detail=None):
        status = getattr(response,'status',getattr(response,'code',None))
        headers = getattr(response,'headers',{})
        retry = self.safe(headers.get('Retry-After'),128)
        message = self.safe(detail)
        if message:
            phase = {'health':'연결 상태 확인','session':'대화 생성','message':'답변 수신'}[stage]
            message = f'{phase}에 실패했습니다 (HTTP {status}). {message}'
        return UpstreamError(code,stage,status,message,retry)
    def read_json(self, response, cancel, stage):
        chunks=[]; total=0
        while True:
            cancel.remaining(stage)
            chunk=response.read(65536)
            cancel.remaining(stage)
            if not chunk: break
            total+=len(chunk)
            if total>1048576: raise self.error('RESPONSE_LIMIT',stage,response)
            chunks.append(chunk)
        try: return json.loads(b''.join(chunks).decode('utf-8'))
        except (ValueError,UnicodeError): raise self.error('INVALID_JSON',stage,response) from None
    def open(self, path, cancel, stage, body=None, stream=False, expected=200):
        started=time.monotonic(); response=None
        try:
            if not isinstance(self.api_key,str) or not self.api_key.strip(): raise self.error('KEY_MISSING',stage)
            if not self.api_key.isascii() or any(ord(c)<33 or ord(c)>126 for c in self.api_key): raise self.error('KEY_INVALID',stage)
            headers={'X-API-Key':self.api_key,'Accept':'application/x-ndjson' if stream else 'application/json'}
            raw=None
            if body is not None: raw=json.dumps(body,ensure_ascii=False).encode();headers['Content-Type']='application/json'
            request=Request(BASE_URL+path,data=raw,headers=headers)
            try:
                response=self.opener.open(request,timeout=min(30,cancel.remaining(stage)))
            except HTTPError as error:
                response=error
            cancel.attach(response,stage)
            status=getattr(response,'status',getattr(response,'code',None))
            mime=response.headers.get('Content-Type','').split(';')[0].strip().lower()
            is_json=mime=='application/json' or mime.endswith('+json')
            if status!=expected:
                detail=None
                if is_json:
                    try:
                        data=self.read_json(response,cancel,stage)
                        if isinstance(data,dict):
                            nested=data.get('error')
                            detail=nested.get('message') if isinstance(nested,dict) else data.get('message')
                    except UpstreamError:
                        cancel.remaining(stage)
                raise self.error('HTTP_ERROR',stage,response,detail)
            if not (mime=='application/x-ndjson' if stream else is_json): raise self.error('CONTENT_TYPE',stage,response)
            # Socket idle timeout follows the remaining turn deadline once connected.
            sock=getattr(getattr(getattr(response,'fp',None),'raw',None),'_sock',None)
            if sock is not None: sock.settimeout(cancel.remaining(stage))
            return response
        except UpstreamError:
            if response is not None: cancel.detach(response);close_response(response)
            raise
        except (URLError,OSError,ValueError):
            if response is not None: cancel.detach(response);close_response(response)
            cancel.remaining(stage)
            raise self.error('NETWORK_ERROR',stage,response) from None
        finally:
            try: self.log({'stage':stage,'status':getattr(response,'status',None),'content_type':self.safe(getattr(response,'headers',{}).get('Content-Type'),200),'request_id':self.safe(getattr(response,'headers',{}).get('X-Request-Id'),200),'elapsed_ms':round((time.monotonic()-started)*1000)})
            except Exception: pass
    def json_request(self,path,cancel,stage,body=None,expected=200):
        response=self.open(path,cancel,stage,body,expected=expected)
        try: return self.sanitize(self.read_json(response,cancel,stage))
        except (OSError,ValueError):
            cancel.remaining(stage)
            raise self.error('INVALID_RESPONSE',stage,response) from None
        finally: cancel.detach(response);close_response(response)
    def health(self,cancel):
        data=self.json_request(f'/open/v1/chatbots/{CHATBOT_ID}/health',cancel,'health')
        if not isinstance(data,dict) or not isinstance(data.get('status'),str): raise self.error('INVALID_RESPONSE','health')
        return {key:data.get(key) for key in ['status','message','checked_at','embedding_models']}
    def create_session(self,query,cancel):
        data=self.json_request(f'/open/v1/chatbots/{CHATBOT_ID}/sessions',cancel,'session',{'query':query},201)
        session=data.get('session_id') if isinstance(data,dict) else None
        try:
            if not isinstance(session,str) or str(uuid.UUID(session))!=session.lower(): raise ValueError()
        except ValueError: raise self.error('INVALID_SESSION','session') from None
        return session
    def message(self,session,message,cancel):
        response=self.open(f'/open/v1/chat-sessions/{session}/messages',cancel,'message',{'message':message},True)
        total=0;seen=set()
        try:
            while True:
                cancel.remaining('message')
                line=response.readline(1048577)
                cancel.remaining('message')
                if not line: raise self.error('INCOMPLETE_STREAM','message',response)
                total+=len(line)
                if len(line)>1048576 or total>8388608: raise self.error('STREAM_LIMIT','message',response)
                if not line.strip(): continue
                try: event=json.loads(line.decode('utf-8'))
                except (ValueError,UnicodeError): raise self.error('INVALID_JSON','message',response) from None
                if not isinstance(event,dict): raise self.error('INVALID_EVENT','message',response)
                event=self.sanitize(event)
                data=event.get('data') if isinstance(event.get('data'),dict) else {}
                event_id=event.get('event_id',data.get('event_id'))
                if event_id is not None:
                    if not isinstance(event_id,str): raise self.error('INVALID_EVENT','message',response)
                    if event_id in seen: continue
                    seen.add(event_id)
                # Event names carry the protocol action; type can be AIMessageChunk/progress.
                kind=event.get('event') or event.get('type')
                if kind in ['tool.failed','turn.failed']: raise self.error('TOOL_FAILED' if kind=='tool.failed' else 'TURN_FAILED','message',response)
                if kind=='assistant.delta':
                    content=event.get('content',data.get('content',''))
                    if isinstance(content,list): content=''.join(block['text'] for block in content if isinstance(block,dict) and block.get('type')=='text' and isinstance(block.get('text'),str))
                    if isinstance(content,str) and content: yield {'type':'delta','text':content}
                for field in ['sources','citations','references']:
                    evidence=event.get(field,data.get(field))
                    if evidence is not None: yield {'type':'evidence','data':evidence}
                if kind=='turn.completed': yield {'type':'completed'};return
        except UpstreamError: raise
        except (OSError,ValueError):
            cancel.remaining('message')
            raise self.error('STREAM_ERROR','message',response) from None
        finally: cancel.detach(response);close_response(response)
