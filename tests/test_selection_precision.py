import json
import unittest
from unittest.mock import patch

import test_continuity as fixtures
from jcm.coordinator import dispatch


class SelectionPrecisionTests(unittest.TestCase):
    setUp = fixtures.ContinuityTests.setUp
    tearDown = fixtures.ContinuityTests.tearDown
    capture = fixtures.ContinuityTests.capture
    request = fixtures.ContinuityTests.request
    provider = fixtures.ContinuityTests.provider
    def selected_fixture(self):
        task = self.capture('old', 'task', 'Use the animation workflow.')
        context = self.capture('old', 'task', 'Prepare 16 warmup clips using the existing rig.', role='assistant')
        direct = self.capture('old', 'result', 'Warmup clip correction: preserve the foot contact.', role='tool')
        shared = self.capture('old', 'rule', 'All animation assets must preserve the 22 bone rig.', role='tool')
        noise = self.capture('old', 'noise', 'JCM recovery diagnostics: an important unresolved transport error.', role='tool')
        uncertain = self.capture('old', 'uncertain', 'Contact result needs the parent clip identity.', role='tool')
        omitted = self.capture('old', 'omit', 'Redundant background.', role='tool')
        token = self.request('Recover the warmup task and report reading receipt status.')
        late = self.capture('fresh', 'now', 'Output of this recovery must not become its own input.', role='tool')
        self.store.db.execute('INSERT INTO meta VALUES (?,?)', ('entry_selection:' + token, json.dumps({'task_id': task})))
        self.store.db.execute("UPDATE jobs SET state='succeeded'")
        return token, dict(task=task, context=context, direct=direct, shared=shared, noise=noise,
                           uncertain=uncertain, omitted=omitted, late=late)

    def test_task_applicability_precedes_high_omission_risk_and_original_context_is_sent(self):
        token, ids = self.selected_fixture()
        observed = []
        def transport(body, key):
            payload = fixtures.fixture_payload(body)
            result = fixtures.fake_http(body, key)
            if 'candidates' in payload['state']:
                observed.append(payload['state'])
            for i, candidate in enumerate(payload['state'].get('candidates', [])):
                answers = result['answers']
                answers[f'relevance_{i}'].update(score=3, probabilities={str(n): float(n == 3) for n in range(4)})
                answers[f'omission_{i}']['noul'] = 1
                value = ('unrelated' if candidate['event_id'] in (ids['noise'], ids['context']) else
                         'shared' if candidate['event_id'] == ids['shared'] else
                         'uncertain' if candidate['event_id'] == ids['uncertain'] else 'direct')
                answer = answers[f'applicability_{i}']; answer['choice'] = value
                answer['probabilities'] = {v: float(v == value) for v in answer['probabilities']}
                if candidate['event_id'] == ids['shared']:
                    answer.update(choice='unrelated', probabilities={'direct': .4, 'shared': .15, 'uncertain': 0, 'unrelated': .45})
                if candidate['event_id'] == ids['omitted']:
                    answer = answers[f'representation_{i}']; answer['choice'] = 'omit'
                    answer['probabilities'] = {v: float(v == 'omit') for v in answer['probabilities']}
            return result
        route = dispatch(self.store, token, self.provider(transport))
        pack = self.store.blob(self.store.db.execute('SELECT blob FROM packs WHERE id=?', (route['pack_id'],)).fetchone()[0])
        selected = {r['event_id'] for r in pack['selected_records']}
        self.assertTrue({ids[k] for k in ('task','context','direct','shared','uncertain')} <= selected)
        self.assertTrue({ids[k] for k in ('noise','omitted','late')}.isdisjoint(selected))
        self.assertNotIn(ids['late'], {c['event_id'] for state in observed for c in state['candidates']})
        self.assertIn('16 warmup clips', str(observed[0]['task_scope']))
        self.assertIn('transport error', self.store.blob(self.store.event(ids['noise'])['blob'])['text'])

    def test_omitted_reference_span_does_not_reintroduce_unrelated_original(self):
        original = 'Earlier unrelated output. ' * 30
        source = self.capture('old', 'original', original, role='assistant')
        copied = self.capture('old', 'copy', json.dumps({'copy': original, 'novel': 'New applicable result'}), role='tool')
        token = self.request('Recover only the new applicable result')
        self.store.db.execute("UPDATE jobs SET state='succeeded'")
        material = self.store.material(self.store.event(copied))
        self.assertIn(source, material['derived_from'])
        start = material['text'].index('New applicable result')
        empty = {'complete': True, 'spans': [], 'relevance': 0, 'omission': 0, 'representation': 'full'}
        assessment = {**empty, 'spans': [{'start': start, 'end': start + len('New applicable result')}], 'relevance': 3}
        semantic = {'assessments': [empty, assessment], 'intent': 'resume', 'decisions': [],
                    'errors': [], 'batches': [], 'relations': []}
        with patch('jcm.coordinator.select', return_value=semantic):
            route = dispatch(self.store, token, self.provider())
        pack = self.store.blob(self.store.db.execute('SELECT blob FROM packs WHERE id=?', (route['pack_id'],)).fetchone()[0])
        self.assertEqual([r['event_id'] for r in pack['selected_records']], [copied])
        self.assertEqual(pack['selected_records'][0]['derived_from'], [])
        self.assertEqual(pack['selected_records'][0]['text'], 'New applicable result')
        self.assertEqual(self.store.blob(self.store.event(source)['blob'])['text'], original)

    def test_unknown_turn_does_not_protect_all_session_reports(self):
        task = self.capture('old', 'task', 'Recover the network task')
        unrelated = self.capture('old', 'other', 'An unrelated theme task report', role='assistant')
        self.store.db.execute("UPDATE events SET turn=NULL WHERE session='old'")
        token = self.request('Recover network work')
        self.store.db.execute('INSERT INTO meta VALUES (?,?)', ('entry_selection:' + token, json.dumps({'task_id': task})))
        self.store.db.execute("UPDATE jobs SET state='succeeded'")
        route = dispatch(self.store, token, self.provider())
        pack = self.store.blob(self.store.db.execute('SELECT blob FROM packs WHERE id=?', (route['pack_id'],)).fetchone()[0])
        self.assertEqual({r['event_id'] for r in pack['selected_records']}, {task})
        self.assertIn(unrelated, {r['event_id'] for r in pack['excluded_records']})
