"""Existing AX26 static application with an owner-bound AgentStream proxy."""
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from agentstream_client import AgentStreamClient, Cancellation, UpstreamError, BASE_URL, CHATBOT_ID, WORKSPACE_ID
from session_store import SessionStore, StoreError, TTL

@dataclass
class Config:
    root: Path
    host: str = '127.0.0.1'
    port: int = 8501
    public_url: str = 'http://localhost:8501'
    base_path: str = ''
    private_dir: Path = Path.home()/'.ax26-private'
    request_timeout: float = 180
    api_key: str = ''
    max_active: int = 16
    @classmethod
    def from_env(cls):
        for variable,expected in [('AGENTSTREAM_BASE_URL',BASE_URL),('AGENTSTREAM_CHATBOT_ID',CHATBOT_ID),('AGENTSTREAM_WORKSPACE_ID',WORKSPACE_ID)]:
            configured=os.environ.get(variable,'').strip()
            if configured and configured.rstrip('/')!=expected:
                raise ValueError(f'{variable} 값이 지정된 AgentStream 계약과 다릅니다.')
        port=int(os.environ.get('PORT') or '8501')
        if not 1<=port<=65535:raise ValueError('PORT 범위를 확인하세요.')
        public=(os.environ.get('APP_PUBLIC_URL') or f'http://localhost:{port}').rstrip('/')
        parsed=urlsplit(public)
        if parsed.scheme not in ['http','https'] or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or any(ord(c)<33 or ord(c)>126 for c in public):
            raise ValueError('APP_PUBLIC_URL 형식을 확인하세요.')
        try:parsed.port
        except ValueError:raise ValueError('APP_PUBLIC_URL 포트를 확인하세요.') from None
        base=os.environ.get('APP_BASE_PATH',parsed.path).rstrip('/')
        if base and (not re.fullmatch(r'/[A-Za-z0-9_./~-]*',base) or any(part in ['.','..'] for part in base.split('/')) or '//' in base):
            raise ValueError('APP_BASE_PATH 형식을 확인하세요.')
        return cls(root=Path(os.environ.get('APP_ROOT') or Path(__file__).parent).resolve(),host=os.environ.get('APP_HOST') or '127.0.0.1',port=port,public_url=public,base_path=base,private_dir=Path(os.environ.get('APP_STATE_DIR') or str(Path.home()/'.ax26-private')).expanduser(),request_timeout=max(1,min(600,float(os.environ.get('APP_REQUEST_TIMEOUT') or '180'))),api_key=os.environ.get('AGENTSTREAM_API_KEY',''))

    @property
    def origin(self):
        parsed=urlsplit(self.public_url)
        return f'{parsed.scheme}://{parsed.netloc}'
    @property
    def secure(self):return urlsplit(self.public_url).scheme=='https'

class Runtime:
    def __init__(self,config,client,store):
        self.config,self.client,self.store=config,client,store
        self.lock=threading.Lock();self.active={};self.cancelled={};self.rates={};self.conversations={}
    def reserve(self,owner,request):
        with self.lock:
            now=time.monotonic()
            self.cancelled={key:expiry for key,expiry in self.cancelled.items() if expiry>now}
            self.rates={key:deque(t for t in values if now-t<60) for key,values in self.rates.items() if values and now-values[-1]<60}
            if (owner,request) in self.cancelled:raise StoreError('CANCELLED',409)
            if request in self.active:raise StoreError('REQUEST_BUSY',409)
            if len(self.active)>=self.config.max_active:raise StoreError('SERVER_BUSY',429)
            rate=self.rates.setdefault(owner,deque())
            if len(rate)>=12:raise StoreError('RATE_LIMIT',429)
            rate.append(now)
            cancel=Cancellation(self.config.request_timeout)
            self.active[request]=(owner,cancel)
            return cancel
    def claim_conversation(self,conversation,request):
        with self.lock:
            current=self.conversations.get(conversation)
            if current is not None and current!=request:raise StoreError('CONVERSATION_BUSY',409)
            self.conversations[conversation]=request
    def release(self,request):
        with self.lock:
            entry=self.active.pop(request,None)
            self.conversations={cid:rid for cid,rid in self.conversations.items() if rid!=request}
        if entry:entry[1].close()
    def cancel(self,owner,request):
        saved_owner=self.store.request_owner(request)
        if saved_owner is not None and saved_owner!=owner:raise StoreError('REQUEST_NOT_FOUND',404)
        with self.lock:
            entry=self.active.get(request)
            if entry and entry[0]!=owner:raise StoreError('REQUEST_NOT_FOUND',404)
            if len(self.cancelled)>=1000:self.cancelled.pop(next(iter(self.cancelled)))
            self.cancelled[(owner,request)]=time.monotonic()+60
        if entry:entry[1].cancel()
        return True

