"""Current-question delivery must not replace reusable task knowledge."""
import json
import re
import unittest

import test_continuity as fixtures
import test_context_reuse as reuse
from jcm.util import JCMError


class QueryContextTests(unittest.TestCase):
    def setUp(self):
        fixtures.ContinuityTests.setUp(self)
        self.sent = []
    tearDown = fixtures.ContinuityTests.tearDown
    capture = fixtures.ContinuityTests.capture
    provider = fixtures.ContinuityTests.provider
    recover = reuse.TaskReuseTests.recover

    @staticmethod
    def focus(request):
        if 'failure' in request:
            return 'failure'
        if 'exception' in request or 'special case' in request:
            return 'exception'
        return 'resume'

    def transport(self, body, key):
        response = reuse.TaskReuseTests.transport(self, body, key)
        payload = json.loads(body)
        for name, question in payload['questions'].items():
            match = re.match(r'i(\d+)_(.+)', name)
            if not match:
                continue
            item = payload['state']['items'][int(match[1])]
            field = match[2]
            context = payload['state']['context']
            selected = None
            if field == 'query_equivalence':
                selected = 'same' if self.focus(item['request']) == self.focus(context['request']) else 'different'
            elif field == 'query_level':
                focus = self.focus(context['request'])
                selected = ('full' if focus == 'failure' else 'brief' if focus == 'resume' or
                            item['paragraphs'][0]['text'].startswith('EXPLAIN:') else 'omit')
            elif field.startswith('query_block_'):
                focus = self.focus(context['request'])
                paragraph = item['paragraphs'][int(field.removeprefix('query_block_'))]['text']
                selected = 'support' if paragraph.startswith('LOG:') else 'core'
                if focus == 'exception' and not paragraph.startswith(('EXPLAIN:', 'Exception:')):
                    selected = 'omit'
            elif field.startswith('preservation_'):
                paragraph = item['paragraphs'][int(field.removeprefix('preservation_'))]['text']
                selected = 'required' if paragraph.startswith('Exception:') else 'optional'
            if selected is not None:
                response['answers'][name].update(choice=selected,
                    probabilities={v: float(v == selected) for v in question['criteria']})
        return response

    def corpus(self):
        self.rule = self.capture('history', 'rule', 'Never resume automatically. Preserve protocol 47. Use delay 5 seconds.')
        self.explanation = self.capture('history', 'explanation',
            'EXPLAIN: A manual action is the special case for pause recovery.\n\n'
            'LOG: handler sequence 7 -> callback 8 -> failure code 42.\n\n'
            'Exception: never auto-resume when the user explicitly paused.', role='assistant')
        self.design = self.capture('history', 'design',
            'DESIGN: Historical comparison of pause handler layout alternatives.\n\n'
            'LOG: trace of a successful callback.', role='assistant')

    def required(self, pack):
        return {s['event_id']: s for s in pack['context_views']['brief']['selected_records']}

    def test_focus_changes_evidence_and_detail_without_changing_task_membership(self):
        self.corpus()
        resume = self.recover('a', 'Continue pause recovery.')
        exception = self.recover('b', 'Explain only the exception to automatic resume in pause recovery.')
        failure = self.recover('c', 'Analyze the pause recovery failure with the full callback trace.')
        self.assertEqual(len({p['task_frame']['task_id'] for p in (resume, exception, failure)}), 1)
        self.assertGreater(exception['metrics']['source_units_evaluated'], 0)
        self.assertGreater(exception['metrics']['classification_units_reused'], 0)
        self.assertIn(self.design, self.required(resume))
        self.assertNotIn(self.design, self.required(exception))
        self.assertNotIn('failure code 42', self.required(exception)[self.explanation]['text'])
        self.assertIn('failure code 42', self.required(failure)[self.explanation]['text'])
        self.assertEqual(self.required(failure)[self.explanation]['representation'], 'full')
        for pack in (resume, exception, failure):
            self.assertIn(self.rule, self.required(pack))
            self.assertIn('never auto-resume', self.required(pack)[self.explanation]['text'])
            self.assertIn(self.design, {a['event_id'] for a in pack['task_frame']['assertions']})
            self.assertIn(self.design, {s['event_id'] for s in pack['context_views']['full']['selected_records']})
            self.assertEqual(pack['query_context']['request'], pack['request'])

    def test_paraphrase_reuses_question_selection_and_correction_is_preserved(self):
        self.corpus()
        self.recover('a', 'Continue pause recovery.')
        first = self.recover('b', 'Explain only the exception to automatic resume in pause recovery.')
        before = len(self.sent)
        paraphrase = self.recover('c', 'Describe the special case in pause recovery.')
        self.assertEqual(paraphrase['query_context']['route'], 'equivalent_question')
        self.assertEqual(first['query_context']['id'], paraphrase['query_context']['id'])
        # The previous procedural request is a newly admitted source. Reusing
        # the question must avoid retransmitting the already assessed evidence.
        reassessed = {item.get('event_id') for payload in self.sent[before:]
                      if any(name.endswith('_query_level') for name in payload['questions'])
                      for item in payload['state']['items']}
        self.assertTrue({self.rule, self.explanation, self.design}.isdisjoint(reassessed))
        self.assertGreater(paraphrase['metrics']['query_units_reused'], 0)
        correction = self.recover('d', 'Correction: use delay 8 seconds. Explain only the exception in pause recovery.')
        self.assertIn('8 seconds', str(correction['context_views']['brief']))
        self.assertTrue(any(a['state'] == 'disputed' for a in correction['task_frame']['assertions'] if a['event_id'] == self.rule))
        self.assertTrue(all(a['implementation_status'] == 'not_established' for a in correction['task_frame']['assertions']))

    def test_low_old_query_relevance_does_not_hide_task_member_from_new_question(self):
        self.corpus()
        original = self.transport
        def low_relevance(body, key):
            response = original(body, key)
            for name, answer in response['answers'].items():
                if name.endswith('_relevance'):
                    answer.update(score=0, probabilities={str(i): float(i == 0) for i in range(4)})
                elif name.endswith('_omission'):
                    answer['noul'] = 0
                elif name.endswith('_representation'):
                    answer.update(choice='omit', probabilities={'full': 0, 'omit': 1})
            return response
        self.transport = low_relevance
        pack = self.recover('a', 'Analyze pause recovery failure with full trace.')
        self.assertIn('failure code 42', self.required(pack)[self.explanation]['text'])

    def test_uncertain_equivalence_cannot_reuse_focus_and_failure_preserves_full_sources(self):
        self.corpus()
        first = self.recover('a', 'Continue pause recovery.')
        original = self.transport
        def uncertain(body, key):
            payload = json.loads(body)
            if any('_query_level' in k for k in payload['questions']):
                raise JCMError('PROVIDER_OFFLINE')
            response = original(body, key)
            for name, answer in response['answers'].items():
                if name.endswith('_query_equivalence'):
                    answer.update(choice='uncertain', probabilities={'same': .3, 'different': .3, 'uncertain': .4})
            return response
        self.transport = uncertain
        pack = self.recover('b', 'Explain only the exception in pause recovery.')
        self.assertNotEqual(first['query_context']['id'], pack['query_context']['id'])
        self.assertEqual(pack['quality'], 'degraded')
        self.assertIn('failure code 42', self.required(pack)[self.explanation]['text'])
        self.assertIn(self.design, self.required(pack))

    def test_revision_invalidation_and_forgetting_cover_question_derivatives(self):
        self.corpus()
        first = self.recover('a', 'Explain only the exception in pause recovery.')
        self.store.db.execute('UPDATE events SET revision=revision+1 WHERE id=?', (self.explanation,))
        second = self.recover('b', 'Explain only the exception in pause recovery.')
        self.assertGreater(second['metrics']['query_units_evaluated'], 0)
        self.assertEqual(self.required(second)[self.explanation]['revision'], 2)
        self.store.forget_session('history')
        for table in ('query_views', 'semantic_items', 'representations'):
            self.assertEqual(self.store.db.execute('SELECT count(*) FROM ' + table).fetchone()[0], 0)

    def test_relative_question_is_not_reused_after_new_evidence(self):
        self.corpus()
        original = self.transport
        def relative(body, key):
            response = original(body, key)
            for name, answer in response['answers'].items():
                if name.endswith('_query_referent'):
                    answer.update(choice='relative', probabilities={'anchored': 0, 'relative': 1, 'uncertain': 0})
            return response
        self.transport = relative
        first = self.recover('a', 'Analyze the latest failure in pause recovery.')
        self.capture('history', 'new', 'A later callback observation.', role='tool')
        second = self.recover('b', 'Analyze the latest failure in pause recovery.')
        self.assertNotEqual(first['query_context']['id'], second['query_context']['id'])
        self.assertGreater(second['metrics']['query_units_evaluated'], 0)

    def test_omission_cannot_override_mandatory_exception_or_user_constraint(self):
        self.corpus()
        original = self.transport
        def omit(body, key):
            response = original(body, key)
            payload = json.loads(body)
            for name, question in payload['questions'].items():
                if name.endswith('_query_level') or '_query_block_' in name:
                    response['answers'][name].update(choice='omit', probabilities={v: float(v == 'omit') for v in question['criteria']})
            return response
        self.transport = omit
        pack = self.recover('a', 'Explain the exception in pause recovery.')
        self.assertIn(self.rule, self.required(pack))
        self.assertIn('never auto-resume', self.required(pack)[self.explanation]['text'])
        self.assertNotIn('failure code 42', self.required(pack)[self.explanation]['text'])

    def test_all_unrelated_passages_do_not_fall_back_to_full_on_aggregate_uncertainty(self):
        self.corpus()
        original = self.transport
        def omit(body, key):
            response = original(body, key)
            for name, answer in response['answers'].items():
                if name.endswith('_query_level'):
                    answer.update(choice='omit', probabilities={'omit':.76, 'brief':.12, 'detail':.11, 'full':.01})
                elif '_query_block_' in name:
                    answer.update(choice='omit', probabilities={'core':0, 'support':.05, 'omit':.95})
                elif '_query_guard_' in name:
                    answer['noul'] = 0
            return response
        self.transport = omit
        pack = self.recover('a', 'Explain the exception in pause recovery.')
        self.assertNotIn(self.design, self.required(pack))
        self.assertNotIn(self.explanation, self.required(pack))
        self.assertIn(self.design, {r['event_id'] for r in pack['task_records']})

    def test_current_question_revision_is_a_delivery_dependency(self):
        from jcm.coordinator import read_pack
        self.corpus()
        self.recover('a', 'Continue pause recovery.')
        pack = self.recover('b', 'Explain the exception in pause recovery.')
        request_id = self.store.db.execute('SELECT event_id FROM requests WHERE token=?', (pack['request_token'],)).fetchone()[0]
        self.assertNotEqual(request_id, pack['task_frame']['goal']['event_id'])
        self.store.db.execute('UPDATE events SET revision=revision+1 WHERE id=?', (request_id,))
        with self.assertRaisesRegex(JCMError, 'SEMANTIC_DEPENDENCY_CHANGED'):
            read_pack(self.store, pack['pack_id'])

    def test_brief_combines_optional_outcomes_but_preserves_uncertain_core_evidence(self):
        self.corpus()
        original = self.transport
        core_probability = [.02]
        def optional(body, key):
            response = original(body, key)
            payload = json.loads(body)
            for name, question in payload['questions'].items():
                if name.endswith('_query_block_1'):
                    core = core_probability[0]
                    response['answers'][name].update(choice='omit', confidence=.5,
                        probabilities={'core': core, 'support': .33, 'omit': .67 - core})
            return response
        self.transport = optional
        first = self.recover('a', 'Explain only the exception in pause recovery.')
        self.assertNotIn('failure code 42', self.required(first)[self.explanation]['text'])
        core_probability[0] = .3
        self.store.db.execute('UPDATE events SET revision=revision+1 WHERE id=?', (self.explanation,))
        second = self.recover('b', 'Explain only the exception in pause recovery.')
        self.assertIn('failure code 42', self.required(second)[self.explanation]['text'])

    def test_schema5_upgrade_preserves_journal_and_adds_forgettable_query_profiles(self):
        from jcm.store import Store
        self.corpus()
        self.store.db.execute('DROP TABLE query_views')
        self.store.db.execute('PRAGMA user_version=5')
        self.store.close()
        self.store = Store(self.cfg)
        self.assertEqual(self.store.db.execute('PRAGMA user_version').fetchone()[0], 6)
        self.assertIn('protocol 47', self.store.material(self.store.event(self.rule))['text'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM query_views').fetchone()[0], 0)

    def test_detail_level_covers_split_brief_detail_probability_without_restoring_logs(self):
        self.corpus()
        original = self.transport
        def split(body, key):
            response = original(body, key)
            for name, answer in response['answers'].items():
                if name.endswith('_query_level'):
                    answer.update(choice='detail', probabilities={'omit': .1, 'brief': .28, 'detail': .6, 'full': .02})
            return response
        self.transport = split
        pack = self.recover('a', 'Explain the exception in pause recovery.')
        record = self.required(pack)[self.explanation]
        self.assertEqual(record['representation'], 'detail')
        self.assertNotIn('failure code 42', record['text'])

    def test_different_property_cannot_dispute_an_unchanged_constraint(self):
        self.corpus()
        original = self.transport
        def cross_property(body, key):
            response = original(body, key)
            payload = json.loads(body)
            for name, question in payload['questions'].items():
                item = payload['state']['items'][int(name.split('_')[0][1:])]
                if 'older' not in item or 'protocol 47' not in item['older']['text']:
                    continue
                answer = response['answers'][name]
                if name.endswith('_same_property'):
                    answer.update(choice='different', probabilities={'same': 0, 'different': 1, 'uncertain': 0})
                elif name.endswith('_relation'):
                    answer.update(choice='corrects', probabilities={v: float(v == 'corrects') for v in question['criteria']})
                elif name.endswith('_affects_assertion'):
                    answer['noul'] = .6
            return response
        self.transport = cross_property
        self.recover('a', 'Continue pause recovery.')
        pack = self.recover('b', 'Correction: use delay 8 seconds. Explain the exception.')
        protocol = [a for a in pack['task_frame']['assertions'] if a['event_id'] == self.rule and 'protocol 47' in a['text']]
        self.assertEqual({a['state'] for a in protocol}, {'active_evidence'})


if __name__ == '__main__':
    unittest.main()
