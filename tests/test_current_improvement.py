"""Mechanical development cases; do not substitute for independent recall tests."""
import copy
import json
import unittest

import test_core_context as core
import test_context_reuse as reuse
from jcm import query_context, representations
from jcm.evidence import lookup
from jcm.util import digest, encode


class CompletionTests(unittest.TestCase):
    setUp = core.CoreContextTests.setUp
    tearDown = core.CoreContextTests.tearDown
    source = core.CoreContextTests.source

    def complete(self, text, spans, allowed=None, limit=False):
        source = self.source('nested', text, 'tool')
        if allowed is not None:
            source['spans'] = allowed
        selection = {'text':'\n\n[... optional detail ...]\n\n'.join(text[s['start']:s['end']] for s in spans),
                     'spans':spans, 'representation':'brief'}
        rep = {**source, 'query':selection}
        question = {'sources':[{'event_id':source['event_id'],'decision':'unresolved' if limit else 'keep'}]}
        if limit:
            question['qualification_limits'] = [{'event_id':source['event_id'], 'revision':1, 'spans':spans}]
        frame, gaps = query_context.complete(self.store, question, [rep], [source], [source],
            {'task_id':'fixture','assertions':[], 'relations':[]})
        self.assertEqual(gaps, [])
        return question, rep, frame

    def test_cheaper_exact_enclosure_is_frozen_and_scope_stays_unresolved(self):
        text = '{"artifact":"/builds/cedar-A","status":"pending","detail":"signature absent"}'
        spans = [{'start':2,'end':30}, {'start':32,'end':48}, {'start':50,'end':len(text)-2}]
        before = {'text':'\n\n[... optional detail ...]\n\n'.join(text[s['start']:s['end']] for s in spans),'spans':spans}
        plan, rep, frame = self.complete(text, spans, limit=True)
        self.assertEqual(rep['query']['text'], text)
        self.assertEqual(rep['query']['spans'], [{'start':0,'end':len(text)}])
        self.assertLessEqual(len(encode({k:rep['query'][k] for k in ('text','spans')})),len(encode(before)))
        self.assertTrue(frame['qualification_limits'][0]['enclosing_source_delivered'])
        self.assertFalse(frame['qualification_boundary_policy']['complete_recovery_established'])
        self.assertEqual(plan['sources'][0]['decision'], 'unresolved')
        delivered = {'selected_records':[copy.deepcopy(plan['sources'][0]['required_record'])], 'task_frame':copy.deepcopy(frame)}
        self.assertEqual(representations.validate_contract(plan, delivered), [])
        delivered['task_frame']['qualification_limits'][0]['enclosing_source_delivered'] = False
        self.assertIn('REQUIRED_FRAME_CHANGED:qualification_limits', representations.validate_contract(plan, delivered))

    def test_large_or_rejected_enclosure_is_not_reintroduced(self):
        text = 'pending result\n\n' + 'unrelated archive ' * 200
        plan, rep, frame = self.complete(text, [{'start':0,'end':14}], limit=True)
        self.assertNotIn('unrelated archive',rep['query']['text'])
        self.assertFalse(frame['qualification_limits'][0]['enclosing_source_delivered'])
        text = 'AA omitted BB'
        spans = [{'start':0,'end':2},{'start':11,'end':13}]
        _, rep, _ = self.complete(text, spans, allowed=spans)
        self.assertNotIn('omitted',rep['query']['text'])

    def test_cheaper_enclosure_does_not_promote_an_optional_peer_reference(self):
        from jcm.source_units import units
        target=self.source('optional-original','Unrelated archive text. '*300,'tool')
        text=json.dumps({'records':[{'artifact':'cedar-A','status':'pending','code':'MESH_27','scope':'fixture'},
                                  {'jcm_source_reference':target['event_id']}]})
        owner=self.source('wrapper',text,'tool');owner['derived_from']=[target['event_id']]
        spans=[{k:u['span'][k] for k in ('start','end')} for u in units(owner) if u['source_path'][:2]==['records',0]]
        rep={**owner,'query':{'text':'','spans':spans,'representation':'brief'}}
        question={'sources':[{'event_id':owner['event_id'],'decision':'keep'}]}
        query_context.complete(self.store,question,[rep],[owner],[owner,target],{'assertions':[],'relations':[]})
        self.assertEqual(rep['query']['text'],text)
        self.assertEqual({s['event_id'] for s in question['sources']},{owner['event_id']})


