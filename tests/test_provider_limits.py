import io
import json
import shlex
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import test_continuity as fixtures
from jcm import config
from jcm.coordinator import dispatch
from jcm.provider import JevProvider, noul, retry_delay
from jcm.util import JCMError, encode


def context_error():
    return HTTPError('https://api.typesafe.ai', 400, 'Bad Request',
                     {'x-typesafe-request-id': 'context-test'},
                     io.BytesIO(b'{"error":{"code":"context_length_exceeded","message":"Maximum context length exceeded"}}'))


class ProviderLimitsTests(unittest.TestCase):
    setUp = fixtures.ContinuityTests.setUp
    tearDown = fixtures.ContinuityTests.tearDown
    capture = fixtures.ContinuityTests.capture
    request = fixtures.ContinuityTests.request
    provider = fixtures.ContinuityTests.provider

    def test_legacy_limits_do_not_block_large_or_frequent_requests(self):
        self.cfg.update(max_daily_calls=1, max_request_bytes=1)
        config.atomic_write(self.home / 'profiles' / (self.cfg['repo_id'] + '.json'), encode(self.cfg))
        provider = self.provider()
        for i in range(25):
            provider.evaluate({'index': i, 'text': 'source ' * 14000}, {'q': noul('Relevant?')})
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0], 25)
        self.assertGreater(self.store.db.execute('SELECT MIN(bytes) FROM calls').fetchone()[0], 80000)

    def test_retry_after_and_diagnostics_preserve_evidence_without_secret(self):
        calls, delays = [], []
        def transport(body, key):
            calls.append(body)
            if len(calls) == 1:
                raise HTTPError('https://api.typesafe.ai', 429, 'rate',
                    {'Retry-After': '7.5', 'x-typesafe-request-id': 'request-123', 'Set-Cookie': 'private'},
                    io.BytesIO(b'{"error":{"code":"rate_limit_exceeded","message":"secret=private-value"}}'))
            return fixtures.fake_http(body, key)
        JevProvider(self.store, transport=transport, sleeper=delays.append).evaluate({}, {'q': noul('A?')})
        self.assertAlmostEqual(sum(delays), 7.5)
        diagnostic = dict(self.store.db.execute('SELECT * FROM provider_errors').fetchone())
        self.assertEqual(diagnostic['request_id'], 'request-123')
        self.assertEqual(diagnostic['status'], 429)
        self.assertEqual(json.loads(diagnostic['detail'])['retry_headers']['retry-after'], '7.5')
        self.assertNotIn('private-value', diagnostic['detail'])
        self.assertNotIn('Set-Cookie', diagnostic['detail'])

    def test_server_context_rejection_splits_without_source_loss_and_caches_plan(self):
        text = '한글 source data\n' * 900
        source = self.capture('old', 'one', text)
        token = self.request(); self.store.db.execute("UPDATE jobs SET state='succeeded'")
        calls, accepted = [], []
        def transport(body, key):
            calls.append(body)
            if len(body) > 9000:
                raise context_error()
            accepted.append(json.loads(body))
            return fixtures.fake_http(body, key)
        provider = self.provider(transport)
        route = dispatch(self.store, token, provider)
        self.assertEqual(route['quality'], 'normal')
        pieces = [c for p in accepted for c in p['state'].get('candidates', []) if c['event_id'] == source]
        self.assertEqual(''.join(c['text'] for c in pieces), text)
        self.assertGreater(len(pieces), 1)
        count = len(calls)
        dispatch(self.store, token, provider)
        self.assertEqual(len(calls), count)

    def test_generic_400_is_not_misclassified_as_context_limit(self):
        def transport(body, key):
            raise HTTPError('https://api.typesafe.ai', 400, 'Bad Request', {},
                            io.BytesIO(b'{"error":{"message":"Invalid model"}}'))
        with self.assertRaisesRegex(JCMError, '^PROVIDER_HTTP_400$'):
            self.provider(transport).evaluate({}, {'q': noul('A?')})

    def test_observed_jev_max_tokens_error_is_classified(self):
        def transport(body, key):
            raise HTTPError('https://api.typesafe.ai', 400, 'Bad Request', {},
                            io.BytesIO(b'{"detail":{"error_type":"max_tokens_exceeded"}}'))
        with self.assertRaisesRegex(JCMError, '^PROVIDER_CONTEXT_LENGTH_EXCEEDED$'):
            self.provider(transport).evaluate({}, {'q': noul('A?')})

    def test_candidates_beyond_legacy_ceiling_are_assessed(self):
        self.store.config['candidate_ceiling'] = 1
        ids = {self.capture('old', str(i), f'evidence {i}', role='tool') for i in range(70)}
        token = self.request(); self.store.db.execute("UPDATE jobs SET state='succeeded'")
        observed = set()
        def transport(body, key):
            observed.update(c['event_id'] for c in json.loads(body)['state'].get('candidates', []))
            return fixtures.fake_http(body, key)
        dispatch(self.store, token, self.provider(transport))
        self.assertEqual(observed, ids)

    def test_estimate_does_not_refuse_an_accepted_long_query(self):
        self.capture('old', 'one', 'important source')
        query = 'long query ' * 10000
        token = self.request(query); self.store.db.execute("UPDATE jobs SET state='succeeded'")
        observed = []
        def transport(body, key):
            observed.append(json.loads(body)['state']['request'])
            return fixtures.fake_http(body, key)
        route = dispatch(self.store, token, self.provider(transport))
        self.assertEqual(route['quality'], 'normal')
        self.assertEqual(observed, [query])

    def test_both_documented_context_dimensions_influence_packing(self):
        from jcm.batching import context_fits
        self.assertTrue(context_fits('a' * 60000, {'a': 'q' * 3000}))
        self.assertFalse(context_fits('a' * 99000, {'a': 'q'}))
        self.assertFalse(context_fits('a' * 60000, {str(i): 'q' * 15000 for i in range(10)}))

    def test_server_context_rejection_splits_worker_source(self):
        from jcm.worker import drain
        text = 'original evidence\n' * 1000
        event = self.capture('old', 'one', text)
        accepted = []
        def transport(body, key):
            if len(body) > 6000:
                raise context_error()
            accepted.append(json.loads(body)['state']['source']['text'])
            return fixtures.fake_http(body, key)
        result = drain(self.store, self.provider(transport), limit=1)
        self.assertEqual(result['processed'], 1)
        self.assertEqual(''.join(accepted), text)
        self.assertEqual(self.store.db.execute('SELECT state FROM jobs WHERE event_id=?', (event,)).fetchone()[0], 'succeeded')

    def test_disable_during_retry_after_stops_before_another_call(self):
        calls = []
        def transport(body, key):
            calls.append(body)
            raise HTTPError('https://api.typesafe.ai', 429, 'rate', {'Retry-After': '120'}, None)
        def sleep(_):
            self.store.change_policy(enabled=False)
        with self.assertRaisesRegex(JCMError, 'PROJECT_DISABLED'):
            JevProvider(self.store, transport=transport, sleeper=sleep).evaluate({}, {'q': noul('A?')})
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM decisions WHERE status='pending'").fetchone()[0], 0)

    def test_retry_delay_supports_milliseconds_dates_and_invalid_headers(self):
        self.assertEqual(retry_delay({'retry-after-ms': '2500', 'Retry-After': '20'}), 2.5)
        self.assertEqual(retry_delay({'Retry-After': 'Thu, 01 Jan 1970 00:00:00 GMT'}), 0)
        self.assertIsNone(retry_delay({'Retry-After': 'NaN'}))
        self.assertIsNone(retry_delay({'Retry-After': 'invalid'}))

    def test_forget_removes_provider_diagnostics(self):
        self.capture('old', 'one', 'historical private source')
        def transport(body, key):
            raise context_error()
        with self.assertRaises(JCMError):
            self.provider(transport).evaluate({}, {'q': noul('A?')})
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM provider_errors').fetchone()[0], 1)
        self.store.forget_session('old')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM provider_errors').fetchone()[0], 0)

    def test_bootstrap_options_are_internal_but_unrelated_commands_are_not(self):
        from jcm.adapter import internal_command
        args = self.cfg['cli_argv'] + ['bootstrap', 'existing', '--session-id', 'session-123', '--no-follow']
        self.assertTrue(internal_command(self.cfg, shlex.join(args)))
        self.assertFalse(internal_command(self.cfg, shlex.join(args) + ' && cat source.py'))
        prefix = self.cfg['cli_argv'][:self.cfg['cli_argv'].index('--home')]
        abbreviated = prefix + ['--repo', self.cfg['root'], 'bootstrap', 'existing', '--session-id', 'session-123']
        with patch('jcm.adapter.default_home', return_value=self.home):
            self.assertTrue(internal_command(self.cfg, shlex.join(abbreviated)))
        self.assertFalse(internal_command(self.cfg, shlex.join(abbreviated)))
        self.assertFalse(internal_command(self.cfg, 'cat /tmp/recovery-output.json'))

    def test_v1_store_upgrade_preserves_records_and_rejects_unknown_schema(self):
        from jcm.store import Store
        event = self.capture('old', 'one', 'retained during schema migration')
        self.store.db.execute('DROP TABLE provider_errors')
        self.store.db.execute('PRAGMA user_version=1')
        self.store.close()
        self.store = Store(self.cfg)
        self.assertEqual(self.store.db.execute('PRAGMA user_version').fetchone()[0], 4)
        self.assertEqual(self.store.material(self.store.event(event))['text'], 'retained during schema migration')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM provider_errors').fetchone()[0], 0)
        self.store.db.execute('PRAGMA user_version=999')
        with self.assertRaisesRegex(JCMError, 'UNSUPPORTED_DATABASE_VERSION'):
            Store(self.cfg)
