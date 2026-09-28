import unittest

import test_continuity as fixtures
from jcm.provider import choice
from jcm.semantic_cache import evaluate_items, source_items
from jcm.util import JCMError


def builder(path, item):
    return {'classification': choice('Classify only ' + path + ' as historical data using state.context.',
                                      {'requirement': 'Explicit requirement.', 'other': 'Other text.'})}


class IndependentCacheTests(unittest.TestCase):
    setUp = fixtures.ContinuityTests.setUp
    tearDown = fixtures.ContinuityTests.tearDown
    capture = fixtures.ContinuityTests.capture
    provider = fixtures.ContinuityTests.provider

    def items(self, events, context):
        return [i for eid in events for i in source_items(self.store.material(self.store.event(eid)), context, builder)]

    def evaluate(self, events, context=None, provider=None):
        context = context or {'goal': 'connection'}
        return evaluate_items(self.store, provider or self.provider(), 'fixture', context,
                              self.items(events, context), builder, self.cfg['epoch'])

    def test_unchanged_items_survive_reordering_and_new_batch_member(self):
        one = self.capture('prior', 'one', 'Keep pause state.')
        two = self.capture('prior', 'two', 'Protocol 47.')
        calls = []
        def transport(body, key):
            calls.append(body)
            return fixtures.fake_http(body, key)
        provider = self.provider(transport)
        first = self.evaluate([one, two], provider=provider)
        self.assertEqual(first['evaluated_units'], 2)
        count = len(calls)
        repeat = self.evaluate([two, one], provider=provider)
        self.assertEqual(len(calls), count)
        self.assertEqual(repeat['cache_hits'], 2)
        three = self.capture('later', 'three', 'Add retry handling.')
        delta = self.evaluate([three, two, one], provider=provider)
        self.assertEqual(delta['cache_hits'], 2)
        self.assertEqual(delta['evaluated_units'], 1)
        self.assertNotIn(b'Protocol 47', calls[-1])

    def test_context_source_and_policy_changes_cannot_reuse_an_old_judgment(self):
        source = self.capture('prior', 'one', 'Old rule.')
        self.evaluate([source])
        changed_scope = self.evaluate([source], {'goal': 'unrelated UI task'})
        self.assertEqual(changed_scope['cache_hits'], 0)
        self.store.db.execute('UPDATE events SET revision=revision+1 WHERE id=?', (source,))
        self.assertEqual(self.evaluate([source])['cache_hits'], 0)
        self.store.change_policy(enabled=False)
        with self.assertRaisesRegex(JCMError, 'PROJECT_DISABLED'):
            self.evaluate([source])

    def test_forget_removes_cached_derivatives_and_call_metrics(self):
        source = self.capture('prior', 'one', 'A source to forget.')
        self.evaluate([source])
        self.assertGreater(self.store.db.execute('SELECT count(*) FROM semantic_items').fetchone()[0], 0)
        self.assertGreater(self.store.db.execute('SELECT count(*) FROM call_metrics').fetchone()[0], 0)
        self.store.forget_session('prior')
        for table in ('semantic_items', 'call_metrics', 'task_views', 'assertions', 'representations'):
            self.assertEqual(self.store.db.execute('SELECT count(*) FROM ' + table).fetchone()[0], 0)


