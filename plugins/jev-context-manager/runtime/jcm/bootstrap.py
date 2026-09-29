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
from .util import JCMError, digest, encode, now


def session_id(value=None):
    if value is None:
        values = {os.environ[k] for k in ('CODEX_THREAD_ID', 'CODEX_SESSION_ID') if os.environ.get(k)}
        if len(values) != 1:
            raise JCMError('CURRENT_SESSION_ID_MISSING_OR_AMBIGUOUS')
        value = values.pop()
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise JCMError('INVALID_SESSION_ID')
    return value


def discover_all(store, session, path=None):
    matches = {Path(path)} if path else set()
    name = re.compile(r'.+-' + re.escape(session) + r'(?:_[A-Za-z0-9-]+)?\.jsonl')
    for root in (() if path else store.config['transcript_roots']):
        # Inspect exact session names only, including Codex paginated segments.
        matches.update(p.resolve() for p in Path(root).rglob('*' + session + '*.jsonl')
                       if name.fullmatch(p.name) and not p.is_symlink())
    if not matches:
        raise JCMError('TRANSCRIPT_NOT_FOUND')
    sources = []
    for candidate in sorted(matches):
        source = transcript_path(store, candidate, session)
        with source.open('rb') as stream:
            meta = json.loads(stream.readline(1_000_001))['payload']
        base = meta.get('history_base')
        ordinal = 0
        if base is not None:
            if not isinstance(base, dict):
                raise JCMError('UNSUPPORTED_PAGINATED_HISTORY_BASE')
            ordinal = base.get('end_ordinal_exclusive')
            if base.get('thread_id') != session or type(ordinal) is not int or ordinal < 0:
                raise JCMError('UNSUPPORTED_PAGINATED_HISTORY_BASE')
            store.gap('PAGINATED_HISTORY_COVERAGE_PARTIAL', str(source))
        sources.append((ordinal, source))
    if len({ordinal for ordinal, _ in sources}) != len(sources):
        raise JCMError('TRANSCRIPT_DISCOVERY_AMBIGUOUS')
    return [source for _, source in sorted(sources)]


def discover(store, session, path=None):
    return discover_all(store, session, path)[-1]


def existing(store, session=None, path=None, install=True, follow=True):
    store.policy()
    session = session_id(session)
    if store.db.execute('SELECT 1 FROM tombstones WHERE session=?', (session,)).fetchone():
        raise JCMError('SOURCE_FORGOTTEN')
    from .entry import session_items
    from .request_source import current_request
    from .scope import admit_prompt
    current, request_error = None, None
    try:
        items, _ = session_items(store.config, session, path, requests_only=True)
        current = current_request(items)
        try:
            is_current_session = session == session_id()
        except JCMError:
            is_current_session = False
        if is_current_session:
            admit_prompt(store, session, current['turn'], current['identity'])
    except JCMError as exc:
        request_error = str(exc)
    before = store.db.execute('SELECT COUNT(*) FROM events WHERE session=?', (session,)).fetchone()[0]
    error = None
    sources = []
    for source in discover_all(store, session, path):
        key = register_transcript(store, source, session)
        row = store.db.execute('SELECT * FROM sources WHERE key=?', (key,)).fetchone()
        try:
            recover_source(store, row)
        except (JCMError, OSError) as exc:
            error = str(exc) if isinstance(exc, JCMError) else 'TRANSCRIPT_UNAVAILABLE'
        row = store.db.execute('SELECT * FROM sources WHERE key=?', (row['key'],)).fetchone()
        try:
            size = source.stat().st_size
        except OSError:
            size = None
        sources.append({**dict(row), 'source_bytes': size,
                        'backlog_bytes': max(0, size - row['offset']) if size is not None else None})
        if row['status'] not in ('read_to_offset', 'partial_line'):
            error = error or row['status']
    ready = error is None
    after = store.db.execute('SELECT COUNT(*) FROM events WHERE session=?', (session,)).fetchone()[0]
    try:
        installed = config.install_hooks(store.policy()) if install and ready else None
    except PermissionError:
        raise JCMError('PROJECT_HOOK_INSTALL_PERMISSION_DENIED') from None
    from .follower import start, follower_status
    follower = start(store, row['key']) if follow and ready else follower_status(store, row['key'])
    token, request_status = None, 'not_checked'
    if ready:
        try:
            if current is None:
                raise JCMError(request_error or 'CURRENT_REQUEST_NOT_OBSERVABLE')
            event_id = digest([store.config['repo_id'], session, current['identity']])
            event = store.event(event_id)
            if event['role'] != 'user':
                raise JCMError('CURRENT_REQUEST_NOT_CAPTURED')
            token = store.request(session, event_id)
            request_status = 'linked'
        except JCMError as exc:
            request_error = str(exc)
            request_status = 'unsupported' if request_error == 'UNSUPPORTED_CURRENT_REQUEST' else 'unavailable'
    result = {'origin': 'jcm', 'bootstrap': 'existing', 'session_id': session,
              'stage': 'captured' if ready else 'blocked', 'error': error,
              'source_bytes': sources[-1]['source_bytes'],
              'backlog_bytes': sources[-1]['backlog_bytes'], 'sources': sources,
              'new_events': after - before, 'session_event_count': after,
              'source': dict(store.db.execute('SELECT * FROM sources WHERE key=?', (row['key'],)).fetchone()),
              'capture': ('local_transcript_follower' if follower['running'] else 'registered_for_next_recovery') if ready else 'blocked',
              'follower': follower, 'hooks': installed, 'hook_hot_reload': 'not_attested',
              'current_snapshot': snapshot(store.config['root']), 'historical_verification': 'not_established',
              'request_token': token, 'read_command': shlex.join(store.config['cli_argv'] +
                  ['bootstrap', 'new', '--request-token', token]) if token else None,
              'request_status': request_status, 'request_error': request_error,
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
    result = read_pack(store, route['pack_id'], bootstrap=True)
    result.update(bootstrap='new', stage='read_served' if result['delivery'] == 'read_served' else 'reading' if result['delivery'] == 'page_served' else 'blocked',
                  session_id=request['session'], recovery_success='not_attested')
    # Serving bytes does not prove the agent consumed them or resumed correctly.
    meta_key = 'bootstrap_new:' + request['session']
    previous = store.db.execute('SELECT value FROM meta WHERE key=?', (meta_key,)).fetchone()
    metadata = json.loads(previous[0]) if previous else {}
    metadata.update(bootstrap='new', stage=result['stage'], pack_id=route['pack_id'],
                    delivery=result['delivery'], updated_at=now())
    store.policy(request['epoch'])
    store.db.execute("UPDATE meta SET value=? WHERE key=? AND json_extract(value, '$.request_token')=? "
                     "AND json_extract(value, '$.pack_id')=? AND EXISTS(SELECT 1 FROM requests WHERE token=?)",
                     (json.dumps(metadata), meta_key, token, route['pack_id'], token))
    return result
