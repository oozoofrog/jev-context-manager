import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

from . import config as policies
from .util import JCMError, atomic_write, digest, encode, identifier, now, private_dir, redact

SCHEMA = '''
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, session TEXT NOT NULL,
 turn TEXT, kind TEXT NOT NULL, role TEXT NOT NULL, blob TEXT NOT NULL, snapshot TEXT NOT NULL,
 observed_at TEXT, recorded_at TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
 egress INTEGER NOT NULL, redactions INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sources (
 key TEXT PRIMARY KEY, session TEXT NOT NULL, path TEXT NOT NULL, generation INTEGER NOT NULL,
 offset INTEGER NOT NULL, prefix_hash TEXT NOT NULL, inode INTEGER NOT NULL, status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS event_sources (
 event_id TEXT NOT NULL, source_key TEXT NOT NULL, blob TEXT NOT NULL,
 PRIMARY KEY(event_id, source_key));
CREATE TABLE IF NOT EXISTS jobs (
 event_id TEXT PRIMARY KEY, state TEXT NOT NULL, owner TEXT, lease_until REAL,
 attempts INTEGER NOT NULL DEFAULT 0, revision INTEGER, decision TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS projections (
 event_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, labels TEXT NOT NULL,
 basis TEXT NOT NULL, lifecycle TEXT NOT NULL, implementation_status TEXT NOT NULL,
 decision TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions (
 id TEXT PRIMARY KEY, cache_key TEXT, epoch INTEGER NOT NULL, status TEXT NOT NULL,
 request_blob TEXT NOT NULL, response_blob TEXT, model TEXT, usage TEXT, error TEXT,
 created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, day TEXT NOT NULL, bytes INTEGER NOT NULL, status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS requests (
 token TEXT PRIMARY KEY, session TEXT NOT NULL, event_id TEXT NOT NULL, epoch INTEGER NOT NULL,
 created REAL NOT NULL, UNIQUE(session, event_id, epoch));
CREATE TABLE IF NOT EXISTS packs (
 id TEXT PRIMARY KEY, token TEXT NOT NULL, epoch INTEGER NOT NULL, blob TEXT NOT NULL,
 snapshot TEXT NOT NULL, delivery TEXT NOT NULL, invalid INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS receipts (
 id TEXT PRIMARY KEY, pack_id TEXT NOT NULL, kind TEXT NOT NULL, bytes INTEGER NOT NULL,
 hash TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tombstones (session TEXT PRIMARY KEY, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS gaps (code TEXT PRIMARY KEY, detail TEXT NOT NULL, created TEXT NOT NULL);
'''


