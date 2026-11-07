import io
import json
import tempfile
import unittest
from pathlib import Path
from email.message import Message
from agentstream_client import AgentStreamClient, UpstreamError, Cancellation
from session_store import SessionStore, StoreError

SESSION = '22222222-2222-4222-8222-222222222222'
class Response(io.BytesIO):
    def __init__(self, data, status=200, content_type='application/json'):
        super().__init__(data if isinstance(data, bytes) else json.dumps(data).encode())
        self.status = status
        self.headers = Message(); self.headers['Content-Type'] = content_type
class Opener:
    def __init__(self, responses): self.responses = iter(responses); self.calls = []
    def open(self, request, timeout):
        self.calls.append(request)
        return next(self.responses)
class ClientTests(unittest.TestCase):
    def client(self, responses, key='test-secret'):
        opener = Opener(responses)
        return AgentStreamClient(key, opener=opener), opener
    def test_health_and_exact_session_message(self):
        client, opener = self.client([Response({'status':'healthy','embedding_models':[]}), Response({'session_id':SESSION},201),Response(b'{"type":"assistant.delta","content":"answer"}\n{"type":"turn.completed"}\n',content_type='application/x-ndjson')])
        cancel = Cancellation(5)
        try:
            self.assertEqual(client.health(cancel)['status'],'healthy')
            self.assertEqual(client.create_session('first',cancel),SESSION)
            events = list(client.message(SESSION,'first',cancel))
        finally: cancel.close()
        self.assertEqual(events,[{'type':'delta','text':'answer'},{'type':'completed'}])
        self.assertEqual(json.loads(opener.calls[1].data),{'query':'first'})
        self.assertEqual(json.loads(opener.calls[2].data),{'message':'first'})
        self.assertIn('/open/v1/chat-sessions/'+SESSION+'/messages',opener.calls[2].full_url)
        self.assertEqual(opener.calls[0].get_header('X-api-key'),'test-secret')
    def test_key_missing_preflight(self):
        client, opener=self.client([],key='')
        with self.assertRaises(UpstreamError) as error: client.health(Cancellation(1))
        self.assertEqual(error.exception.code,'KEY_MISSING');self.assertEqual(opener.calls,[])
    def test_stream_evidence_nested_text_deduplication(self):
        records=[{'event':'assistant.delta','event_id':'a','data':{'content':[{'type':'text','text':'답변'},{'type':'reasoning','text':'hidden'}],'citations':[{'title':'law'}]}},{'type':'assistant.delta','event_id':'a','content':'duplicate'},{'type':'turn.completed'}]
        client,_=self.client([Response(('\n'.join(json.dumps(e) for e in records)).encode(),content_type='application/x-ndjson')]); cancel=Cancellation(5)
        try: events=list(client.message(SESSION,'x',cancel))
        finally: cancel.close()
        self.assertEqual(events,[{'type':'delta','text':'답변'},{'type':'evidence','data':[{'title':'law'}]},{'type':'completed'}])
    def test_eof_failure_bad_json_and_non_json(self):
        for payload,ctype in [(b'{"type":"assistant.delta","content":"partial"}\n','application/x-ndjson'),(b'bad\n','application/x-ndjson'),(b'<html>secret</html>','text/html'),(b'{"type":"tool.failed"}\n','application/x-ndjson')]:
            client,_=self.client([Response(payload,content_type=ctype)]); cancel=Cancellation(5)
            try:
                with self.assertRaises(UpstreamError): list(client.message(SESSION,'x',cancel))
            finally: cancel.close()
    def test_non_uuid_session_rejected(self):
        client,_=self.client([Response({'session_id':'notuuid'},201)]); cancel=Cancellation(5)
        try:
            with self.assertRaises(UpstreamError): client.create_session('x',cancel)
        finally: cancel.close()
class StoreTests(unittest.TestCase):
    def setUp(self): self.tmp=tempfile.TemporaryDirectory();self.store=SessionStore(Path(self.tmp.name))
    def tearDown(self): self.tmp.cleanup()
    def test_owner_csrf_and_followup(self):
        token,csrf=self.store.new_owner();owner=self.store.owner(token)
        self.assertEqual(owner['csrf'],csrf)
        conversation=self.store.begin_turn(owner['id'],None,'a')
        self.store.set_upstream(conversation,SESSION)
        self.store.finish_turn(conversation,'a',True)
        self.assertEqual(self.store.begin_turn(owner['id'],conversation,'b'),conversation)
        self.assertEqual(self.store.conversation(owner['id'],conversation)['upstream'],SESSION)
    def test_cross_owner_busy_uncertain_and_duplicate(self):
        a=self.store.owner(self.store.new_owner()[0])['id'];b=self.store.owner(self.store.new_owner()[0])['id']
        cid=self.store.begin_turn(a,None,'req')
        for owner,request in [(b,'other'),(a,'busy')]:
            with self.assertRaises(StoreError): self.store.begin_turn(owner,cid,request)
        self.store.finish_turn(cid,'req',False)
        with self.assertRaises(StoreError): self.store.begin_turn(a,cid,'next')
        with self.assertRaises(StoreError): self.store.begin_turn(a,None,'req')
    def test_restart_busy_becomes_uncertain(self):
        owner=self.store.owner(self.store.new_owner()[0])['id'];cid=self.store.begin_turn(owner,None,'req')
        reloaded=SessionStore(Path(self.tmp.name))
        self.assertEqual(reloaded.conversation(owner,cid)['state'],'uncertain')

