"""Owner-bound conversation metadata. Credentials and messages are never stored."""
import hashlib
import os
import secrets
import sqlite3
import time
import uuid
from pathlib import Path

TTL=30*24*60*60
class StoreError(Exception):
    def __init__(self,code,status=409): self.code,self.status=code,status;super().__init__(code)
class SessionStore:
    def __init__(self,directory,workspace='a35a47e3-876f-43c2-ac7f-7a96bdff83e2',chatbot='5a09d748-53e8-4260-9d24-5a64e5e52960'):
        self.directory=Path(directory);self.directory.mkdir(mode=0o700,parents=True,exist_ok=True);os.chmod(self.directory,0o700)
        self.path=self.directory/'conversations.sqlite';self.workspace,self.chatbot=workspace,chatbot
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS owners (id TEXT PRIMARY KEY, token TEXT UNIQUE NOT NULL, csrf TEXT NOT NULL, expires INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, owner TEXT NOT NULL, workspace TEXT NOT NULL, chatbot TEXT NOT NULL, upstream TEXT, state TEXT NOT NULL, updated INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, owner TEXT NOT NULL, conversation TEXT NOT NULL, state TEXT NOT NULL, created INTEGER NOT NULL);''')
            columns={row['name'] for row in db.execute('PRAGMA table_info(conversations)')}
            if 'active_request' not in columns:db.execute('ALTER TABLE conversations ADD COLUMN active_request TEXT')
            db.execute("UPDATE conversations SET state='uncertain' WHERE state='busy'")
            db.execute("UPDATE requests SET state='uncertain' WHERE state='running'")
        os.chmod(self.path,0o600)
    def connect(self):
        db=sqlite3.connect(self.path,timeout=5);db.row_factory=sqlite3.Row;return db
    def new_owner(self):
        token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute('DELETE FROM requests WHERE created < ?',(int(time.time())-TTL,))
            db.execute('DELETE FROM conversations WHERE updated < ?',(int(time.time())-TTL,))
            db.execute('DELETE FROM owners WHERE expires <= ?',(int(time.time()),))
            db.execute('INSERT INTO owners VALUES (?,?,?,?)',(str(uuid.uuid4()),hashlib.sha256(token.encode()).hexdigest(),csrf,int(time.time())+TTL))
        return token,csrf
    def owner(self,token):
        if not isinstance(token,str) or not 20<=len(token)<=200:return None
        with self.connect() as db: row=db.execute('SELECT id,csrf,expires FROM owners WHERE token=? AND expires>?',(hashlib.sha256(token.encode()).hexdigest(),int(time.time()))).fetchone()
        return dict(row) if row else None
    def conversation(self,owner,conversation):
        with self.connect() as db: row=db.execute('SELECT * FROM conversations WHERE id=? AND owner=? AND workspace=? AND chatbot=?',(conversation,owner,self.workspace,self.chatbot)).fetchone()
        if not row:raise StoreError('CONVERSATION_NOT_FOUND',404)
        return dict(row)
    def begin_turn(self,owner,conversation,request):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM requests WHERE id=?',(request,)).fetchone():raise StoreError('REQUEST_REPLAY',409)
            if conversation:
                row=db.execute('SELECT * FROM conversations WHERE id=? AND owner=? AND workspace=? AND chatbot=?',(conversation,owner,self.workspace,self.chatbot)).fetchone()
                if not row:raise StoreError('CONVERSATION_NOT_FOUND',404)
                if row['state']!='ready':raise StoreError('CONVERSATION_'+row['state'].upper())
                db.execute("UPDATE conversations SET state='busy',updated=?,active_request=? WHERE id=?",(int(time.time()),request,conversation))
            else:
                conversation=str(uuid.uuid4())
                db.execute('INSERT INTO conversations (id,owner,workspace,chatbot,upstream,state,updated,active_request) VALUES (?,?,?,?,?,?,?,?)',(conversation,owner,self.workspace,self.chatbot,None,'busy',int(time.time()),request))
            db.execute('INSERT INTO requests VALUES (?,?,?,?,?)',(request,owner,conversation,'running',int(time.time())))
        return conversation
    def set_upstream(self,conversation,session):
        with self.connect() as db:db.execute('UPDATE conversations SET upstream=? WHERE id=?',(session,conversation))
    def finish_turn(self,conversation,request,success):
        with self.connect() as db:
            db.execute('UPDATE conversations SET state=?,updated=? WHERE id=? AND active_request=?',('ready' if success else 'uncertain',int(time.time()),conversation,request))
            db.execute('UPDATE requests SET state=? WHERE id=?',('completed' if success else 'uncertain',request))
    def request(self,owner,request):
        with self.connect() as db:row=db.execute('SELECT state FROM requests WHERE id=? AND owner=?',(request,owner)).fetchone()
        if not row:raise StoreError('REQUEST_NOT_FOUND',404)
        return dict(row)

    def request_owner(self,request):
        with self.connect() as db:row=db.execute('SELECT owner FROM requests WHERE id=?',(request,)).fetchone()
        return row['owner'] if row else None
