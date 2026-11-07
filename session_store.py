"""Encrypted, browser-isolated API credentials. Keep outside the web root."""
import hashlib
import os
import secrets
import sqlite3
import time
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken

TTL = 30 * 24 * 60 * 60
class SessionStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        path = self.directory / 'encryption.key'
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, 'wb') as file: file.write(Fernet.generate_key())
        os.chmod(path, 0o600)
        self.cipher = Fernet(path.read_bytes())
        self.path = self.directory / 'sessions.sqlite'
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, secret BLOB NOT NULL, expires INTEGER NOT NULL)')
        os.chmod(self.path, 0o600)
    def shared_key(self):
        path = self.directory / 'shared-api.enc'
        if not path.exists(): return None
        try: return self.cipher.decrypt(path.read_bytes()).decode()
        except (InvalidToken, OSError): return None

    def digest(self, token):
        return hashlib.sha256(token.encode()).hexdigest()
    def save(self, key, old_token=None):
        token = secrets.token_urlsafe(32)
        expires = int(time.time()) + TTL
        with sqlite3.connect(self.path) as db:
            db.execute('DELETE FROM sessions WHERE expires <= ?', (int(time.time()),))
            if old_token: db.execute('DELETE FROM sessions WHERE token=?', (self.digest(old_token),))
            db.execute('INSERT INTO sessions VALUES (?,?,?)', (self.digest(token), self.cipher.encrypt(key.encode()), expires))
        return token, expires
    def get(self, token):
        if not token or len(token) > 200: return None
        with sqlite3.connect(self.path) as db:
            db.execute('DELETE FROM sessions WHERE expires <= ?', (int(time.time()),))
            row = db.execute('SELECT secret,expires FROM sessions WHERE token=?', (self.digest(token),)).fetchone()
        if not row: return None
        try: return self.cipher.decrypt(row[0]).decode(), row[1]
        except InvalidToken: return None
    def delete(self, token):
        if token:
            with sqlite3.connect(self.path) as db:
                db.execute('DELETE FROM sessions WHERE token=?', (self.digest(token),))

store = None
def get_store():
    global store
    if store is None: store = SessionStore('/home/ubuntu/.ax26-private')
    return store