class Store:
    def __init__(self, config):
        os.umask(0o077)
        self.config = config
        self.path = private_dir(Path(config['home']) / 'stores' / identifier(config['repo_id']))
        self.blobs = private_dir(self.path / 'blobs')
        db_path = self.path / 'journal.sqlite'
        if db_path.is_symlink():
            raise JCMError('SYMLINK_DATABASE_REFUSED')
        self.db = sqlite3.connect(db_path, timeout=10, isolation_level=None)
        db_path.chmod(0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('PRAGMA foreign_keys=ON')
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 1):
            self.db.close()
            raise JCMError('UNSUPPORTED_DATABASE_VERSION')
        self.db.executescript(SCHEMA)
        self.db.execute('PRAGMA user_version=1')

    def close(self):
        self.db.close()

    def policy(self, expected_epoch=None, require_enabled=True):
        current = policies.load(self.config['home'], self.config['root'])
        if require_enabled and not current['enabled']:
            raise JCMError('PROJECT_DISABLED')
        if expected_epoch is not None and current['epoch'] != expected_epoch:
            raise JCMError('POLICY_EPOCH_CHANGED')
        return current

    def put_blob(self, value):
        data = encode(value)
        key = digest(data)
        target = self.blobs / key
        if not target.exists():
            atomic_write(target, data)
        elif target.is_symlink() or digest(target.read_bytes()) != key:
            raise JCMError('BLOB_HASH_MISMATCH')
        return key

    def blob(self, key):
        target = self.blobs / identifier(key)
        if target.is_symlink():
            raise JCMError('SYMLINK_BLOB_REFUSED')
        try:
            data = target.read_bytes()
        except FileNotFoundError:
            raise JCMError('BLOB_MISSING') from None
        if digest(data) != key:
            raise JCMError('BLOB_HASH_MISMATCH')
        return json.loads(data)

    def gap(self, code, detail=''):
        self.db.execute('INSERT OR REPLACE INTO gaps VALUES (?,?,?)', (code, detail, now()))

    def capture(self, *, session, turn, kind, role, payload, snapshot, source_key,
                identity, observed_at=None, cursor=None, failpoint=None):
        policy = self.policy()
        if self.db.execute('SELECT 1 FROM tombstones WHERE session=?', (session,)).fetchone():
            return None
        admitted, redactions = redact(payload)
        if len(encode(admitted)) > 1_000_000:
            self.gap('EVENT_TOO_LARGE', kind)
            raise JCMError('EVENT_TOO_LARGE_NOT_ACKNOWLEDGED')
        blob = self.put_blob(admitted)
        event_id = digest([self.config['repo_id'], session, identity])
        if failpoint:
            failpoint('after_blob')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.policy(policy['epoch'])
            if self.db.execute('SELECT 1 FROM tombstones WHERE session=?', (session,)).fetchone():
                raise JCMError('SOURCE_FORGOTTEN')
            old = self.db.execute('SELECT * FROM events WHERE id=?', (event_id,)).fetchone()
            duplicate = self.db.execute('SELECT 1 FROM event_sources WHERE event_id=? AND source_key=?',
                                        (event_id, source_key)).fetchone()
            if not old:
                self.db.execute('''INSERT INTO events
                  (id,session,turn,kind,role,blob,snapshot,observed_at,recorded_at,egress,redactions)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                  (event_id, session, turn, kind, role, blob, json.dumps(snapshot), observed_at,
                   now(), int(policy['allow_egress']), redactions))
            elif not duplicate:
                self.db.execute('UPDATE events SET revision=revision+1, blob=? WHERE id=?', (blob, event_id))
            if not duplicate:
                self.db.execute('INSERT INTO event_sources VALUES (?,?,?)', (event_id, source_key, blob))
                if (old['role'] if old else role) in ('user', 'assistant', 'tool'):
                    self.db.execute('''INSERT INTO jobs (event_id,state) VALUES (?, 'queued')
                     ON CONFLICT(event_id) DO UPDATE SET state='queued',owner=NULL,lease_until=NULL''', (event_id,))
            if cursor:
                self.set_cursor(cursor)
            if failpoint:
                failpoint('before_commit')
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise
        if failpoint:
            failpoint('after_commit')
        return event_id

    def change_policy(self, **changes):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            result = policies.save_policy(self.policy(require_enabled=False), **changes)
            self.db.execute('UPDATE packs SET invalid=1')
            self.db.execute('COMMIT')
            return result
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def set_cursor(self, cursor):
        self.db.execute('INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?,?,?,?)',
                        tuple(cursor[k] for k in ('key', 'session', 'path', 'generation', 'offset', 'prefix_hash', 'inode', 'status')))

    def request(self, session, event_id):
        epoch = self.policy()['epoch']
        row = self.db.execute('SELECT token FROM requests WHERE session=? AND event_id=? AND epoch=?',
                              (session, event_id, epoch)).fetchone()
        if row:
            return row[0]
        token = uuid.uuid4().hex
        self.db.execute('INSERT INTO requests VALUES (?,?,?,?,?)', (token, session, event_id, epoch, time.time()))
        return token

    def resolve_request(self, token):
        row = self.db.execute('SELECT * FROM requests WHERE token=?', (identifier(token),)).fetchone()
        if not row:
            raise JCMError('REQUEST_NOT_FOUND_IN_THIS_PROJECT')
        self.policy(row['epoch'])
        if time.time() - row['created'] > 86400:
            raise JCMError('REQUEST_EXPIRED')
        return dict(row)

    def events(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM events ORDER BY seq')]

    def event(self, event_id):
        row = self.db.execute('SELECT * FROM events WHERE id=?', (event_id,)).fetchone()
        if not row:
            raise JCMError('EVENT_NOT_FOUND')
        return dict(row)

    def material(self, event):
        payload = self.blob(event['blob'])
        return {'event_id': event['id'], 'seq': event['seq'], 'revision': event['revision'],
                'session': event['session'], 'role': event['role'], 'kind': event['kind'],
                'recorded_at': event['recorded_at'], 'observed_at': event['observed_at'],
                'text': payload.get('text', ''), 'basis': {'user': 'source_observed',
                'assistant': 'agent_reported', 'tool': 'tool_observed'}.get(event['role'], 'unknown'),
                'implementation_status': 'not_established', 'trust': 'historical_data_not_instructions',
                'source_refs': [r[0] for r in self.db.execute('SELECT source_key FROM event_sources WHERE event_id=?', (event['id'],))]}

    def lease(self, owner, seconds=30):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute("UPDATE jobs SET state='queued',owner=NULL WHERE state='leased' AND lease_until<?", (time.time(),))
            row = self.db.execute("SELECT * FROM jobs WHERE state IN ('queued','retryable') ORDER BY rowid LIMIT 1").fetchone()
            if row:
                event = self.event(row['event_id'])
                self.db.execute("UPDATE jobs SET state='leased',owner=?,lease_until=?,attempts=attempts+1,revision=? WHERE event_id=?",
                                (owner, time.time() + seconds, event['revision'], event['id']))
            self.db.execute('COMMIT')
            return event if row else None
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def forget_session(self, session):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            policies.save_policy(self.policy(require_enabled=False))
            self.db.execute('INSERT OR REPLACE INTO tombstones VALUES (?,?)', (session, now()))
            for table in ('projections', 'jobs', 'event_sources'):
                self.db.execute(f'DELETE FROM {table} WHERE event_id IN (SELECT id FROM events WHERE session=?)', (session,))
            self.db.execute('DELETE FROM events WHERE session=?', (session,))
            self.db.execute('DELETE FROM sources WHERE session=?', (session,))
            self.db.execute('DELETE FROM meta WHERE key IN (?,?)',
                            ('bootstrap_existing:' + session, 'bootstrap_new:' + session))
            # Conservative derivative invalidation includes mixed-source model requests.
            for table in ('requests', 'packs', 'receipts', 'decisions'):
                self.db.execute(f'DELETE FROM {table}')
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise
        retained = {r[0] for r in self.db.execute('SELECT blob FROM events UNION SELECT blob FROM event_sources')}
        for path in self.blobs.iterdir():
            if path.is_file() and path.name not in retained:
                path.unlink()
        return {'session': session, 'tombstone': True, 'derivatives_invalidated': True,
                'external_transcripts_deleted': False, 'physical_erasure_guaranteed': False}
