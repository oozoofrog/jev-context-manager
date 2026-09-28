"""Explicit skill entry: transient preview, durable choices, source-bound recovery."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import uuid

from . import config
from .adapter import MAX_LINE_BYTES, public_item
from .bootstrap import discover_all, session_id
from .util import JCMError, atomic_write, digest, encode, identifier, private_dir
from .transcript_io import read_record
from .request_source import current_request


def bare_invocation(text):
    if not isinstance(text, str):
        return False
    text = text.strip()
    if text in ('$astra-continuity', '$jev-context-manager:astra-continuity'):
        return True
    # Codex skill mentions carry the actual skill file, not an arbitrary quoted name.
    return bool(re.fullmatch(r'\[\$(?:jev-context-manager:)?astra-continuity\]\((?:/|file://)[^\n()]+/astra-continuity/SKILL\.md\)', text))


class Reader:
    def __init__(self, policy):
        self.config, self.gaps = policy, []

    def gap(self, code, detail=''):
        self.gaps.append(code)


def session_items(policy, session, path=None, requests_only=False):
    reader = Reader(policy)
    items = []
    for source in discover_all(reader, session, path):
        with source.open('rb') as stream:
            while line := read_record(stream):
                if not line['complete']:
                    reader.gap('TRANSCRIPT_PARTIAL_LINE')
                    break
                try:
                    if line.get('error'):
                        raise JCMError(line['error'])
                    item = public_item(line['value'], session)
                except JCMError as exc:
                    reader.gap(str(exc))
                    continue
                if item and (not requests_only or item['role'] in ('user', 'unsupported_request')):
                    items.append(item)
    return items, sorted(set(reader.gaps))


def reader_policy(home, root, roots=None):
    try:
        return config.load(home, root)
    except JCMError as exc:
        if str(exc) != 'PROJECT_NOT_ENABLED':
            raise
    return {'root': str(Path(root).resolve(strict=True)), 'home': str(Path(home).expanduser().resolve()),
            'transcript_roots': [str(Path(p).expanduser().resolve()) for p in (roots if roots is not None else
                [Path(os.environ.get('CODEX_HOME', '~/.codex')).expanduser() / 'sessions'])]}


def state_path(home, root, session):
    return Path(home).expanduser().resolve() / 'entry' / (digest([str(Path(root).resolve()), session]) + '.json')


@contextmanager
def locked(home, root, session):
    path = state_path(home, root, session)
    private_dir(path.parent)
    lock = path.with_suffix('.lock')
    if path.is_symlink() or lock.is_symlink():
        raise JCMError('SYMLINK_ENTRY_REFUSED')
    with lock.open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield path


def preview(home, root, session=None, path=None, page=1, roots=None):
    session = session_id(session)
    policy = reader_policy(home, root, roots)
    with locked(home, root, session) as target:
        state = json.loads(target.read_text()) if target.exists() else None
        items, gaps = session_items(policy, session, path)
        current = current_request(items)
        if bare_invocation(current['payload']['text']):
            if state and state['stage'] == 'awaiting_scope':
                # Reopening the skill while a scope choice is pending resumes
                # that choice. Moving its boundary would silently drop messages
                # written since the original invocation.
                if current['turn'] not in state['preview_turns']:
                    state['preview_turns'].append(current['turn'])
            elif not state or state['anchor']['identity'] != current['identity']:
                state = {'id': uuid.uuid4().hex, 'session': session, 'root': policy['root'],
                         'anchor': {k: current[k] for k in ('turn', 'identity')},
                         'stage': 'disabled' if policy.get('enabled') is False else
                                  'awaiting_task' if 'repo_id' in policy else 'awaiting_scope',
                         'epoch': policy.get('epoch'), 'preview_turns': [current['turn']],
                         'transcript_roots': policy['transcript_roots']}
        elif not state or state['stage'] not in ('awaiting_scope', 'awaiting_task', 'disabled'):
            raise JCMError('CURRENT_REQUEST_IS_NOT_BARE_SKILL')
        elif current['turn'] not in state['preview_turns']:
            state['preview_turns'].append(current['turn'])
        atomic_write(target, encode(state))
        result = {'origin': 'jcm', 'entry_id': state['id'], 'stage': state['stage'],
                  'session_id': session, 'scope': policy.get('capture_scope', 'legacy_project'),
                  'coverage': 'partial', 'gaps': gaps, 'preview_persisted': False}
        if 'repo_id' in policy:
            from .store import Store
            from .health import capture_health
            store = Store(policy)
            try:
                result['capture'] = capture_health(store)
            finally:
                store.close()
        if state['stage'] == 'awaiting_scope':
            if type(page) is not int or page < 1:
                raise JCMError('INVALID_PREVIEW_PAGE')
            from .batching import text_spans
            pages, current = [], []
            for item in reversed(items):
                if item['role'] not in ('user', 'assistant'):
                    continue
                text = item['payload']['text']
                for start, end in text_spans(text, lambda a, b: len(encode(text[a:b])) <= 6000):
                    fragment = {'role': item['role'], 'text': text[start:end], 'turn': item['turn'],
                                'source_identity': item['identity'], 'start': start, 'end': end, 'total_chars': len(text)}
                    if current and (len(current) >= 8 or len(encode(current + [fragment])) > 20000):
                        pages.append(current)
                        current = []
                    current.append(fragment)
            if current:
                pages.append(current)
            result.update(choices=['from_invocation', 'whole_session'],
                          preview_order='newest_first',
                          preview=pages[page-1] if page <= len(pages) else [],
                          next_page=page + 1 if page < len(pages) else None,
                          preview_use='Summarize current work briefly; do not copy this preview into managed records.')
        elif state['stage'] == 'disabled':
            result['choices'] = ['resume', 'keep_disabled']
        else:
            result['choices'] = ['select_task', 'new_task']
        return result


def pending(store, session):
    path = state_path(store.config['home'], store.config['root'], session)
    if not path.exists():
        return None
    state = json.loads(path.read_text())
    return state if state['stage'] in ('awaiting_scope', 'awaiting_task', 'disabled') else None


def choose(home, root, entry_id, choice, session=None, binding=None, install=True, follow=True):
    session = session_id(session)
    with locked(home, root, session) as target:
        if not target.exists():
            raise JCMError('ENTRY_NOT_FOUND_IN_THIS_SESSION')
        state = json.loads(target.read_text())
        if state['id'] != identifier(entry_id):
            raise JCMError('ENTRY_SELECTION_STALE')
        if state.get('choice') == choice and state['stage'] == 'ready':
            policy = config.load(home, root)
            return {**state['result'], 'enabled': policy['enabled'], 'already_applied': True}
        policy = reader_policy(home, root)
        items, _ = session_items({**policy, 'transcript_roots': state['transcript_roots']}, session)
        users = [item for item in items if item['role'] == 'user']
        current_request(items)
        if not users or users[-1]['turn'] == state['anchor']['turn']:
            raise JCMError('USER_SELECTION_REQUIRED')
        if bare_invocation(users[-1]['payload']['text']):
            raise JCMError('ENTRY_SELECTION_STALE')
        seen = False
        for item in items:
            if item['turn'] == state['anchor']['turn']:
                seen = True
            if seen and item['turn'] not in state['preview_turns']:
                state['preview_turns'].append(item['turn'])
        atomic_write(target, encode(state))
        if state['stage'] == 'awaiting_scope':
            if choice not in ('from_invocation', 'whole_session'):
                raise JCMError('INVALID_SCOPE_CHOICE')
            if 'repo_id' in policy and policy.get('capture_scope', {}).get('entry_id') != entry_id:
                raise JCMError('PROJECT_CHANGED_DURING_SELECTION')
            if 'repo_id' in policy and policy['capture_scope']['mode'] != choice:
                raise JCMError('ENTRY_SCOPE_ALREADY_APPLIED')
            scope = {'mode': choice, 'session': session, **state['anchor'],
                     'preview_turns': [t for t in state['preview_turns'] if t != users[-1]['turn']],
                     'preview_ids': [i['identity'] for i in items if i['turn'] == users[-1]['turn'] and i['role'] != 'user'],
                     'entry_id': entry_id}
            policy = config.enable(home, root, transcript_roots=state['transcript_roots'], capture_scope=scope)
        elif state['stage'] == 'disabled':
            if choice == 'keep_disabled':
                return {'stage': 'disabled', 'enabled': False}
            if choice != 'resume' or policy.get('epoch') != state['epoch']:
                raise JCMError('ENTRY_SELECTION_STALE')
            from .store import Store
            with_store = Store(policy)
            try:
                policy = with_store.change_policy(enabled=True)
            finally:
                with_store.close()
            state.update(stage='awaiting_task', epoch=policy['epoch'])
            atomic_write(target, encode(state))
            return {'stage': 'awaiting_task', 'entry_id': entry_id, 'scope_preserved': True}
        elif state['stage'] == 'awaiting_task' and choice in ('new_task', 'continue_request'):
            if policy.get('epoch') != state['epoch']:
                raise JCMError('ENTRY_SELECTION_STALE')
            from .store import Store
            from .scope import admit_prompt
            from .bootstrap import existing
            store = Store(policy)
            try:
                admit_prompt(store, session, state['anchor']['turn'], state['anchor']['identity'])
                captured = existing(store, session, install=install, follow=follow)
                if captured['stage'] == 'blocked':
                    return captured
            finally:
                store.close()
            state.update(stage='ready', choice=choice, result={'stage': 'ready', 'action':
                         'ask_for_new_task' if choice == 'new_task' else 'continue_current_request',
                         'read_command': captured['read_command'] if choice == 'continue_request' else None,
                         'capture': captured['capture'], 'records_preserved': True})
            atomic_write(target, encode(state))
            return state['result']
        else:
            raise JCMError('INVALID_ENTRY_CHOICE')
        if binding:
            from .plugin import bind
            policy = bind(policy, binding, migrate=True)
        from .store import Store
        from .bootstrap import existing
        store = Store(policy)
        try:
            result = existing(store, session, install=install, follow=follow)
            if result['stage'] == 'blocked':
                return result
        finally:
            store.close()
        state.update(stage='ready', choice=choice, result={'stage': 'ready', 'scope': choice,
                     'capture': result['capture'], 'follower': result['follower'], 'coverage': result['coverage'],
                     'project_reference': policy.get('project_reference', 'legacy'),
                     'read_command': result['read_command'],
                     'gaps': result['gaps'], 'action': 'scope_applied_no_task_started'})
        atomic_write(target, encode(state))
        return state['result']


def tasks(store, entry_id, session=None, page=1, search=None, provider=None):
    session = session_id(session)
    with locked(store.config['home'], store.config['root'], session) as target:
        state = json.loads(target.read_text()) if target.exists() else {}
        if state.get('id') != entry_id or state.get('stage') != 'awaiting_task':
            raise JCMError('ENTRY_SELECTION_STALE')
        epoch = store.policy()['epoch']
        if state['epoch'] != epoch:
            raise JCMError('ENTRY_POLICY_CHANGED')
        # Capture the current session, but never replace the bare invocation with
        # an arbitrary old task. No pack is dispatched until a choice is made.
        from .bootstrap import existing
        from .scope import admit_prompt
        admit_prompt(store, session, state['anchor']['turn'], state['anchor']['identity'])
        captured = existing(store, session, install=False, follow=False)
        for event in store.events():
            if event['session'] == session and event['turn'] in state['preview_turns']:
                store.db.execute('INSERT OR IGNORE INTO meta VALUES (?,?)', ('entry_control:' + event['id'], 'true'))
        from .worklist import catalogue
        revision = store.db.execute('SELECT COALESCE(MAX(seq),0) FROM events WHERE role IN (\'user\',\'assistant\',\'tool\')').fetchone()[0]
        cache_key = 'entry_catalogue:' + entry_id
        from .snapshot import snapshot
        fingerprint = snapshot(store.config['root'])['fingerprint']
        cached = store.db.execute('SELECT value FROM meta WHERE key=?', (cache_key,)).fetchone()
        cached = json.loads(cached[0]) if cached else None
        if cached and cached['epoch'] == epoch and cached['revision'] == revision and cached['fingerprint'] == fingerprint:
            result = cached['result']
        else:
            result = catalogue(store, provider)
            store.save_metadata(cache_key,
                {'epoch': epoch, 'revision': revision, 'fingerprint': fingerprint, 'result': result}, epoch)
        all_tasks = result['tasks']
        filtered = [t for t in all_tasks if not search or search.casefold() in t['title_source'].casefold()]
        if type(page) is not int or page < 1:
            raise JCMError('INVALID_TASK_PAGE')
        shown = filtered[(page-1)*6:page*6]
        state['offered'] = sorted(set(state.get('offered', [])) | {t['id'] for t in shown})
        atomic_write(target, encode(state))
        # Display previews are not raw-source delivery. The exact source remains
        # addressable and task selection recovers its full relevant evidence.
        import shlex
        def preview_material(material):
            text = material.get('text', '')
            return {**material, 'text': text[:1400], 'preview': len(text) > 1400,
                    'total_chars': len(text), 'read_command': shlex.join(store.config['cli_argv'] +
                        ['inspect', '--record', material['event_id'], '--raw'])}
        shown = [{**task, 'title_source': task['title_source'][:600],
                  'source': preview_material(task['source']),
                  'latest_evidence': [preview_material(e) for e in task['latest_evidence']]} for task in shown]
        result = {**result, 'snapshot': {k:v for k,v in result['snapshot'].items() if k != 'files'}}
        from .health import capture_health
        capture = capture_health(store)
        gaps = sorted(set(result['gaps'] + capture['current_errors'] + ([captured['error']] if captured.get('error') else [])))
        return {**result, 'tasks': shown, 'total': len(filtered), 'entry_id': entry_id,
                'capture': capture, 'gaps': gaps, 'quality': 'degraded' if gaps else result['quality'],
                'judgment_quality': result['quality'], 'coverage': 'partial',
                'continuation_ready': captured['stage'] == 'captured' and capture['state'] == 'caught_up',
                'stage': 'awaiting_task', 'new_task_available': True,
                'next_page': page + 1 if len(filtered) > page*6 else None}


def select_task(store, entry_id, task_id, session=None, provider=None):
    session = session_id(session)
    with locked(store.config['home'], store.config['root'], session) as target:
        state = json.loads(target.read_text()) if target.exists() else {}
        if state.get('id') != entry_id or task_id not in state.get('offered', []):
            raise JCMError('TASK_NOT_OFFERED_FOR_THIS_ENTRY')
        if state.get('stage') == 'ready' and state.get('choice') == task_id:
            from .coordinator import read_pack
            return read_pack(store, state['pack_id'])
        if state.get('stage') != 'awaiting_task' or state['epoch'] != store.policy()['epoch']:
            raise JCMError('ENTRY_SELECTION_STALE')
        from .bootstrap import existing, new
        captured = existing(store, session, install=True, follow=True)
        if captured['stage'] != 'captured' or not captured['request_token']:
            from .health import blocked_result
            return blocked_result(store, captured.get('error') or captured.get('request_error'))
        token = captured['request_token']
        request = store.resolve_request(token)
        if store.event(request['event_id'])['turn'] == state['anchor']['turn']:
            raise JCMError('USER_SELECTION_REQUIRED')
        if bare_invocation(store.material(store.event(request['event_id']))['text']):
            raise JCMError('ENTRY_SELECTION_STALE')
        store.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('entry_control:' + request['event_id'], 'true'))
        store.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('entry_selection:' + token,
            encode({'task_id': task_id, 'entry_id': entry_id}).decode()))
        result = new(store, token, provider)
        if result['stage'] != 'blocked':
            pack_id = result.get('pack', {}).get('pack_id') or result.get('pack_id')
            state.update(stage='ready', choice=task_id, pack_id=pack_id)
            atomic_write(target, encode(state))
        return result
