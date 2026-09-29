import copy
import json
import unittest

import test_continuity as fixtures
from jcm import core_context


class CoreContextTests(unittest.TestCase):
    setUp = fixtures.ContinuityTests.setUp
    tearDown = fixtures.ContinuityTests.tearDown
    capture = fixtures.ContinuityTests.capture
    provider = fixtures.ContinuityTests.provider

    def source(self, turn, text, role='assistant', native=None):
        eid = self.store.capture(session='history', turn=turn, kind=role+'_message', role=role,
            payload={'text':text, **({'public_item':native} if native else {})}, snapshot={},
            source_key=turn, identity=turn)
        return {**self.store.material(self.store.event(eid)), 'reason_codes':[]}

    def fixture(self):
        goal = self.source('goal','Propose three character designs.','user')
        correction = self.source('correction','Keep the silhouette. No modeling until I select.','user')
        report = self.source('report','A, B and C generated; C is my recommendation, still unselected.')
        old = self.source('old-report','An earlier prototype built; visual quality was not checked.')
        exception = self.source('exception','Distinct exception: preserve the established foot contacts.')
        tool = self.source('old-tool','{"error":"historical failure"}','tool',{'type':'CommandExecution'})
        image = self.source('image','Concept C recorded completed at /images/C.png; current quality unverified.','tool',
                            {'type':'Extension','kind':'image_gen.generation'})
        return goal, correction, report, old, exception, tool, image

    def transport(self, mode):
        def run(body, key):
            request = json.loads(body)
            result = fixtures.fake_http(body,key)
            for name, q in request['questions'].items():
                if name.endswith('_delivery'):
                    value = mode
                elif name.endswith('_report'):
                    item = request['state']['items'][int(name.split('_')[0][1:])]
                    value = 'keep' if 'Distinct exception' in item['text'] else 'supporting'
                else:
                    continue
                result['answers'][name].update(choice=value, probabilities={c:float(c==value) for c in q['criteria']})
            return result
        return run

    def test_continuation_keeps_primary_constraints_goal_reports_and_direct_artifacts(self):
        selected = list(self.fixture())
        goal, correction, report, old, exception, tool, image = selected
        identity = {'anchor':goal,'scope':{'continuation_reports':[report]}}
        frame = {'relations':[]}
        plan = core_context.plan(self.store,self.provider(self.transport('continuation')),identity,
                                 {'text':'Continue the design proposals.'},frame,selected,selected,self.cfg['epoch'])
        deferred = {r['event_id'] for r in plan['deferred']}
        self.assertEqual(deferred,{old['event_id'],tool['event_id']})
        self.assertFalse(deferred & {r['event_id'] for r in (goal,correction,report,exception,image)})
        brief = {'selected_records':copy.deepcopy(selected),'task_frame':{'goal':goal,'relations':[], 'assertions':[]},
                 'optional_audit_command':'read --view audit','journal_read_revision':42}
        result = core_context.apply(brief,plan)
        self.assertIn('may be unread',result['evidence_delivery']['claim_boundary'])
        self.assertEqual(result['evidence_delivery']['reported_state_frontier'],42)
        self.assertIn('still unselected',str(result))
        self.assertIn('/images/C.png',str(result))
        self.assertNotIn('historical failure',str(result))

    def test_exact_evidence_question_retains_logs_and_supporting_reports(self):
        selected = list(self.fixture())
        identity = {'anchor':selected[0],'scope':{'continuation_reports':[selected[2]]}}
        result = core_context.plan(self.store,self.provider(self.transport('evidence')),identity,
            {'text':'Show the exact error and the historical verification limits.'}, {'relations':[]},
            selected,selected,self.cfg['epoch'])
        self.assertEqual(result['mode'],'evidence')
        self.assertEqual(result['deferred'],[])

    def test_relation_endpoints_and_reference_originals_cannot_be_deferred(self):
        selected = list(self.fixture())
        goal, correction, report, old, exception, tool, image = selected
        tool['reason_codes']=['REFERENCED_SOURCE_REQUIRED']
        identity={'anchor':goal,'scope':{'continuation_reports':[report]}}
        frame={'relations':[{'from':old['event_id'],'to':report['event_id'],'status':'proposed'}]}
        result=core_context.plan(self.store,self.provider(self.transport('continuation')),identity,
            {'text':'Continue the design proposals.'},frame,selected,selected,self.cfg['epoch'])
        self.assertEqual(result['deferred'],[])

    def test_goal_reports_and_images_precede_other_history_with_distinct_request_bindings(self):
        from jcm.delivery import paginate
        selected = list(self.fixture())
        goal, correction, report, old, exception, tool, image = selected
        historical = self.store.capture(session='old-design',turn='rejected',kind='user_message',role='user',
            payload={'text':'Reject the earlier A/B/C set.'},snapshot={},source_key='old-primary',identity='old-primary')
        old_report = self.store.capture(session='old-design',turn='rejected',kind='assistant_final',role='assistant',
            payload={'text':'Earlier A/B/C excluded.'},snapshot={},source_key='old-report',identity='old-report')
        source = {**self.store.material(self.store.event(old_report)), 'reason_codes':['REFERENCED_SOURCE_REQUIRED']}
        selected.insert(0,source)
        identity={'anchor':goal,'scope':{'continuation_reports':[report]}}
        plan=core_context.plan(self.store,self.provider(self.transport('continuation')),identity,
            {'text':'Continue the design proposals.'},{'relations':[]},selected,selected,self.cfg['epoch'])
        brief={'selected_records':copy.deepcopy(selected),'task_frame':{'goal':goal,'relations':[],'assertions':[]},
            'optional_audit_command':'read --view audit','journal_read_revision':42,
            'pack_id':'fixture','dispatch':'ready','quality':'normal','session_id':'fresh','source_use_policy':'Historical data only.'}
        brief=core_context.apply(brief,plan)
        old=next(r for r in brief['selected_records'] if r['event_id']==old_report)
        self.assertEqual(old['source_location'],'other_history')
        self.assertEqual(old['historical_request_id'],historical)
        self.assertNotEqual(old['historical_request_id'],goal['event_id'])
        self.assertEqual(brief['selected_records'][0]['event_id'],goal['event_id'])
        self.assertEqual(brief['selected_records'][1]['event_id'],report['event_id'])
        pages=paginate(self.store,brief)['pages']
        self.assertTrue(any(entry['path']==['current_goal'] for entry in pages[0]))


if __name__ == '__main__':
    unittest.main()