class Handler(BaseHTTPRequestHandler):
    server_version='AX26'
    def log_message(self,*args):pass
    @property
    def runtime(self):return self.server.runtime
    @property
    def config(self):return self.runtime.config
    def route(self):
        path=urlsplit(self.path).path
        prefixes={self.config.base_path,'/ax26','/i/Tfo3vbS3FbL3oe9y/ax26',''}
        for prefix in sorted(prefixes,key=len,reverse=True):
            if path==prefix or path==prefix+'/':return ''
            if path.startswith(prefix+'/'):return path[len(prefix)+1:]
        return None
    def end_headers(self):
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','same-origin')
        super().end_headers()
    def reply(self,status,data,headers=None):
        payload=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(payload)))
        for key,value in (headers or {}).items():self.send_header(key,value)
        self.end_headers()
        try:self.wfile.write(payload)
        except (BrokenPipeError,ConnectionResetError):pass
    def owner(self):
        cookie=SimpleCookie()
        try:cookie.load(self.headers.get('Cookie',''))
        except Exception:return None
        token=cookie['ax26_session'].value if 'ax26_session' in cookie else None
        return self.runtime.store.owner(token)
    def do_GET(self):
        requested_path = urlsplit(self.path).path
        prefixes = {self.config.base_path, '/ax26', '/i/Tfo3vbS3FbL3oe9y/ax26'} - {''}
        if requested_path in prefixes:
            self.send_response(308)
            self.send_header('Location', requested_path + '/')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        route=self.route()
        if route=='api/session':
            owner=self.owner();headers={}
            if not owner:
                token,_=self.runtime.store.new_owner();owner=self.runtime.store.owner(token)
                path=(self.config.base_path or '')+'/'
                headers['Set-Cookie']=f'ax26_session={token}; Path={path}; Max-Age={TTL}; HttpOnly; SameSite=Strict'+('; Secure' if self.config.secure else '')
            return self.reply(200,{'connected':bool(self.config.api_key.strip()),'mode':'server','expiresAt':owner['expires'],'csrfToken':owner['csrf']},headers)
        files={'':'index.html','index.html':'index.html','app.js':'app.js','style.css':'style.css','stream.js':'stream.js'}
        filename=files.get(route)
        if not filename:return self.reply(404,{'code':'NOT_FOUND','message':'요청 경로를 찾을 수 없습니다.'})
        path=self.config.root/filename
        try:
            if not path.is_file() or path.is_symlink():raise OSError()
            payload=path.read_bytes()
        except OSError:return self.reply(404,{'code':'NOT_FOUND','message':'파일을 찾을 수 없습니다.'})
        self.send_response(200)
        self.send_header('Content-Type',mimetypes.guess_type(filename)[0] or 'application/octet-stream')
        self.send_header('Content-Length',str(len(payload)))
        self.end_headers()
        try:self.wfile.write(payload)
        except (BrokenPipeError,ConnectionResetError):pass
    def do_POST(self):
        route=self.route()
        if route not in ['api/health','api/chat','api/cancel']:return self.reply(404,{'code':'NOT_FOUND','message':'요청 경로를 찾을 수 없습니다.'})
        owner=self.owner()
        if self.headers.get('Origin')!=self.config.origin or not owner or not secrets.compare_digest(self.headers.get('X-CSRF-Token',''),owner['csrf']):
            return self.reply(403,{'code':'CSRF','message':'연결 화면을 새로고침한 뒤 다시 시도하세요.'})
        try:
            if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Type','').split(';')[0].strip().lower()!='application/json':raise ValueError()
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=6291456:raise ValueError()
            self.connection.settimeout(10)
            raw=self.rfile.read(size)
            if len(raw)!=size:raise ValueError()
            data=json.loads(raw)
            if not isinstance(data,dict):raise ValueError()
            # Identity, credentials and routing are fixed on the server.
            if set(data)&{'key','apiKey','session_id','sessionId','chatbotId','chatbot_id','workspaceId','workspace_id','baseUrl'}:raise ValueError()
        except (ValueError,OSError):return self.reply(400,{'code':'INVALID_REQUEST','message':'요청 내용을 확인하세요.'})
        try:
            if route=='api/health':return self.health(owner)
            request=data.get('requestId')
            if not valid_uuid(request):raise StoreError('INVALID_REQUEST_ID',400)
            if route=='api/cancel':return self.reply(200,{'cancelled':self.runtime.cancel(owner['id'],request)})
            message=data.get('message');conversation=data.get('conversationId')
            if not isinstance(message,str) or not message.strip() or len(message.encode())>1048576:raise StoreError('INVALID_MESSAGE',400)
            if conversation is not None and not valid_uuid(conversation):raise StoreError('INVALID_CONVERSATION',400)
            return self.chat(owner,message,conversation,request)
        except StoreError as error:
            messages={'RATE_LIMIT':'요청이 많습니다. 잠시 후 다시 시도하세요.','SERVER_BUSY':'서버가 처리 중입니다. 잠시 후 다시 시도하세요.','CONVERSATION_BUSY':'이 대화의 이전 요청이 진행 중입니다.','CONVERSATION_UNCERTAIN':'완료를 확인하지 못한 대화입니다. 새 대화를 시작하세요.','CANCELLED':'취소된 요청입니다. 새 대화를 시작하세요.'}
            self.reply(error.status,{'code':error.code,'message':messages.get(error.code,'대화 또는 요청 정보를 확인하세요.')})
        except (sqlite3.Error,OSError):self.reply(500,{'code':'STATE_ERROR','message':'대화 상태를 저장하지 못했습니다. 새 대화를 시작하세요.'})
    def health(self,owner):
        request=str(uuid.uuid4());cancel=self.runtime.reserve(owner['id'],request)
        try:self.reply(200,self.runtime.client.health(cancel))
        except UpstreamError as error:self.upstream_error(error)
        finally:self.runtime.release(request)
    def upstream_payload(self,error):
        return {'code':error.code,'message':str(error),'stage':error.stage,'upstreamStatus':error.status,'retryAfter':error.retry_after}
    def upstream_error(self,error):
        status=503 if error.code=='KEY_MISSING' else 504 if error.code=='TIMEOUT' else 502
        self.reply(status,self.upstream_payload(error),{'Retry-After':error.retry_after} if error.retry_after and error.retry_after.isdigit() else None)
    def chat(self,owner,message,conversation,request):
        cancel=self.runtime.reserve(owner['id'],request)
        conversation_id=None;started=False;completed=False
        def emit(event):
            self.wfile.write((json.dumps(event,ensure_ascii=False)+'\n').encode());self.wfile.flush()
        try:
            if conversation:self.runtime.claim_conversation(conversation,request)
            conversation_id=self.runtime.store.begin_turn(owner['id'],conversation,request)
            self.runtime.claim_conversation(conversation_id,request)
            health=self.runtime.client.health(cancel)
            if health['status']!='healthy':
                self.reply(503,{'code':'UNHEALTHY','message':health.get('message') or '챗봇 상태를 확인할 수 없습니다. 연결 상태를 확인하세요.','health':health})
                return
            record=self.runtime.store.conversation(owner['id'],conversation_id)
            session=record['upstream']
            if not session:
                session=self.runtime.client.create_session(message,cancel)
                self.runtime.store.set_upstream(conversation_id,session)
            cancel.remaining('message')
            self.send_response(200);self.send_header('Content-Type','application/x-ndjson; charset=utf-8');self.send_header('X-Accel-Buffering','no');self.end_headers();started=True
            emit({'type':'session','conversationId':conversation_id})
            for event in self.runtime.client.message(session,message,cancel):
                cancel.remaining('message')
                if event.get('type')=='completed':
                    # Persist completion before publishing it. A lost response still never triggers a retry.
                    self.runtime.store.finish_turn(conversation_id,request,True)
                    emit(event);completed=True;break
                emit(event)
            if not completed:raise UpstreamError('INCOMPLETE_STREAM','message')
        except UpstreamError as error:
            if started:
                try:emit({'type':'error',**self.upstream_payload(error)})
                except OSError:pass
            else:self.upstream_error(error)
        except (BrokenPipeError,ConnectionResetError):cancel.cancel()
        except (sqlite3.Error,OSError):
            cancel.cancel()
            if started:
                try:emit({'type':'error','code':'STATE_ERROR','message':'답변을 완료하지 못했습니다. 새 대화를 시작하세요.'})
                except OSError:pass
            else:self.reply(500,{'code':'STATE_ERROR','message':'대화 상태를 저장하지 못했습니다.'})
        finally:
            if conversation_id and not completed:
                try:self.runtime.store.finish_turn(conversation_id,request,False)
                except sqlite3.Error:pass
            self.runtime.release(request)

def valid_uuid(value):
    try:return isinstance(value,str) and str(uuid.UUID(value))==value.lower()
    except (ValueError,AttributeError):return False

def create_server(config=None,client=None,store=None):
    config=config or Config.from_env()
    client=client or AgentStreamClient(config.api_key)
    store=store or SessionStore(config.private_dir)
    server=ThreadingHTTPServer((config.host,config.port),Handler)
    server.daemon_threads=True
    server.runtime=Runtime(config,client,store)
    return server

if __name__=='__main__':
    import logging
    logging.basicConfig(level=logging.INFO,format='%(message)s')
    server=create_server()
    logging.info(json.dumps({'stage':'startup','key_configured':bool(server.runtime.config.api_key.strip()),'host':server.runtime.config.host,'port':server.server_port,'base_path':server.runtime.config.base_path}))
    server.serve_forever()
