import json
import os
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import test_continuity as fixtures
from jcm.adapter import hook, internal_command, recover_source
from jcm.bootstrap import existing, new, prepare_new
from jcm.follower import follower_status
from jcm.util import JCMError


class BootstrapTests(unittest.TestCase):
    setUp = fixtures.ContinuityTests.setUp
    transcript = fixtures.ContinuityTests.transcript
    user_line = fixtures.ContinuityTests.user_line
    capture = fixtures.ContinuityTests.capture
    request = fixtures.ContinuityTests.request
    provider = fixtures.ContinuityTests.provider

    def tearDown(self):
        self.store.change_policy(enabled=False)
        rows = self.store.db.execute('SELECT key FROM sources').fetchall()
        for _ in range(30):
            if not any(follower_status(self.store, r[0])['running'] for r in rows):
                break
            time.sleep(.05)
        fixtures.ContinuityTests.tearDown(self)

    def history(self):
        path = self.transcript()
        with path.open('ab') as stream:
            stream.write(self.user_line('protocol_version=1'))
        return path

    def test_existing_backfills_idempotently_without_fabricating_snapshot(self):
        path = self.history()
        result = existing(self.store, 'prior', path, follow=False)
        self.assertEqual(result['new_events'], 1)
        self.assertFalse(result['provider_called'])
        self.assertEqual(json.loads(self.store.events()[0]['snapshot']), {})
        again = existing(self.store, 'prior', path, follow=False)
        self.assertEqual(again['new_events'], 0)
        self.assertEqual(result['request_token'], again['request_token'])
        self.assertEqual(again['hooks']['changed'], False)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 1)

    def test_current_session_discovery_and_ambiguous_environment(self):
        path = self.history()
        path.rename(path.with_name('rollout-date-prior.jsonl'))
        with patch.dict(os.environ, {'CODEX_THREAD_ID': 'prior', 'CODEX_SESSION_ID': 'prior'}):
            self.assertEqual(existing(self.store, follow=False)['session_id'], 'prior')
        with patch.dict(os.environ, {'CODEX_THREAD_ID': 'prior', 'CODEX_SESSION_ID': 'different'}):
            with self.assertRaisesRegex(JCMError, 'AMBIGUOUS'):
                existing(self.store, follow=False)

    def test_existing_rejects_foreign_project_session_and_forgotten(self):
        path = self.history()
        original = path.read_text()
        for changed, code in [(original.replace(str(self.root), str(self.base)), 'PROJECT_MISMATCH'),
                              (original.replace('"id":"prior"', '"id":"foreign"'), 'SESSION_MISMATCH')]:
            path.write_text(changed)
            with self.assertRaisesRegex(JCMError, code):
                existing(self.store, 'prior', path, follow=False)
        self.assertEqual(self.store.events(), [])
        self.assertFalse((self.root / '.codex/hooks.json').exists())
        path.write_text(original)
        self.store.forget_session('prior')
        with self.assertRaisesRegex(JCMError, 'FORGOTTEN'):
            existing(self.store, 'prior', path, follow=False)

    def test_follower_captures_partial_tail_without_hooks_and_stops_on_disable(self):
        path = self.history()
        result = existing(self.store, 'prior', path, install=False)
        self.assertTrue(result['follower']['running'])
        again = existing(self.store, 'prior', path, install=False)
        self.assertEqual(result['follower']['pid'], again['follower']['pid'])
        line = self.user_line('paused must remain true', 'tail')
        with path.open('ab') as stream:
            stream.write(line[:-3])
        time.sleep(.6)
        self.assertEqual(len(self.store.events()), 1)
        with path.open('ab') as stream:
            stream.write(line[-3:])
        for _ in range(40):
            if len(self.store.events()) == 2:
                break
            time.sleep(.05)
        self.assertEqual(len(self.store.events()), 2)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0], 0)
        self.store.change_policy(enabled=False)
        for _ in range(30):
            value = follower_status(self.store, result['source']['key'])
            if not value['running']:
                break
            time.sleep(.05)
        self.assertFalse(value['running'])
        self.assertEqual(value['error'], 'PROJECT_DISABLED')

    def test_stale_reader_cursor_cannot_rewind_or_duplicate(self):
        path = self.history()
        result = existing(self.store, 'prior', path, install=False, follow=False)
        old = dict(result['source'])
        with path.open('ab') as stream:
            stream.write(self.user_line('tail', 't2'))
        recover_source(self.store, old)
        recover_source(self.store, old)
        self.assertEqual(len(self.store.events()), 2)
        self.assertTrue(all(e['revision'] == 1 for e in self.store.events()))

    def test_new_preparation_has_no_invented_request_or_provider_call(self):
        hook(self.store, {'hook_event_name': 'SessionStart', 'session_id': 'fresh', 'cwd': str(self.root)})
        row = json.loads(self.store.db.execute("SELECT value FROM meta WHERE key='bootstrap_new:fresh'").fetchone()[0])
        self.assertEqual(row['stage'], 'awaiting_request')
        self.assertEqual(row['history'], 'empty')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM requests').fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0], 0)

    def test_new_recovers_registered_tail_and_serves_actual_pack(self):
        path = self.history()
        existing(self.store, 'prior', path, install=False, follow=False)
        with path.open('ab') as stream:
            stream.write(self.user_line('latest correction paused=true', 't2'))
        token = self.request()
        result = new(self.store, token, self.provider())
        self.assertEqual(result['stage'], 'read_served')
        self.assertIn('latest correction paused=true', str(result['pack']))
        self.assertEqual(result['pack']['quality'], 'normal')
        self.assertEqual(result['recovery_success'], 'not_attested')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0], 1)
        self.assertTrue(all(r['reconciliation'] == 'not_checked' for r in result['pack']['selected_records']))
        (self.root / 'changed.py').write_text('changed')
        again = new(self.store, token, self.provider())
        self.assertNotEqual(result['pack']['snapshot']['fingerprint'], again['pack']['snapshot']['fingerprint'])

    def test_new_blocked_and_revoked_token_do_not_claim_read(self):
        self.capture('old', 'x', 'requirement')
        token = self.request()
        self.store.config['pack_byte_ceiling'] = 100
        result = new(self.store, token, self.provider())
        self.assertEqual(result['stage'], 'blocked')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0], 0)
        self.store.change_policy()
        with self.assertRaisesRegex(JCMError, 'EPOCH'):
            new(self.store, token, self.provider())

    def test_bootstrap_hook_and_internal_output_boundary(self):
        result = hook(self.store, {'hook_event_name': 'UserPromptSubmit', 'session_id': 'fresh',
                                  'turn_id': 't', 'cwd': str(self.root), 'prompt': 'continue'})
        self.assertIn('bootstrap new --request-token', result['hookSpecificOutput']['additionalContext'])
        import shlex
        command = shlex.join(self.cfg['cli_argv'] + ['bootstrap', 'new', '--request-token', 'a' * 32])
        self.assertTrue(internal_command(self.cfg, command))
        self.assertFalse(internal_command(self.cfg, command + ' && touch marker'))

    def test_cli_existing_to_new_missing_credential_is_local_degraded(self):
        env = {**os.environ, 'TYPESAFE_API_KEY': ''}
        path = self.history()
        command = [sys.executable, '-m', 'jcm', '--home', str(self.home), '--repo', str(self.root)]
        result = subprocess.run(command + ['bootstrap', 'existing', '--session-id', 'prior',
                                 '--transcript', str(path), '--no-follow'], capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        output = subprocess.run(command + ['bootstrap', 'new', '--request-token', report['request_token']],
                                capture_output=True, text=True, env=env)
        self.assertEqual(output.returncode, 0, output.stderr)
        pack = json.loads(output.stdout)
        self.assertEqual(pack['pack']['quality'], 'degraded')
        self.assertEqual(pack['pack']['semantic_error'], 'PROVIDER_CREDENTIAL_UNAVAILABLE')
        self.assertIn('protocol_version=1', str(pack))
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0], 0)