class RobustnessTests(unittest.TestCase):
    def test_redirect_policy_does_not_construct_redirect_request(self):
        from agentstream_client import NoRedirect
        from urllib.request import Request
        self.assertIsNone(NoRedirect().redirect_request(Request('https://a.test'),None,302,'',{},'https://b.test'))
    def test_cancel_and_timeout_close_attached_response(self):
        import time
        for timeout,explicit in [(5,True),(.02,False)]:
            cancel=Cancellation(timeout);response=Response({});cancel.attach(response,'health')
            if explicit:cancel.cancel()
            else:time.sleep(.04)
            self.assertTrue(response.closed)
            with self.assertRaises(UpstreamError) as error:cancel.remaining('message')
            self.assertEqual(error.exception.code,'CANCELLED' if explicit else 'TIMEOUT');cancel.close()
    def test_size_limit_and_http_json_error_redaction(self):
        client=AgentStreamClient('test-secret',opener=Opener([Response(b' '*1048577)]));cancel=Cancellation(5)
        try:
            with self.assertRaises(UpstreamError) as error:client.health(cancel)
            self.assertEqual(error.exception.code,'RESPONSE_LIMIT')
        finally:cancel.close()
        response=Response({'error':{'message':'test-secret unavailable'}},503);response.headers['Retry-After']='10'
        logs=[];client=AgentStreamClient('test-secret',opener=Opener([response]),log=logs.append);cancel=Cancellation(5)
        try:
            with self.assertRaises(UpstreamError) as error:client.health(cancel)
            self.assertEqual(error.exception.status,503);self.assertEqual(error.exception.retry_after,'10')
            self.assertNotIn('test-secret',str(error.exception));self.assertIn('[REDACTED]',str(error.exception))
            self.assertNotIn('test-secret',json.dumps(logs))
        finally:cancel.close()
    def test_cancel_before_reserve_and_global_bound(self):
        from server import Runtime,Config
        with tempfile.TemporaryDirectory() as tmp:
            store=SessionStore(tmp);runtime=Runtime(Config(root=Path(tmp),max_active=1),None,store)
            self.assertTrue(runtime.cancel('owner','request'))
            with self.assertRaises(StoreError) as error:runtime.reserve('owner','request')
            self.assertEqual(error.exception.code,'CANCELLED')
            cancel=runtime.reserve('owner','one')
            with self.assertRaises(StoreError) as error:runtime.reserve('two','two')
            self.assertEqual(error.exception.status,429)
            with self.assertRaises(StoreError):runtime.cancel('other','one')
            runtime.cancel('owner','one');self.assertTrue(cancel.event.is_set());runtime.release('one')

    def test_cancel_cannot_target_other_owner_completed_request(self):
        from server import Runtime,Config
        with tempfile.TemporaryDirectory() as tmp:
            store=SessionStore(tmp);runtime=Runtime(Config(root=Path(tmp)),None,store)
            cid=store.begin_turn('first',None,'request')
            store.finish_turn(cid,'request',True)
            with self.assertRaises(StoreError) as error:runtime.cancel('second','request')
            self.assertEqual(error.exception.status,404)

class ConfigurationTests(unittest.TestCase):
    def test_fixed_upstream_settings_and_public_url_validation(self):
        import os
        from unittest.mock import patch
        from server import Config
        for env in [{'AGENTSTREAM_BASE_URL':'https://other.test'},{'AGENTSTREAM_CHATBOT_ID':'other'}, {'APP_PUBLIC_URL':'https://user:pass@example.test/'},{'APP_PUBLIC_URL':'https://example.test/?secret=x'},{'APP_PUBLIC_URL':'ftp://example.test/'},{'APP_BASE_PATH':'/prefix\r\nInjected:x'}]:
            with self.subTest(env=env), patch.dict(os.environ,env,clear=True):
                with self.assertRaises(ValueError):Config.from_env()
        with patch.dict(os.environ,{'AGENTSTREAM_BASE_URL':'','AGENTSTREAM_CHATBOT_ID':'','APP_PUBLIC_URL':'https://devopen.jntp-ax.or.kr/i/team/ax26/'},clear=True):
            config=Config.from_env();self.assertEqual(config.base_path,'/i/team/ax26');self.assertEqual(config.origin,'https://devopen.jntp-ax.or.kr')

class TurnRaceTests(unittest.TestCase):
    def test_late_first_finalizer_cannot_overwrite_second_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=SessionStore(tmp)
            cid=store.begin_turn('owner',None,'first')
            store.finish_turn(cid,'first',True)
            store.begin_turn('owner',cid,'second')
            store.finish_turn(cid,'first',False)
            self.assertEqual(store.conversation('owner',cid)['state'],'busy')
            store.finish_turn(cid,'second',True)
            self.assertEqual(store.conversation('owner',cid)['state'],'ready')
    def test_conversation_lock_survives_persisted_completion_until_release(self):
        from server import Runtime,Config
        with tempfile.TemporaryDirectory() as tmp:
            store=SessionStore(tmp);runtime=Runtime(Config(root=Path(tmp)),None,store)
            runtime.reserve('owner','first')
            cid=store.begin_turn('owner',None,'first')
            runtime.claim_conversation(cid,'first')
            store.finish_turn(cid,'first',True)
            runtime.reserve('owner','second')
            with self.assertRaises(StoreError):runtime.claim_conversation(cid,'second')
            runtime.release('first')
            runtime.claim_conversation(cid,'second')
            runtime.release('second')