class CurrentRequestTests(unittest.TestCase):
    def setUp(self):
        core.CoreContextTests.setUp(self)
        self.sent = []
    tearDown = reuse.TaskReuseTests.tearDown
    capture = reuse.TaskReuseTests.capture
    provider = reuse.TaskReuseTests.provider
    recover = reuse.TaskReuseTests.recover
    transport = reuse.TaskReuseTests.transport
    def test_membership_receives_current_question_on_confirmed_task(self):
        self.capture('history','rule','Continue the Cedar release /builds/cedar-A. Preserve its interlock.')
        first = self.recover('one','Continue the Cedar release /builds/cedar-A.')
        offset = len(self.sent)
        request = 'For /builds/cedar-A, explain only its pending hardware result and whether /archive/cedar-B affects it.'
        second = self.recover('two',request)
        retrieval = [p for p in self.sent[offset:] if any(n.endswith('_applicability') for n in p['questions'])]
        self.assertTrue(retrieval)
        self.assertTrue(all(p['state']['context']['request'] == request for p in retrieval))
        self.assertEqual(first['task_frame']['task_id'],second['task_frame']['task_id'])
        # Changed question is a semantic dependency; classification stays reusable.
        self.assertGreater(second['metrics']['source_units_evaluated'],0)
        self.assertGreater(second['metrics']['classification_units_reused'],0)

    def test_lookup_exposes_frozen_coverage_without_claiming_retention(self):
        self.capture('history','rule','Cedar /builds/cedar-A protocol 52. Hardware pending.')
        pack = self.recover('consumer','Continue Cedar /builds/cedar-A.')
        result = lookup(self.store, pack['pack_id'],'protocol 52')
        self.assertTrue(result['matches'])
        required = {s['event_id']:s for s in pack['query_context']['sources']}
        for hit in result['matches']:
            self.assertEqual(hit['required_view_evidence']['spans'],required[hit['event_id']]['required_spans'])
            self.assertIn('not a receipt',hit['required_view_evidence']['meaning'])
            self.assertFalse(hit['snippet_is_complete_source'])

    def test_new_artifact_comparison_reassesses_an_earlier_excluded_source(self):
        self.capture('history','goal','Continue Cedar release /builds/cedar-A. Keep the interlock enabled.')
        other=self.capture('archive','result','Independent /archive/cedar-B has CEDAR_OLD_31; no relationship to /builds/cedar-A is established.',role='tool')
        normal=self.transport
        def scoped(body,key):
            response=normal(body,key);payload=json.loads(body)
            for name,q in payload['questions'].items():
                if name.endswith('_applicability'):
                    item=payload['state']['items'][int(name.split('_')[0][1:])]
                    if item['event_id']==other:
                        value='direct' if 'cedar-B' in payload['state']['context']['request'] else 'unrelated'
                        response['answers'][name].update(choice=value,probabilities={k:float(k==value) for k in q['criteria']})
            return response
        self.transport=scoped
        first=self.recover('one','Continue Cedar release /builds/cedar-A.')
        self.assertNotIn(other,{s['event_id'] for s in first['selected_records']})
        second=self.recover('two','For /builds/cedar-A, does the /archive/cedar-B result establish a blocker?')
        self.assertIn(other,{s['event_id'] for s in second['selected_records']})
        self.assertIn('no relationship',str(second['context_views']['brief']))
        self.assertEqual(first['task_frame']['task_id'],second['task_frame']['task_id'])


if __name__ == '__main__':
    unittest.main()