class TaskReuseTests(IndependentCacheTests):
    request = fixtures.ContinuityTests.request

    def transport(self, body, key):
        import json
        import re
        payload = json.loads(body)
        response = fixtures.fake_http(body, key)
        for name, question in payload['questions'].items():
            match = re.match(r'i(\d+)_(.+)', name)
            if not match:
                continue
            item = payload['state']['items'][int(match[1])]
            field = match[2]
            text = item.get('text', ''.join(p['text'] for p in item.get('paragraphs', [])))
            answer = response['answers'][name]
            if question['type'] == 'noul':
                answer['noul'] = float((field == 'requirement' and item.get('role') == 'user') or
                    (field == 'correction' and 'Correction:' in text) or
                    (field == 'verification_claim' and 'test passed' in text) or (field == 'open_issue' and 'Unresolved:' in text) or field == 'omission' or
                    (field == 'affects_assertion' and 'delay' in item['older']['text'] and 'delay' in item['newer']['text']))
            elif question['type'] == 'score':
                answer.update(score=3, probabilities={str(i): float(i == 3) for i in range(4)})
            elif question['type'] == 'choice':
                if field == 'scope':
                    selected = 'changed' if 'color' in payload['state']['context']['request'] else 'same'
                elif field == 'effect':
                    selected = 'update' if 'Correction:' in payload['state']['context']['request'] else 'procedural'
                elif field == 'applicability':
                    selected = 'unrelated' if text.startswith('OTHER:') else 'direct'
                elif field == 'target_scope':
                    selected = 'whole'
                elif field == 'relation':
                    selected = 'corrects' if 'delay' in item['older']['text'] and 'delay' in item['newer']['text'] else 'unrelated'
                elif field.startswith('block_'):
                    from jcm.task_state import blocks
                    a,b = list(blocks(text))[int(field[6:])]
                    selected = 'detail' if text[a:b].startswith('LOG:') else 'keep'
                else:
                    continue
                answer.update(choice=selected, probabilities={v: float(v == selected) for v in question['criteria']})
        self.sent.append(payload)
        return response

    def setUp(self):
        super().setUp()
        self.sent = []

    def recover(self, session, text):
        from jcm.coordinator import dispatch
        eid = self.capture(session, 'request', text)
        token = self.store.request(session, eid)
        route = dispatch(self.store, token, self.provider(self.transport))
        row = self.store.db.execute('SELECT blob FROM packs WHERE id=?', (route['pack_id'],)).fetchone()
        return self.store.blob(row[0])

    def read_view(self, pack, view='brief'):
        from jcm.coordinator import read_pack
        result = None
        pages = []
        for page in range(1, len(pack['context_views'][view]['page_manifest']['pages']) + 1):
            result = read_pack(self.store, pack['pack_id'], page, view)
            pages.append(result)
        return result, pages

    def test_rephrase_reuses_source_judgments_and_delta_only_reassesses_new_sources(self):
        self.capture('history', 'one', 'Keep pause state. Delay 5 seconds.')
        self.capture('history', 'two', 'Never resume automatically, except on explicit user action.')
        first = self.recover('session-a', 'Continue pause recovery.')
        second = self.recover('session-b', 'Resume work on pause handling.')
        self.assertEqual(second['metrics']['task_route'], 'confirmed_scope_reuse')
        self.assertEqual(second['metrics']['source_units_evaluated'], 0)
        self.assertEqual(second['metrics']['source_units_reused'], 2)
        self.assertEqual(second['task_frame']['task_id'], first['task_frame']['task_id'])
        self.assertIn('except on explicit user action', str(second['context_views']['brief']))
        correction = self.capture('session-c', 'correction', 'Correction: change delay to 8 seconds.')
        third = self.recover('session-c', 'Continue pause recovery after correction.')
        # The prior session's request is newly admitted history, alongside the correction.
        self.assertEqual(third['metrics']['source_units_evaluated'], 2)
        self.assertEqual(third['metrics']['source_units_reused'], 2)
        self.assertTrue(any(a['event_id'] == correction for a in third['task_frame']['assertions']))
        self.assertGreaterEqual(third['metrics']['relation_units_evaluated'], 3)

    def test_scope_change_does_not_reuse_membership_but_reuses_classification(self):
        self.capture('history', 'one', 'Keep pause state.')
        first = self.recover('session-a', 'Continue pause recovery.')
        second = self.recover('session-b', 'Change color palette.')
        self.assertNotEqual(first['task_frame']['task_id'], second['task_frame']['task_id'])
        self.assertEqual(second['metrics']['task_route'], 'cold_scope_expansion')
        self.assertGreater(second['metrics']['source_units_evaluated'], 0)
        self.assertGreater(second['metrics']['classification_units_reused'], 0)

    def test_scoped_review_requires_original_reads_and_does_not_establish_verification(self):
        from jcm.task_state import confirm
        from jcm.source_read import inspect_source
        old = self.capture('history', 'one', 'Use delay 5 seconds.')
        new = self.capture('history', 'two', 'Correction: use delay 8 seconds.')
        pack = self.recover('session-a', 'Continue pause recovery.')
        relation = pack['task_frame']['relations'][0]
        assertions = {a['event_id']: a for a in pack['task_frame']['assertions']}
        self.assertEqual(assertions[old]['state'], 'disputed')
        with self.assertRaisesRegex(JCMError, 'EXACT_SOURCE_READ_REQUIRED'):
            confirm(self.store, relation['id'], 'confirmed')
        inspect_source(self.store, old)
        inspect_source(self.store, new)
        confirm(self.store, relation['id'], 'confirmed')
        rebuilt = self.recover('session-b', 'Resume pause recovery.')
        older = next(a for a in rebuilt['task_frame']['assertions'] if a['event_id'] == old)
        self.assertEqual(older['state'], 'superseded')
        self.assertTrue(all(a['implementation_status'] == 'not_established' for a in rebuilt['task_frame']['assertions']))

    def test_brief_retains_late_exception_and_completes_without_audit_reads(self):
        from jcm.metrics import delivery_metrics
        self.capture('history', 'one', 'Keep pause state.')
        tool = self.capture('history', 'two', 'Pause handler changed.\n\nLOG: ' + ('uninteresting trace ' * 300) +
                            '\n\nException: never auto-resume when the user explicitly paused.', role='tool')
        pack = self.recover('session-a', 'Continue pause recovery.')
        source = next(s for s in pack['context_views']['brief']['selected_records'] if s['event_id'] == tool)
        self.assertIn('never auto-resume', source['text'])
        self.assertNotIn('uninteresting trace', source['text'])
        result, pages = self.read_view(pack)
        self.assertTrue(result['required_context_complete'])
        metrics = delivery_metrics(self.store, pack['pack_id'])
        self.assertGreater(metrics['required_served_bytes'], 0)
        self.assertEqual(metrics['optional_served_bytes'], 0)

    def test_source_revision_and_policy_invalidate_cached_state_and_old_read(self):
        from jcm.coordinator import read_pack
        source = self.capture('history', 'one', 'Keep pause state.')
        pack = self.recover('session-a', 'Continue pause recovery.')
        self.store.db.execute('UPDATE events SET revision=revision+1 WHERE id=?', (source,))
        with self.assertRaisesRegex(JCMError, 'SEMANTIC_DEPENDENCY_CHANGED'):
            read_pack(self.store, pack['pack_id'])
        changed = self.recover('session-b', 'Continue pause handling.')
        self.assertEqual(changed['metrics']['source_units_evaluated'], 1)
        self.store.change_policy(enabled=False)
        with self.assertRaisesRegex(JCMError, 'PROJECT_DISABLED|PACK_MISSING_OR_INVALIDATED'):
            read_pack(self.store, changed['pack_id'])

    def test_correction_in_current_request_is_applied_to_its_own_recovery(self):
        source = self.capture('history', 'one', 'Use delay 5 seconds.')
        self.recover('session-a', 'Continue pause recovery.')
        corrected = self.recover('session-b', 'Correction: change pause delay to 8 seconds.')
        self.assertEqual(corrected['metrics']['source_units_evaluated'], 1)
        old = next(a for a in corrected['task_frame']['assertions'] if a['event_id'] == source)
        self.assertEqual(old['state'], 'disputed')
        self.assertTrue(any('8 seconds' in a['text'] for a in corrected['task_frame']['assertions']))

    def test_partial_correction_cannot_confirm_whole_compound_requirement(self):
        from jcm.task_state import confirm
        from jcm.source_read import inspect_source
        old = self.capture('history', 'one', 'Use delay 5 seconds and preserve protocol 1')
        new = self.capture('history', 'two', 'Correction: use delay 8 seconds')
        original = self.transport
        def partial(body, key):
            response = original(body, key)
            for name, answer in response['answers'].items():
                if name.endswith('_target_scope'):
                    answer.update(choice='partial', probabilities={k: float(k == 'partial') for k in answer['probabilities']})
            return response
        self.transport = partial
        pack = self.recover('session-a', 'Continue pause recovery.')
        for source in (old, new):
            inspect_source(self.store, source)
        with self.assertRaisesRegex(JCMError, 'RELATION_REQUIRES_SCOPE_REVIEW'):
            confirm(self.store, pack['task_frame']['relations'][0]['id'], 'confirmed')
        self.assertIn('preserve protocol 1', str(pack['context_views']['brief']))

    def test_model_and_rubric_changes_invalidate_state_and_old_delivery(self):
        from unittest.mock import patch
        from jcm.coordinator import read_pack
        from jcm.util import encode
        self.capture('history', 'one', 'Keep pause state.')
        first = self.recover('session-a', 'Continue pause recovery.')
        with patch('jcm.task_state.RUBRIC_VERSION', 'changed-rubric'):
            with self.assertRaisesRegex(JCMError, 'PACK_SEMANTIC_VERSION_CHANGED'):
                read_pack(self.store, first['pack_id'])
        self.cfg['model'] = 'fixture-future-model'
        fixtures.config.atomic_write(self.home / 'profiles' / (self.cfg['repo_id'] + '.json'), encode(self.cfg))
        with self.assertRaisesRegex(JCMError, 'PACK_SEMANTIC_VERSION_CHANGED'):
            read_pack(self.store, first['pack_id'])
        second = self.recover('session-b', 'Continue pause recovery.')
        self.assertGreater(second['metrics']['source_units_evaluated'], 0)
        self.assertEqual(second['metrics']['source_units_reused'], 0)

    def test_concurrent_source_change_cannot_publish_normal_state_or_pack(self):
        source = self.capture('history', 'one', 'Keep pause state.')
        original = self.transport
        def mutate(body, key):
            import json
            payload = json.loads(body)
            response = original(body, key)
            if 'i0_relevance' in payload['questions']:
                self.store.db.execute('UPDATE events SET revision=revision+1 WHERE id=?', (source,))
            return response
        self.transport = mutate
        with self.assertRaisesRegex(JCMError, 'SEMANTIC_DEPENDENCY_CHANGED'):
            self.recover('session-a', 'Continue pause recovery.')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM task_views').fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM packs').fetchone()[0], 0)

    def test_no_lexical_hit_still_expands_to_unassessed_source(self):
        source = self.capture('history', 'one', '사용자가 멈춘 상태를 그대로 보존하고 임의로 재시작하지 않는다.')
        pack = self.recover('session-a', 'Continue pause recovery.')
        self.assertEqual(pack['metrics']['search']['lexical_hits'], 0)
        self.assertIn(source, {r['event_id'] for r in pack['selected_records']})

    def test_identical_repetition_within_one_source_keeps_occurrence_offsets(self):
        self.capture('history', 'one', 'Keep pause state. ' * 80)
        pack = self.recover('session-a', 'Continue pause recovery.')
        self.assertEqual(len(pack['task_frame']['assertions']), 1)
        assertion = pack['task_frame']['assertions'][0]
        self.assertEqual(len(assertion['occurrences']), 80)
        self.assertEqual(assertion['occurrences'][0]['start'], 0)
        self.assertEqual(assertion['occurrences'][-1]['end'], len('Keep pause state. ' * 80))


    def test_optional_view_does_not_complete_required_context(self):
        from jcm.metrics import delivery_metrics
        self.capture('history', 'one', 'Keep pause state.')
        pack = self.recover('session-a', 'Continue pause recovery.')
        result, _ = self.read_view(pack, 'detail')
        self.assertFalse(result['required_context_complete'])
        self.assertEqual(delivery_metrics(self.store, pack['pack_id'])['required_served_bytes'], 0)
        result, _ = self.read_view(pack)
        self.assertTrue(result['required_context_complete'])
        self.assertGreater(delivery_metrics(self.store, pack['pack_id'])['dispatch_timing']['pack_preparation_seconds'], 0)

    def test_source_receipt_requires_every_page_at_same_revision(self):
        from jcm.source_read import inspect_source
        source = self.capture('history', 'large', 'Keep pause state. ' * 10000)
        first = inspect_source(self.store, source)
        self.assertGreater(first['pagination']['page_count'], 1)
        self.assertFalse(first['pagination']['all_pages_served'])
        self.assertIsNone(self.store.db.execute('SELECT value FROM meta WHERE key=?', ('source_read:' + source,)).fetchone())
        for page in range(2, first['pagination']['page_count'] + 1):
            result = inspect_source(self.store, source, page)
        self.assertTrue(result['pagination']['all_pages_served'])
        self.store.db.execute('UPDATE events SET revision=revision+1 WHERE id=?', (source,))
        self.assertFalse(inspect_source(self.store, source)['pagination']['all_pages_served'])

    def test_new_request_requires_relation_review_again(self):
        from jcm.task_state import confirm
        from jcm.source_read import inspect_source
        old = self.capture('history', 'one', 'Use delay 5 seconds.')
        new = self.capture('history', 'two', 'Correction: use delay 8 seconds.')
        pack = self.recover('session-a', 'Continue pause recovery.')
        inspect_source(self.store, old); inspect_source(self.store, new)
        self.capture('history', 'third', 'Correction: use delay 12 seconds.')
        with self.assertRaisesRegex(JCMError, 'TASK_STATE_NEW_REQUEST_REVIEW_REQUIRED'):
            confirm(self.store, pack['task_frame']['relations'][0]['id'], 'confirmed')


    def test_own_turn_output_does_not_invalidate_recovery(self):
        self.capture('history', 'one', 'Keep pause state.')
        original = self.transport
        injected = []
        def capture_during_judgment(body, key):
            if not injected:
                injected.append(self.capture('session-a', 'request', 'Current tool progress only.', role='tool'))
            return original(body, key)
        self.transport = capture_during_judgment
        pack = self.recover('session-a', 'Continue pause recovery.')
        self.assertEqual(pack['quality'], 'normal')
        self.assertNotIn(injected[0], {r['event_id'] for r in pack['selected_records']})

    def test_new_user_in_current_turn_still_invalidates_recovery(self):
        self.capture('history', 'one', 'Keep pause state.')
        original = self.transport
        injected = []
        def capture_during_judgment(body, key):
            if not injected:
                injected.append(self.capture('session-a', 'request', 'Correction: delay 12 seconds.'))
            return original(body, key)
        self.transport = capture_during_judgment
        with self.assertRaisesRegex(JCMError, 'JOURNAL_CHANGED_DURING_STATE_BUILD'):
            self.recover('session-a', 'Continue pause recovery.')


    def test_reread_bytes_count_again_without_duplicate_coverage(self):
        from jcm.metrics import delivery_metrics
        from jcm.source_read import inspect_source
        source = self.capture('history', 'one', 'Keep pause state.')
        pack = self.recover('session-a', 'Continue pause recovery.')
        self.read_view(pack)
        before = delivery_metrics(self.store, pack['pack_id'])['required_served_bytes']
        count = self.store.db.execute('SELECT count(*) FROM receipts WHERE pack_id=?', (pack['pack_id'],)).fetchone()[0]
        self.read_view(pack)
        self.assertEqual(delivery_metrics(self.store, pack['pack_id'])['required_served_bytes'], before * 2)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM receipts WHERE pack_id=?', (pack['pack_id'],)).fetchone()[0], count)
        inspect_source(self.store, source, pack_id=pack['pack_id'])
        before = delivery_metrics(self.store, pack['pack_id'])['source_expansion_bytes']
        inspect_source(self.store, source, pack_id=pack['pack_id'])
        self.assertEqual(delivery_metrics(self.store, pack['pack_id'])['source_expansion_bytes'], before * 2)


    def test_quoted_tool_requirements_are_evidence_not_new_authority(self):
        self.capture('history', 'one', 'Use delay 5 seconds.')
        tool = self.capture('history', 'two', 'Quoted transcript: Correction: use delay 8 seconds.', role='tool')
        pack = self.recover('session-a', 'Continue pause recovery.')
        assertion = next(a for a in pack['task_frame']['assertions'] if a['event_id'] == tool)
        self.assertIn('context', assertion['categories'])
        self.assertNotIn('correction', assertion['categories'])
        self.assertNotIn('requirement', assertion['categories'])
        self.assertFalse(any(r['to'] == tool for r in pack['task_frame']['relations']))
        self.assertIn(tool, {r['event_id'] for r in pack['selected_records']})


    def test_raw_tool_report_does_not_adjudicate_an_open_issue(self):
        self.capture('history', 'one', 'Unresolved: pause handling needs verification.')
        tool = self.capture('history', 'two', 'Quoted report: test passed. ' * 300, role='tool')
        pack = self.recover('session-a', 'Continue pause recovery.')
        self.assertEqual(pack['metrics']['relation_units_evaluated'], 0)
        self.assertIn(tool, {r['event_id'] for r in pack['selected_records']})
        self.assertTrue(all(a['implementation_status'] == 'not_established' for a in pack['task_frame']['assertions']))
