import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import test_continuity as fixtures
from jcm import config
from jcm.bootstrap import new
from jcm.coordinator import dispatch, read_pack
from jcm.util import JCMError, encode


class BatchedRecoveryTests(unittest.TestCase):
    setUp = fixtures.ContinuityTests.setUp
    tearDown = fixtures.ContinuityTests.tearDown
    capture = fixtures.ContinuityTests.capture
    request = fixtures.ContinuityTests.request
    provider = fixtures.ContinuityTests.provider

    def budget(self, request=9000, page=7000):
        self.cfg['max_request_bytes'] = request
        self.cfg['pack_byte_ceiling'] = page
        config.atomic_write(self.home / 'profiles' / (self.cfg['repo_id'] + '.json'), encode(self.cfg))

    def skip_classification(self):
        self.store.db.execute("UPDATE jobs SET state='succeeded'")

    def test_large_records_batch_by_encoded_bytes_and_cache_successes(self):
        self.budget()
        text = ('한글 source \\" quoted\n' * 1300) + 'important tail'
        source = self.capture('old', '1', text)
        token = self.request()
        self.skip_classification()
        calls = []
        def transport(body, key):
            self.assertLessEqual(len(body), 9000)
            calls.append(json.loads(body))
            return fixtures.fake_http(body, key)
        provider = self.provider(transport)
        route = dispatch(self.store, token, provider)
        self.assertEqual(route['quality'], 'normal')
        chunks = [c for p in calls for c in p['state'].get('candidates', []) if c['event_id'] == source]
        self.assertGreater(len(chunks), 1)
        self.assertEqual(''.join(c['text'] for c in chunks), text)
        self.assertEqual(chunks[0]['span']['start'], 0)
        self.assertEqual(chunks[-1]['span']['end'], len(text))
        count = len(calls)
        again = dispatch(self.store, token, provider)
        self.assertEqual(len(calls), count)
        self.assertTrue(all(d['cached'] for d in again['decision_refs']))

    def test_cross_batch_correction_uses_both_sources(self):
        self.budget(request=12000)
        first = self.capture('old', '1', 'Keep the delay at 10 seconds.\n' * 80)
        second = self.capture('old', '2', 'Correction: change the delay to 20 seconds.\n' * 80)
        token = self.request(); self.skip_classification()
        calls = []
        def transport(body, key):
            calls.append(json.loads(body))
            return fixtures.fake_http(body, key)
        route = dispatch(self.store, token, self.provider(transport))
        pack = self.store.blob(self.store.db.execute('SELECT blob FROM packs WHERE id=?', (route['pack_id'],)).fetchone()[0])
        pairs = [p for call in calls for p in call['state'].get('pairs', [])]
        self.assertTrue(any(p['left']['event_id'] == first and p['right']['event_id'] == second for p in pairs))
        relation = next(r for r in pack['relationship_candidates'] if r['from'] == first and r['to'] == second)
        self.assertEqual(relation['proposed_relationship'], 'corrects')
        self.assertFalse(relation['supersedes_applied'])

    def test_partial_failure_keeps_unjudged_material_and_retries_only_failed_batch(self):
        self.budget()
        for i in range(3):
            self.capture('old', str(i), str(i) * 5000, role='tool')
        token = self.request(); self.skip_classification()
        calls = []; failed = []
        def transport(body, key):
            calls.append(body)
            if len(calls) == 2:
                failed.append(body)
                raise HTTPError('https://api.typesafe.ai', 503, 'temporary', {}, None)
            return fixtures.fake_http(body, key)
        self.cfg['max_attempts'] = 1
        config.atomic_write(self.home / 'profiles' / (self.cfg['repo_id'] + '.json'), encode(self.cfg))
        provider = self.provider(transport)
        route = dispatch(self.store, token, provider)
        self.assertEqual(route['quality'], 'degraded')
        pack = self.store.blob(self.store.db.execute('SELECT blob FROM packs WHERE id=?', (route['pack_id'],)).fetchone()[0])
        self.assertTrue(any(r['pending_retrieval_judgment'] for r in pack['selected_records']))
        prior = len(calls)
        again = dispatch(self.store, token, provider)
        self.assertEqual(again['quality'], 'normal')
        self.assertEqual(calls[prior:], failed)

    def test_mixed_egress_never_sends_previously_denied_records(self):
        self.budget()
        self.store.change_policy(allow_egress=False)
        denied = self.capture('old', 'denied', 'PRIVATE-DENIED-TEXT')
        self.store.change_policy(allow_egress=True)
        self.capture('old', 'allowed', 'eligible text')
        token = self.request(); self.skip_classification()
        calls = []
        def transport(body, key):
            self.assertNotIn(b'PRIVATE-DENIED-TEXT', body)
            calls.append(body)
            return fixtures.fake_http(body, key)
        route = dispatch(self.store, token, self.provider(transport))
        self.assertTrue(calls)
        self.assertEqual(route['quality'], 'degraded')
        pack = self.store.blob(self.store.db.execute('SELECT blob FROM packs WHERE id=?', (route['pack_id'],)).fetchone()[0])
        self.assertIn(denied, [r['event_id'] for r in pack['selected_records']])

    def test_pages_preserve_whole_pack_and_require_all_receipts(self):
        self.budget(page=5000)
        self.store.change_policy(allow_egress=False)
        self.capture('old', '1', '한글 중요 조건\n' * 4000)
        token = self.request()
        fake_snapshot = {'fingerprint': 'stable', 'files': {str(i): 'x' * 100 for i in range(200)}}
        with patch('jcm.coordinator.snapshot', return_value=fake_snapshot):
            route = dispatch(self.store, token, self.provider())
        self.assertNotEqual(route['dispatch'], 'blocked')
        original = self.store.blob(self.store.db.execute('SELECT blob FROM packs WHERE id=?', (route['pack_id'],)).fetchone()[0])
        first = read_pack(self.store, route['pack_id'])
        self.assertEqual(first['delivery'], 'page_served')
        count = first['pagination']['page_count']
        self.assertGreater(count, 2)
        last = read_pack(self.store, route['pack_id'], page=count)
        self.assertEqual(last['delivery'], 'page_served')
        self.assertFalse(last['pagination']['all_pages_served'])
        pages = {1: first, count: last}
        for page in range(2, count):
            pages[page] = read_pack(self.store, route['pack_id'], page=page)
        self.assertEqual(pages[count-1]['delivery'], 'read_served')
        self.assertTrue(pages[count-1]['pagination']['all_pages_served'])
        for result in pages.values():
            self.assertLessEqual(len(encode(result)) + 1, 5000)
        reconstructed = {}
        for page in range(1, count+1):
            for entry in pages[page]['entries']:
                target = reconstructed
                for key in entry['path'][:-1]:
                    target = target[key] if isinstance(target, list) else target.setdefault(key, {})
                key = entry['path'][-1]
                if isinstance(target, list) and key == len(target):
                    target.append('' if 'text' in entry else None)
                if 'text' in entry:
                    prior = target[key] if isinstance(target, list) else target.get(key, '')
                    self.assertEqual(len(prior), entry['start'])
                    target[key] = prior + entry['text']
                else:
                    target[key] = json.loads(json.dumps(entry['value']))
        self.assertEqual(reconstructed, {k:v for k,v in original.items() if k != 'page_manifest'})
        before = self.store.db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0]
        read_pack(self.store, route['pack_id'], page=1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0], before)
        self.store.change_policy(allow_egress=True)
        with self.assertRaisesRegex(JCMError, 'INVALIDATED|EPOCH'):
            read_pack(self.store, route['pack_id'], page=2)

    def test_bootstrap_pagination_is_reading_until_complete(self):
        self.budget()
        self.capture('old', '1', 'retain all of this\n' * 3000)
        result = new(self.store, self.request(), self.provider())
        self.assertEqual(result['stage'], 'reading')
        self.assertIn('--page', result['next_read_command'])
        self.assertEqual(result['recovery_success'], 'not_attested')

    def test_daily_budget_stops_batches_and_preserves_unassessed_sources(self):
        self.budget()
        for i in range(5):
            self.capture('old', str(i), str(i) * 5000, role='tool')
        token = self.request(); self.skip_classification()
        self.cfg['max_daily_calls'] = 2
        config.atomic_write(self.home / 'profiles' / (self.cfg['repo_id'] + '.json'), encode(self.cfg))
        calls = []
        def transport(body, key):
            calls.append(body)
            return fixtures.fake_http(body, key)
        route = dispatch(self.store, token, self.provider(transport))
        self.assertEqual(len(calls), 2)
        self.assertIn('PROVIDER_DAILY_CALL_BUDGET_EXCEEDED', route['coverage']['gaps'])
        pack = self.store.blob(self.store.db.execute('SELECT blob FROM packs WHERE id=?', (route['pack_id'],)).fetchone()[0])
        self.assertTrue(any(b['status'] == 'not_attempted' for b in pack['retrieval_batches']))
        self.assertTrue(any(r['pending_retrieval_judgment'] for r in pack['selected_records']))
        self.assertEqual(route['quality'], 'degraded')

    def test_oversized_request_never_truncated_or_sent(self):
        self.budget()
        self.capture('old', '1', 'keep requirement')
        token = self.request('long request ' * 3000); self.skip_classification()
        def forbidden(*args):
            self.fail('Oversized query must not be truncated to force a provider call')
        route = dispatch(self.store, token, self.provider(forbidden))
        self.assertIn('PROVIDER_REQUEST_CONTEXT_TOO_LARGE', route['coverage']['gaps'])
        self.assertNotEqual(route['dispatch'], 'blocked')

    def test_optional_large_record_selects_original_relevant_span(self):
        self.budget()
        text = 'unrelated background\n' * 1500 + '\nIMPORTANT failure reproduction\n' + 'relevant details\n' * 100
        source = self.capture('old', '1', text, role='tool')
        token = self.request('find failure reproduction'); self.skip_classification()
        def transport(body, key):
            response = fixtures.fake_http(body, key)
            for i, candidate in enumerate(json.loads(body)['state'].get('candidates', [])):
                if 'IMPORTANT' in candidate['text']:
                    response['answers'][f'relevance_{i}'].update(score=3, probabilities={str(n):float(n == 3) for n in range(4)})
            return response
        route = dispatch(self.store, token, self.provider(transport))
        pack = self.store.blob(self.store.db.execute('SELECT blob FROM packs WHERE id=?', (route['pack_id'],)).fetchone()[0])
        record = next(r for r in pack['selected_records'] if r['event_id'] == source)
        self.assertEqual(record['representation'], 'spans')
        self.assertIn('IMPORTANT', record['text'])
        self.assertLess(len(record['text']), len(text))
        self.assertEqual(record['text'], '\n\n[... omitted source span ...]\n\n'.join(text[s['start']:s['end']] for s in record['spans']))

    def test_chunked_worker_records_composition_and_reuses_successes(self):
        from jcm.worker import drain
        self.budget()
        event = self.capture('old', '1', 'requirement\n' * 2000)
        calls = []
        def transport(body, key):
            self.assertLessEqual(len(body), 9000)
            calls.append(body)
            return fixtures.fake_http(body, key)
        result = drain(self.store, self.provider(transport), limit=1)
        self.assertEqual(result['processed'], 1)
        self.assertGreater(len(calls), 1)
        projection = self.store.db.execute('SELECT * FROM projections WHERE event_id=?', (event,)).fetchone()
        decision = self.store.db.execute('SELECT * FROM decisions WHERE id=?', (projection['decision'],)).fetchone()
        self.assertEqual(decision['status'], 'composed')
        self.assertEqual(len(self.store.blob(decision['request_blob'])['fragment_decisions']), len(calls))

    def test_cli_pages_exclude_internal_output_and_report_completion(self):
        import os
        import subprocess
        import sys
        from jcm.adapter import internal_command
        self.budget(page=5000)
        self.store.change_policy(allow_egress=False)
        self.capture('old', '1', 'original requirement\n' * 1000)
        token = self.request()
        result = new(self.store, token, self.provider())
        self.assertEqual(result['stage'], 'reading')
        command = result['next_read_command']
        seen = 1
        while command:
            self.assertTrue(internal_command(self.cfg, command))
            import shlex
            args = shlex.split(command)
            # The fixture process uses the same source package, no installed/global plugin mutation.
            proc = subprocess.run(args, capture_output=True, env=os.environ.copy())
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertLessEqual(len(proc.stdout), 5000)
            result = json.loads(proc.stdout)
            command = result['next_read_command']
            seen += 1
        self.assertEqual(result['delivery'], 'read_served')
        self.assertGreater(seen, 1)
        meta = json.loads(self.store.db.execute("SELECT value FROM meta WHERE key='bootstrap_new:fresh'").fetchone()[0])
        self.assertEqual(meta['stage'], 'read_served')
        count = self.store.db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0]
        with self.assertRaisesRegex(JCMError, 'INVALID_PACK_PAGE'):
            read_pack(self.store, result['pack_id'], page=seen+1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0], count)

    def test_oversized_relation_is_explicitly_unresolved_not_truncated(self):
        self.budget()
        self.capture('old', '1', 'first requirement\n' * 500)
        self.capture('old', '2', 'correction applies\n' * 500)
        token = self.request(); self.skip_classification()
        route = dispatch(self.store, token, self.provider())
        self.assertIn('RELATION_CONTEXT_EXCEEDS_PROVIDER_BUDGET', route['coverage']['gaps'])
        pack = self.store.blob(self.store.db.execute('SELECT blob FROM packs WHERE id=?', (route['pack_id'],)).fetchone()[0])
        self.assertEqual(len(pack['selected_records']), 2)
        self.assertEqual(pack['relationship_candidates'][0]['status'], 'unresolved')
        self.assertFalse(pack['relationship_candidates'][0]['supersedes_applied'])

    def test_corrupted_page_cannot_advance_receipts_or_claim_full_delivery(self):
        self.budget(page=5000)
        self.store.change_policy(allow_egress=False)
        self.capture('old', '1', 'historical content\n' * 1000)
        route = dispatch(self.store, self.request(), self.provider())
        first = read_pack(self.store, route['pack_id'])
        self.assertEqual(first['delivery'], 'page_served')
        pack = self.store.blob(self.store.db.execute('SELECT blob FROM packs WHERE id=?', (route['pack_id'],)).fetchone()[0])
        (self.store.blobs / pack['page_manifest']['pages'][1]).write_text('tampered')
        with self.assertRaisesRegex(JCMError, 'BLOB_HASH_MISMATCH'):
            read_pack(self.store, route['pack_id'], page=2)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0], 1)
        self.assertEqual(self.store.db.execute('SELECT delivery FROM packs').fetchone()[0], 'page_served')
        self.store.forget_session('old')
        with self.assertRaisesRegex(JCMError, 'MISSING_OR_INVALIDATED'):
            read_pack(self.store, route['pack_id'], page=1)

    def test_epoch_change_between_batches_prevents_later_transmission_and_pack(self):
        self.budget()
        self.capture('old', '1', 'large source\n' * 1500)
        token = self.request(); self.skip_classification()
        calls = []
        def transport(body, key):
            calls.append(body)
            self.store.change_policy(allow_egress=False)
            return fixtures.fake_http(body, key)
        with self.assertRaisesRegex(JCMError, 'EPOCH'):
            dispatch(self.store, token, self.provider(transport))
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM packs').fetchone()[0], 0)

    def test_forgetting_during_page_planning_cannot_leave_new_source_blobs(self):
        from jcm.delivery import paginate
        self.budget(page=5000)
        self.store.change_policy(allow_egress=False)
        self.capture('old', '1', 'FORGET-THIS-SOURCE\n' * 1000)
        token = self.request()
        def forget_after_planning(store, pack):
            plan = paginate(store, pack)
            store.forget_session('old')
            return plan
        with patch('jcm.coordinator.paginate', side_effect=forget_after_planning):
            with self.assertRaisesRegex(JCMError, 'EPOCH'):
                dispatch(self.store, token, self.provider())
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM packs').fetchone()[0], 0)
        self.assertNotIn('FORGET-THIS-SOURCE', ''.join(p.read_text() for p in self.store.blobs.iterdir()))
