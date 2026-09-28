"""Read-only capture state shared by status, menus and recovery results."""
import os
from pathlib import Path

from .util import digest, now


def capture_health(store):
    from .adapter import PARSER
    policy = store.policy(require_enabled=False)
    sources = {}
    for row in store.db.execute('SELECT * FROM sources'):
        row = dict(row)
        identity = (row['session'], row['path'])
        canonical = digest([row['path'], row['session'], PARSER])
        if identity not in sources or row['key'] == canonical:
            sources[identity] = row
    result, errors = [], set()
    for row in sources.values():
        reason = None
        try:
            stat = Path(row['path']).stat()
            size = stat.st_size
            rewritten = stat.st_ino != row['inode'] or size < row['offset']
            backlog = size if rewritten else max(0, size - row['offset'])
        except OSError:
            size, backlog, rewritten = None, None, False
            reason = 'TRANSCRIPT_UNAVAILABLE'
        state = row['status']
        if state.startswith('blocked:'):
            reason = state.split(':', 1)[1]
        elif state == 'malformed_line':
            reason = 'TRANSCRIPT_MALFORMED_LINE'
        if reason:
            errors.add(reason)
        result.append({'session': row['session'], 'source_key': row['key'], 'path': row['path'],
                       'offset': row['offset'], 'source_bytes': size, 'backlog_bytes': backlog,
                       'state': state, 'error': reason, 'generation_changed': rewritten})
    backlog = sum(s['backlog_bytes'] or 0 for s in result)
    state = ('disabled' if not policy['enabled'] else 'blocked' if errors else
             'not_registered' if not result else 'catching_up' if backlog or any(s['generation_changed'] for s in result)
             else 'caught_up')
    return {'state': state, 'observed_at': now(), 'backlog_bytes': backlog,
            'backlog_complete': all(s['backlog_bytes'] is not None for s in result),
            'current_errors': sorted(errors), 'sources': result,
            'scope': 'registered_sources_at_observation', 'history_coverage': 'partial',
            'next_action': 'resume_recording' if state == 'disabled' else 'sync' if state in ('blocked', 'catching_up')
                           else 'adopt_current_session' if state == 'not_registered' else None}


def blocked_result(store, error=None):
    capture = capture_health(store)
    gaps = sorted(set(capture['current_errors'] + ([error] if error else [])))
    return {'origin': 'jcm', 'stage': 'blocked', 'capture': capture, 'gaps': gaps,
            'error': error or (gaps[0] if gaps else 'CAPTURE_NOT_READY'),
            'continuation_ready': False, 'read_command': None, 'request_token': None}


def doctor(store):
    from .coordinator import status
    result = status(store)
    checks = {'storage_readable': os.access(store.path, os.R_OK),
              'storage_writable': os.access(store.path, os.W_OK),
              'provider_credential_present': bool(os.environ.get('TYPESAFE_API_KEY')),
              'registered_source_count': len(result['capture']['sources']),
              'delegation_support': 'host_FunctionCallOutput_codex_app_send_message_to_thread',
              'unsupported_request_count': store.db.execute("SELECT COUNT(*) FROM events WHERE role='unsupported_request'").fetchone()[0]}
    return {**result, 'diagnostics': checks, 'mutations': False, 'provider_called': False,
            'recommended_action': result['capture']['next_action']}
