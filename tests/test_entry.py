import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from jcm import config, entry
from jcm.adapter import hook, recover_sources, register_transcript
from jcm.bootstrap import existing, new
from jcm.follower import follower_status
from jcm.provider import JevProvider
from jcm.store import Store
from jcm.util import JCMError, digest, encode
import test_continuity as fixtures


class EntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'project'; self.root.mkdir()
        self.home = self.base / 'home'
        self.logs = self.base / 'logs'; self.logs.mkdir()
        self.path = self.transcript('current')
        self.stores = []

    def tearDown(self):
        for store in self.stores:
            store.close()
        self.temp.cleanup()

    def transcript(self, session, suffix=''):
        path = self.logs / ('rollout-test-' + session + suffix + '.jsonl')
        path.write_bytes(encode({'type': 'session_meta', 'payload': {'id': session, 'cwd': str(self.root),
                                                    'cli_version': '0.158.0-alpha.2.1'}}) + b'\n')
        return path

    def append(self, text, turn, role='user', path=None):
        item = {'type': 'UserMessage' if role == 'user' else 'AgentMessage', 'id': role + '-' + turn,
                'content': [{'type': 'text' if role == 'user' else 'Text', 'text': text}]}
        if role != 'user':
            item['phase'] = 'final_answer'
        with (path or self.path).open('ab') as stream:
            stream.write(encode({'type': 'event_msg', 'payload': {'type': 'item_completed',
                                                         'turn_id': turn, 'item': item}}) + b'\n')

    def preview(self):
        return entry.preview(self.home, self.root, 'current', roots=[self.logs])

    def store(self, policy=None):
        store = Store(policy or config.load(self.home, self.root)); self.stores.append(store)
        return store

    def choose(self, state, choice):
        return entry.choose(self.home, self.root, state['entry_id'], choice, 'current', install=False, follow=False)

    def prepare(self, choice='from_invocation'):
        self.append('OUTSIDE-CANARY old work', 'old')
        self.append('$astra-continuity', 'invoke')
        state = self.preview()
        self.append('Preview of OUTSIDE-CANARY old work', 'invoke', 'assistant')
        self.append(choice, 'answer')
        result = self.choose(state, choice)
        self.assertEqual(result['stage'], 'ready')
        return self.store(), state

    def test_preview_has_no_registration_blobs_or_provider_and_reuses_anchor(self):
        self.append('private past decision', 'old')
        self.append('$astra-continuity', 'invoke')
        first = self.preview()
        self.assertIn('private past decision', str(first['preview']))
        self.assertEqual(first['stage'], 'awaiting_scope')
        self.assertFalse((self.home / 'registry.sqlite').exists())
        self.assertFalse((self.home / 'stores').exists())
        self.assertFalse((self.root / '.codex').exists())
        persisted = entry.state_path(self.home, self.root, 'current').read_text()
        self.assertNotIn('private past decision', persisted)
        self.assertEqual(self.preview()['entry_id'], first['entry_id'])
        self.append('Which scope includes previous decisions?', 'question')
        self.assertEqual(self.preview()['entry_id'], first['entry_id'])

    def test_from_invocation_excludes_prior_and_preview_derived_data(self):
        store, state = self.prepare()
        self.append('INSIDE-CANARY after selection', 'after')
        recover_sources(store)
        text = '\n'.join(p.read_text() for p in store.blobs.iterdir())
        self.assertNotIn('OUTSIDE-CANARY', text)
        self.assertIn('INSIDE-CANARY', text)
        self.assertEqual(store.config['capture_scope']['turn'], 'invoke')
        self.assertTrue(all(store.material(e)['text'] != 'OUTSIDE-CANARY old work' for e in store.events()))
        repeated = self.choose(state, 'from_invocation')
        self.assertTrue(repeated['already_applied'])
        self.assertEqual(len(store.events()), 3)

    def test_whole_session_includes_prior_and_later(self):
        store, _ = self.prepare('whole_session')
        self.append('INSIDE-CANARY', 'after')
        recover_sources(store)
        self.assertIn('OUTSIDE-CANARY', str([store.material(e) for e in store.events()]))
        self.assertIn('INSIDE-CANARY', str([store.material(e) for e in store.events()]))

    def test_reply_during_wait_included_but_preview_assistant_excluded(self):
        self.append('OUTSIDE-CANARY', 'old')
        self.append('$astra-continuity', 'invoke')
        state = self.preview()
        self.append('Also keep this NEW-CONSTRAINT', 'clarify')
        self.preview()
        self.append('This explanation refers to OUTSIDE-CANARY', 'clarify', 'assistant')
        self.append('from now', 'answer')
        self.choose(state, 'from_invocation')
        store = self.store()
        blobs = '\n'.join(p.read_text() for p in store.blobs.iterdir())
        self.assertNotIn('OUTSIDE-CANARY', blobs)
        self.assertIn('NEW-CONSTRAINT', blobs)

    def test_selection_requires_reply_and_rejects_old_or_foreign_entry(self):
        self.append('$astra-continuity', 'invoke')
        state = self.preview()
        with self.assertRaisesRegex(JCMError, 'USER_SELECTION_REQUIRED'):
            self.choose(state, 'from_invocation')
        self.append('$astra-continuity', 'invoke2')
        second = self.preview()
        self.assertEqual(second['entry_id'], state['entry_id'])
        self.append('now', 'answer')
        forged = {**state, 'entry_id': 'a' * 32}
        with self.assertRaisesRegex(JCMError, 'STALE'):
            self.choose(forged, 'from_invocation')
        with self.assertRaisesRegex(JCMError, 'THIS_SESSION'):
            entry.choose(self.home, self.root, second['entry_id'], 'from_invocation', 'other')

    def test_repeated_bare_call_resumes_pending_original_boundary(self):
        self.append('OUTSIDE-CANARY', 'old')
        self.append('$astra-continuity', 'first')
        first = self.preview()
        self.append('Keep the NEW-WAITING-DECISION', 'during')
        self.append('$astra-continuity', 'reopened')
        second = self.preview()
        self.assertEqual(second['entry_id'], first['entry_id'])
        self.append('now', 'answer')
        self.choose(second, 'from_invocation')
        store = self.store()
        self.assertEqual(store.policy()['capture_scope']['turn'], 'first')
        self.assertIn('NEW-WAITING-DECISION', str([store.material(e) for e in store.events()]))
        self.assertNotIn('OUTSIDE-CANARY', '\n'.join(p.read_text() for p in store.blobs.iterdir()))

    def test_hook_and_fresh_session_recover_only_admitted_history(self):
        store, _ = self.prepare()
        self.append('INSIDE-CANARY', 'after')
        path = self.transcript('fresh')
        self.append('OTHER-PAST-CANARY', 'before', path=path)
        self.append('Continue INSIDE-CANARY', 'request', path=path)
        hook(store, {'hook_event_name': 'SessionStart', 'session_id': 'fresh', 'cwd': str(self.root),
                     'transcript_path': str(path)})
        output = hook(store, {'hook_event_name': 'UserPromptSubmit', 'session_id': 'fresh', 'turn_id': 'request',
                     'cwd': str(self.root), 'transcript_path': str(path), 'prompt': 'Continue INSIDE-CANARY'})
        self.assertIn('bootstrap new', str(output))
        token = store.db.execute("SELECT token FROM requests WHERE session='fresh'").fetchone()[0]
        result = new(store, token, JevProvider(store, transport=fixtures.fake_http))
        self.assertIn('INSIDE-CANARY', str(result))
        text = '\n'.join(p.read_text() for p in store.blobs.iterdir())
        self.assertNotIn('OUTSIDE-CANARY', text)
        self.assertNotIn('OTHER-PAST-CANARY', text)

    def test_explicit_past_other_session_cannot_bypass_scope(self):
        store, _ = self.prepare()
        path = self.transcript('unrelated')
        self.append('OTHER-PAST-CANARY', 'x', path=path)
        existing(store, 'unrelated', path, install=False, follow=False)
        self.assertNotIn('OTHER-PAST-CANARY', str([store.material(e) for e in store.events()]))
        self.assertIsNone(store.capture(session='current', turn='old', kind='user_message', role='user',
            payload={'text': 'OUTSIDE-CANARY'}, snapshot={}, source_key='bypass', identity='bypass'))

    def test_rotation_and_pagination_do_not_reintroduce_prefix(self):
        store, _ = self.prepare()
        old = self.path.read_bytes()
        self.path.unlink(); self.path.write_bytes(old)
        self.append('AFTER-ROTATION', 'rotate')
        recover_sources(store)
        self.assertNotIn('OUTSIDE-CANARY', str([store.material(e) for e in store.events()]))
        self.assertIn('AFTER-ROTATION', str([store.material(e) for e in store.events()]))
        segment = self.transcript('current', '_segment')
        meta = json.loads(segment.read_bytes())
        meta['payload']['history_base'] = {'thread_id': 'current', 'end_ordinal_exclusive': 50}
        segment.write_bytes(encode(meta) + b'\n')
        self.append('PAGINATED-TAIL', 'page', path=segment)
        # A later ordinary recovery must discover this page without asking the
        # user to adopt the previous session again.
        recover_sources(store)
        self.assertIn('PAGINATED-TAIL', str([store.material(e) for e in store.events()]))
        self.assertNotIn('OUTSIDE-CANARY', str([store.material(e) for e in store.events()]))

    def test_missing_anchor_fail_closed(self):
        store, _ = self.prepare()
        self.path.write_bytes(self.path.read_bytes().splitlines(keepends=True)[0])
        self.append('UNANCHORED-TAIL', 'later')
        recover_sources(store)
        self.assertNotIn('UNANCHORED-TAIL', str([store.material(e) for e in store.events()]))
        self.assertIsNotNone(store.db.execute("SELECT 1 FROM gaps WHERE code='SCOPE_ANCHOR_NOT_OBSERVABLE'").fetchone())

    def test_disabled_choice_preserves_scope_and_data(self):
        store, _ = self.prepare()
        scope = store.policy()['capture_scope']
        count = len(store.events())
        store.change_policy(enabled=False)
        self.append('$astra-continuity', 'again')
        state = self.preview()
        self.assertEqual(state['stage'], 'disabled')
        self.append('resume', 'resume')
        self.assertFalse(store.policy(require_enabled=False)['enabled'])
        self.assertEqual(self.choose(state, 'resume')['stage'], 'awaiting_task')
        self.assertEqual(store.policy()['capture_scope'], scope)
        self.assertEqual(len(store.events()), count)

    def test_bare_hook_chooses_entry_normal_hook_uses_automatic_recovery(self):
        store = self.store(config.enable(self.home, self.root, [self.logs]))
        base = {'hook_event_name': 'UserPromptSubmit', 'session_id': 'current', 'cwd': str(self.root)}
        output = hook(store, {**base, 'turn_id': 'bare', 'prompt': '$astra-continuity'})
        self.assertIn('entry preview', str(output))
        self.assertNotIn('bootstrap new --request-token', str(output))
        output = hook(store, {**base, 'turn_id': 'work', 'prompt': 'Implement a cache'})
        self.assertIn('bootstrap new --request-token', str(output))

    def test_bare_detection_only_exact_supported_forms(self):
        self.assertTrue(entry.bare_invocation('$astra-continuity'))
        self.assertTrue(entry.bare_invocation('[$astra-continuity](/tmp/skills/astra-continuity/SKILL.md)'))
        for text in ('Please explain $astra-continuity', 'quoted: $astra-continuity',
                     '[$astra-continuity](https://evil/astra-continuity/SKILL.md)', '$astra-continuity implement cache'):
            self.assertFalse(entry.bare_invocation(text))

    def test_permission_failure_rolls_back_registration(self):
        original = config.atomic_write
        def denied(path, data):
            if Path(path) == self.root / '.codex/jcm.json':
                raise PermissionError()
            return original(path, data)
        with patch('jcm.config.atomic_write', side_effect=denied):
            with self.assertRaises(PermissionError):
                config.enable(self.home, self.root, [self.logs])
        with self.assertRaisesRegex(JCMError, 'PROJECT_NOT_ENABLED'):
            config.load(self.home, self.root)
        self.assertFalse(list((self.home / 'profiles').glob('*.json')))

    def test_late_profile_failure_restores_existing_pointer(self):
        pointer = self.root / '.codex/jcm.json'; pointer.parent.mkdir()
        pointer.write_text('existing unrelated pointer')
        original = config.atomic_write
        def denied(path, data):
            if Path(path).parent == self.home / 'profiles':
                raise PermissionError()
            return original(path, data)
        with patch('jcm.config.atomic_write', side_effect=denied):
            with self.assertRaises(PermissionError):
                config.enable(self.home, self.root, [self.logs])
        self.assertEqual(pointer.read_text(), 'existing unrelated pointer')
        with self.assertRaisesRegex(JCMError, 'PROJECT_NOT_ENABLED'):
            config.load(self.home, self.root)

    def test_follower_permission_error_uses_recent_heartbeat(self):
        store = self.store(config.enable(self.home, self.root, [self.logs]))
        store.db.execute('INSERT INTO meta VALUES (?,?)', ('follower:source', encode(
            {'pid': 123, 'state': 'running', 'heartbeat': time.time()}).decode()))
        with patch('jcm.follower.os.kill', side_effect=PermissionError()):
            result = follower_status(store, 'source')
        self.assertTrue(result['running'])
        self.assertEqual(result['process_probe'], 'permission_denied')

    def work_fixture(self):
        policy = config.enable(self.home, self.root, [self.logs])
        store = self.store(policy)
        past = self.transcript('past')
        self.append('Implement network pause recovery. Keep protocol=1.', 'network', path=past)
        self.append('Network pause implementation is complete; interruption test remains.', 'network', 'assistant', past)
        self.append('Implement a red settings theme. Theme edits only.', 'theme', path=past)
        self.append('The settings theme is complete and verified.', 'theme', 'assistant', past)
        self.append('For all project work preserve compatibility.', 'constraint', path=past)
        existing(store, 'past', install=False, follow=False)
        self.append('$astra-continuity', 'invoke')
        state = self.preview()
        return store, state

    def task_transport(self, body, key):
        payload = json.loads(body)
        result = fixtures.fake_http(body, key)
        sources = payload['state'].get('sources', [])
        for name, answer in result['answers'].items():
            if name.startswith('work_'):
                text = sources[int(name.split('_')[1])]['text']
                answer['choice'] = 'context' if text.startswith('For all') else 'task'
                answer['probabilities'] = {v: float(v == answer['choice']) for v in payload['questions'][name]['criteria']}
        candidates = payload['state'].get('candidates', [])
        for n, candidate in enumerate(candidates):
            theme = 'settings theme' in candidate['text']
            if f'relevance_{n}' in result['answers']:
                # The mock separates an unrelated task while retaining a shared constraint.
                score = 0 if theme else 3
                result['answers'][f'relevance_{n}']['score'] = score
                result['answers'][f'relevance_{n}']['probabilities'] = {str(i): float(i == score) for i in range(4)}
                result['answers'][f'omission_{n}']['noul'] = 0 if theme else 1
        return result

    def test_work_list_and_selection_use_source_ids_and_exclude_unrelated_task(self):
        store, state = self.work_fixture()
        provider = JevProvider(store, transport=self.task_transport)
        menu = entry.tasks(store, state['entry_id'], 'current', provider=provider)
        self.assertEqual(len(menu['tasks']), 2)
        self.assertTrue(menu['decisions'])
        network = next(t for t in menu['tasks'] if 'network' in t['title_source'])
        self.assertEqual(network['status'], 'needs_reconciliation')
        self.assertIn('interruption test remains', str(network['latest_evidence']))
        self.assertEqual(store.db.execute('SELECT COUNT(*) FROM packs').fetchone()[0], 0)
        self.append('Continue network pause recovery', 'choice')
        with patch('jcm.follower.start', return_value={'running': False}):
            result = entry.select_task(store, state['entry_id'], network['id'], 'current', provider)
        selected = result['pack']['selected_records']
        self.assertIn('protocol=1', str(selected))
        self.assertIn('preserve compatibility', str(selected))
        self.assertNotIn('settings theme', str(selected))
        self.assertEqual(result['pack']['selected_task']['task_id'], network['id'])
        again = entry.select_task(store, state['entry_id'], network['id'], 'current', provider)
        self.assertEqual(again['pack']['pack_id'], result['pack']['pack_id'])

    def test_task_not_offered_and_stale_epoch_rejected(self):
        store, state = self.work_fixture()
        provider = JevProvider(store, transport=self.task_transport)
        menu = entry.tasks(store, state['entry_id'], 'current', provider=provider)
        self.append('first', 'choice')
        with self.assertRaisesRegex(JCMError, 'NOT_OFFERED'):
            entry.select_task(store, state['entry_id'], 'a'*64, 'current', provider)
        store.change_policy()
        with self.assertRaisesRegex(JCMError, 'STALE'):
            entry.select_task(store, state['entry_id'], menu['tasks'][0]['id'], 'current', provider)

    def test_new_task_does_not_dispatch_or_force_old_work(self):
        store, state = self.work_fixture()
        self.append('Start a new task', 'choice')
        result = self.choose(state, 'new_task')
        self.assertEqual(result['action'], 'ask_for_new_task')
        self.assertIsNone(result['read_command'])
        self.assertEqual(store.db.execute('SELECT COUNT(*) FROM packs').fetchone()[0], 0)
        self.assertIsNone(entry.pending(store, 'current'))
        self.assertEqual(len([e for e in store.events() if e['session'] == 'past']), 5)

    def test_concrete_request_during_menu_uses_normal_recovery(self):
        store, state = self.work_fixture()
        self.append('Please implement the disk cache now', 'work')
        result = self.choose(state, 'continue_request')
        self.assertEqual(result['action'], 'continue_current_request')
        self.assertIn('bootstrap new --request-token', result['read_command'])
        self.assertIsNone(entry.pending(store, 'current'))

    def test_task_pages_do_not_cap_candidates_and_search_reaches_old_tasks(self):
        store, state = self.work_fixture()
        past = self.logs / 'rollout-test-past.jsonl'
        for n in range(12):
            self.append('Implement module number ' + str(n), 'module' + str(n), path=past)
        provider = JevProvider(store, transport=self.task_transport)
        page1 = entry.tasks(store, state['entry_id'], 'current', provider=provider)
        page3 = entry.tasks(store, state['entry_id'], 'current', page=3, provider=provider)
        self.assertEqual(page1['total'], 14)
        self.assertEqual(page1['next_page'], 2)
        self.assertEqual(len(page3['tasks']), 2)
        found = entry.tasks(store, state['entry_id'], 'current', search='network', provider=provider)
        self.assertEqual(found['total'], 1)
        self.assertIn('network', found['tasks'][0]['title_source'])

    def test_forget_removes_work_menu_derivatives_and_tombstone_survives(self):
        store, state = self.work_fixture()
        entry.tasks(store, state['entry_id'], 'current', provider=JevProvider(store, transport=self.task_transport))
        self.assertTrue(store.db.execute("SELECT 1 FROM meta WHERE key LIKE 'entry_catalogue:%'").fetchone())
        store.forget_session('past')
        self.assertFalse(store.db.execute("SELECT 1 FROM meta WHERE key LIKE 'entry_catalogue:%'").fetchone())
        self.assertTrue(store.db.execute("SELECT 1 FROM tombstones WHERE session='past'").fetchone())
        self.assertNotIn('red settings theme', '\n'.join(p.read_text() for p in store.blobs.iterdir()))
        with self.assertRaisesRegex(JCMError, 'POLICY_CHANGED'):
            entry.tasks(store, state['entry_id'], 'current', provider=JevProvider(store, transport=self.task_transport))

    def test_default_registry_activation_survives_protected_project_directory(self):
        original = config.atomic_write
        def denied(path, data):
            if Path(path) == self.root / '.codex/jcm.json':
                raise PermissionError()
            return original(path, data)
        with patch.dict(os.environ, {'JCM_HOME': str(self.home)}), patch('jcm.config.atomic_write', side_effect=denied):
            policy = config.enable(self.home, self.root, [self.logs])
        self.assertEqual(policy['project_reference'], 'default_registry_only')
        self.assertEqual(config.load(self.home, self.root)['repo_id'], policy['repo_id'])

    def test_schema_upgrade_preserves_disabled_profile_records_and_tombstones(self):
        policy = config.enable(self.home, self.root, [self.logs])
        store = self.store(policy)
        self.append('KEEP-LEGACY-RECORD', 'keep')
        existing(store, 'current', install=False, follow=False)
        store.forget_session('deleted')
        store.change_policy(enabled=False)
        store.db.execute('PRAGMA user_version=2')
        upgraded = self.store()
        self.assertFalse(upgraded.policy(require_enabled=False)['enabled'])
        self.assertNotIn('capture_scope', upgraded.policy(require_enabled=False))
        self.assertIn('KEEP-LEGACY-RECORD', str([upgraded.material(e) for e in upgraded.events()]))
        self.assertTrue(upgraded.db.execute("SELECT 1 FROM tombstones WHERE session='deleted'").fetchone())
        self.assertEqual(upgraded.db.execute('PRAGMA user_version').fetchone()[0], 4)

    def test_known_host_settings_event_is_not_a_public_record_gap(self):
        from jcm.adapter import public_item
        self.assertIsNone(public_item({'type': 'event_msg', 'payload': {'type': 'thread_settings_applied'}}))
        with self.assertRaisesRegex(JCMError, 'UNKNOWN_TRANSCRIPT_EVENT'):
            public_item({'type': 'event_msg', 'payload': {'type': 'unsupported_future_event'}})

    def test_scope_exclusion_is_not_reported_as_forgotten(self):
        store, _ = self.prepare()
        result = hook(store, {'hook_event_name': 'Stop', 'session_id': 'current', 'turn_id': 'invoke',
            'cwd': str(self.root), 'last_assistant_message': 'preview-derived content'})
        self.assertEqual(result, {})
        store.forget_session('current')
        result = hook(store, {'hook_event_name': 'UserPromptSubmit', 'session_id': 'current', 'turn_id': 'after',
            'cwd': str(self.root), 'prompt': 'a forgotten session'})
        self.assertIn('forgotten', str(result))

    def test_preview_begins_with_recent_context_and_all_pages_are_available(self):
        for n in range(12):
            self.append('work ' + str(n), 'turn' + str(n))
        self.append('$astra-continuity', 'invoke')
        result = self.preview()
        self.assertEqual(result['preview'][1]['text'], 'work 11')
        self.assertEqual(result['next_page'], 2)
        page2 = entry.preview(self.home, self.root, 'current', page=2, roots=[self.logs])
        self.assertEqual(page2['entry_id'], result['entry_id'])
        self.assertIn('work 0', str(page2['preview']))

    def test_long_preview_is_lossless_paged_without_persisting_text(self):
        text = '한글 원문과 여러 줄의 기록입니다.\n' * 1300
        self.append(text, 'long')
        self.append('$astra-continuity', 'invoke')
        page, fragments = 1, []
        while page:
            result = entry.preview(self.home, self.root, 'current', page=page, roots=[self.logs])
            self.assertLess(len(encode(result)), 24000)
            fragments.extend(r for r in result['preview'] if r['turn'] == 'long')
            page = result['next_page']
        self.assertEqual(''.join(r['text'] for r in sorted(fragments, key=lambda r: r['start'])), text)
        self.assertNotIn('한글 원문', entry.state_path(self.home, self.root, 'current').read_text())
        self.assertFalse((self.home / 'stores').exists())

    def test_follower_discovers_new_page_without_any_user_request(self):
        store, _ = self.prepare()
        result = existing(store, 'current', install=False, follow=True)
        self.assertTrue(result['follower']['running'])
        try:
            segment = self.transcript('current', '_next')
            meta = json.loads(segment.read_bytes())
            meta['payload']['history_base'] = {'thread_id': 'current', 'end_ordinal_exclusive': 50}
            segment.write_bytes(encode(meta) + b'\n')
            self.append('AUTOMATIC-PAGE-TAIL', 'page-tail', path=segment)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if any('AUTOMATIC-PAGE-TAIL' in store.material(e)['text'] for e in store.events()):
                    break
                time.sleep(.1)
            self.assertIn('AUTOMATIC-PAGE-TAIL', str([store.material(e) for e in store.events()]))
            self.assertNotIn('OUTSIDE-CANARY', '\n'.join(p.read_text() for p in store.blobs.iterdir()))
        finally:
            store.change_policy(enabled=False)
            deadline = time.monotonic() + 4
            keys = [r[0] for r in store.db.execute('SELECT key FROM sources')]
            while time.monotonic() < deadline and any(follower_status(store, k)['running'] for k in keys):
                time.sleep(.05)

    def test_rotation_between_scope_resolution_and_capture_cannot_leak_prefix(self):
        from jcm import scope
        from jcm.adapter import recover_source
        store, _ = self.prepare()
        row = dict(store.db.execute('SELECT * FROM sources').fetchone())
        store.db.execute('UPDATE sources SET offset=0,prefix_hash=? WHERE key=?', (digest(b''), row['key']))
        original = self.path.read_bytes()
        anchor = store.policy()['capture_scope']
        old_resolver = scope.start_offset
        rotated = False
        def rotate_before_resolve(store_arg, row_arg, stream=None):
            nonlocal rotated
            if not rotated:
                rotated = True
                self.path.rename(self.path.with_suffix('.old'))
                # Remove the excluded prefix so the new generation's boundary
                # would wrongly authorize old-generation bytes without a proof.
                lines = original.splitlines(keepends=True)
                self.path.write_bytes(lines[0] + b''.join(lines[2:]))
            return old_resolver(store_arg, row_arg, stream)
        with patch('jcm.scope.start_offset', side_effect=rotate_before_resolve):
            with self.assertRaisesRegex(JCMError, 'SCOPE_SOURCE_CHANGED_DURING_READ'):
                recover_source(store, row)
        self.assertNotIn('OUTSIDE-CANARY', '\n'.join(p.read_text() for p in store.blobs.iterdir()))
        recover_sources(store)
        self.assertEqual(store.policy()['capture_scope'], anchor)
        self.assertNotIn('OUTSIDE-CANARY', '\n'.join(p.read_text() for p in store.blobs.iterdir()))

    def test_choice_turn_preview_excluded_but_subsequent_work_is_recorded(self):
        self.append('OUTSIDE-CANARY', 'old')
        self.append('$astra-continuity', 'invoke')
        state = self.preview()
        self.append('Manage from now and implement the requested function', 'answer')
        self.preview()
        self.append('Transient preview mentions OUTSIDE-CANARY', 'answer', 'assistant')
        result = self.choose(state, 'from_invocation')
        self.assertIn('bootstrap new --request-token', result['read_command'])
        store = self.store()
        self.append('IMPLEMENTATION-COMPLETED after choosing', 'answer', 'assistant')
        recover_sources(store)
        content = '\n'.join(p.read_text() for p in store.blobs.iterdir())
        self.assertNotIn('OUTSIDE-CANARY', content)
        self.assertIn('IMPLEMENTATION-COMPLETED', content)
        output = hook(store, {'hook_event_name': 'Stop', 'session_id': 'current', 'turn_id': 'answer',
            'cwd': str(self.root), 'last_assistant_message': 'FINAL-IMPLEMENTATION-REPORT'})
        self.assertEqual(output, {})
        self.assertIn('FINAL-IMPLEMENTATION-REPORT', str([store.material(e) for e in store.events()]))
