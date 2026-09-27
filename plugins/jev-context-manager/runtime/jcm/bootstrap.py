"""Explicit adoption of an existing session and request-driven fresh-session recovery."""
import json
import os
import re
import shlex
from pathlib import Path

from . import config
from .adapter import register_transcript, recover_source, recover_sources, transcript_path
from .coordinator import dispatch, read_pack
from .snapshot import snapshot
from .util import JCMError, encode, now


def session_id(value=None):
    if value is None:
        values = {os.environ[k] for k in ('CODEX_THREAD_ID', 'CODEX_SESSION_ID') if os.environ.get(k)}
        if len(values) != 1:
            raise JCMError('CURRENT_SESSION_ID_MISSING_OR_AMBIGUOUS')
        value = values.pop()
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise JCMError('INVALID_SESSION_ID')
    return value


def discover(store, session, path=None):
    if path:
        return transcript_path(store, path, session)
    matches = set()
    for root in store.config['transcript_roots']:
        # Inspect names for this exact id only; never read other session bodies.
        matches.update(p.resolve() for p in Path(root).rglob('*-' + session + '.jsonl') if not p.is_symlink())
    if len(matches) != 1:
        raise JCMError('TRANSCRIPT_NOT_FOUND' if not matches else 'TRANSCRIPT_DISCOVERY_AMBIGUOUS')
    return transcript_path(store, matches.pop(), session)


def existing(store, session=None, path=None, install=True, follow=True):
    store.policy()
    session = session_id(session)
    if store.db.execute('SELECT 1 FROM tombstones WHERE session=?', (session,)).fetchone():
        raise JCMError('SOURCE_FORGOTTEN')
    source = discover(store, session, path)
    register_transcript(store, source, session)
    row = store.db.execute('SELECT * FROM sources WHERE session=? AND path=?', (session, str(source))).fetchone()
    before = store.db.execute('SELECT COUNT(*) FROM events WHERE session=?', (session,)).fetchone()[0]
    recover_source(store, row)
    after = store.db.execute('SELECT COUNT(*) FROM events WHERE session=?', (session,)).fetchone()[0]
    try:
        installed = config.install_hooks(store.policy()) if install else None
    except PermissionError:
        raise JCMError('PROJECT_HOOK_INSTALL_PERMISSION_DENIED') from None
    from .follower import start, follower_status
    follower = start(store, row['key']) if follow else follower_status(store, row['key'])
    current = store.db.execute("SELECT id FROM events WHERE session=? AND role='user' ORDER BY seq DESC LIMIT 1", (session,)).fetchone()
    token = store.request(session, current[0]) if current else None
    result = {'origin': 'jcm', 'bootstrap': 'existing', 'session_id': session,
              'new_events': after - before, 'session_event_count': after,
              'source': dict(store.db.execute('SELECT * FROM sources WHERE key=?', (row['key'],)).fetchone()),
              'capture': 'local_transcript_follower' if follower['running'] else 'registered_for_next_recovery',
              'follower': follower, 'hooks': installed, 'hook_hot_reload': 'not_attested',
              'current_snapshot': snapshot(store.config['root']), 'historical_verification': 'not_established',
              'request_token': token, 'read_command': shlex.join(store.config['cli_argv'] +
                  ['bootstrap', 'new', '--request-token', token]) if token else None,
              'coverage': 'partial', 'gaps': [r[0] for r in store.db.execute('SELECT code FROM gaps')],
              'allow_egress': store.policy()['allow_egress'], 'provider_called': False,
              'created_at': now()}
    # Metadata carries source references/counts, never a summary or checkpoint.
    store.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',
                     ('bootstrap_existing:' + session, encode(result).decode()))
    return result


def prepare_new(store, session):
    session = session_id(session)
    store.policy()
    recovered = recover_sources(store)
    count = store.db.execute("SELECT COUNT(*) FROM events WHERE session!=? AND role IN ('user','assistant','tool')", (session,)).fetchone()[0]
    result = {'origin': 'jcm', 'bootstrap': 'new', 'stage': 'awaiting_request',
              'session_id': session, 'recovered_public_items': recovered,
              'prior_event_count': count, 'history': 'available' if count else 'empty',
              'delivery': 'not_requested', 'provider_called': False, 'coverage': 'partial', 'created_at': now()}
    store.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',
                     ('bootstrap_new:' + session, encode(result).decode()))
    return result


def new(store, token, provider=None):
    request = store.resolve_request(token)
    route = dispatch(store, token, provider)
    result = read_pack(store, route['pack_id'])
    result.update(bootstrap='new', stage='read_served' if result['delivery'] == 'read_served' else 'blocked',
                  session_id=request['session'], recovery_success='not_attested')
    # Serving bytes does not prove the agent consumed them or resumed correctly.
    store.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('bootstrap_new:' + request['session'],
        json.dumps({'bootstrap': 'new', 'stage': result['stage'], 'pack_id': route['pack_id'], 'created_at': now()})))
    return result
