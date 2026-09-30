"""Frozen native preservation cases; mocks test mechanics, not semantic recall."""
import copy
import json
import unittest
from unittest.mock import patch

import test_continuity as fixtures
import test_core_context as core_fixtures
from jcm import core_context, detail_plan, query_context, representations


class DeliveryContractTests(unittest.TestCase):
    setUp = core_fixtures.CoreContextTests.setUp
    tearDown = core_fixtures.CoreContextTests.tearDown
    source = core_fixtures.CoreContextTests.source
    provider = core_fixtures.CoreContextTests.provider
    def pipeline(self, native_wrapper=True):
        goal = self.source('goal', 'Continue designing the character.', 'user')
        blocker = self.source('native' if native_wrapper else 'text-equivalent', 'BLOCKER_47: Character export failed. The required skeleton is missing. Do not use this exported asset.',
            'tool', {'type':'CommandExecution', 'command':['export'], 'exit_code':0, 'status':'completed'} if native_wrapper else None)
        old = self.source('old', 'OLD_UNRELATED_19: Resolved failure for the retired album project.', 'tool',
            {'type':'CommandExecution', 'command':['export'], 'exit_code':0, 'status':'completed'})
        request = self.source('current', 'Continue the character work.', 'user')
        identity = {'id':'a'*64, 'anchor':goal, 'scope':{'selected_source':goal, 'continuation_reports':[goal]}}
        selected = [goal, blocker, old]
        frame = {'read_revision':1, 'goal':goal, 'assertions':[], 'relations':[]}
        def transport(body, key):
            payload = json.loads(body); result = fixtures.fake_http(body, key)
            for name, q in payload['questions'].items():
                item = payload['state']['items'][int(name.split('_')[0][1:])]
                unrelated = 'OLD_UNRELATED_19' in str(item)
                if 'query_guard_' in name:
                    result['answers'][name]['noul'] = 0 if unrelated else 1
                elif q['type'] == 'choice':
                    value = ('continuation' if name.endswith('_delivery') else 'brief' if name.endswith('_query_level') else
                             ('omit' if unrelated else 'core') if '_query_block_' in name else None)
                    if value:
                        result['answers'][name].update(choice=value, probabilities={k:float(k==value) for k in q['criteria']})
            return result
        provider = self.provider(transport)
        reps,_ = representations.build(self.store,provider,identity,selected,selected,self.cfg['epoch'])
        reps,question,_ = query_context.build(self.store,provider,identity,request,frame,selected,selected,reps,self.cfg['epoch'])
        focused,_ = query_context.selected_records(selected,reps,selected)
        plan = core_context.plan(self.store,provider,identity,request,frame,focused,selected,self.cfg['epoch'])
        brief = {'selected_records':[{**s,**r['query']} for s,r in zip(selected,reps) if r['query']['spans']],
                 'task_frame':copy.deepcopy(frame), 'optional_audit_command':'audit', 'journal_read_revision':4}
        return representations.compact_brief(core_context.apply(brief,plan),frame), question, blocker

    def test_selected_native_blocker_survives_but_body_assessed_old_failure_does_not(self):
        brief, _, blocker = self.pipeline()
        self.assertIn('BLOCKER_47', str(brief))
        self.assertNotIn('OLD_UNRELATED_19', str(brief['selected_records']))
        equivalent,_,_ = self.pipeline(native_wrapper=False)
        self.assertIn('BLOCKER_47', str(equivalent['selected_records']))

    def test_descriptor_alone_never_removes_exit_zero_body(self):
        source = self.source('native', 'Unique current blocker. ' * 1000, 'tool',
            {'type':'CommandExecution','command':['export'],'status':'completed','exit_code':0})
        provider = self.provider()
        kept,deferred,_ = detail_plan.plan(self.store,provider,[self.store.event(source['event_id'])],
            {'id':'a'*64,'scope':{}},'Continue',self.cfg['epoch'])
        self.assertEqual(provider.observed_decisions,[])
        self.assertEqual(len(kept),1)
        self.assertEqual(deferred,[])

    def test_near_half_guard_preserves_enclosing_source_and_specific_uncertainty(self):
        source = self.source('condition','Ready for fixture preview.\n\nLate condition: hardware validation is pending; preserve all unchanged clauses.','tool')
        request = self.source('ask','Continue preview work.','user')
        identity = {'id':'a'*64,'anchor':request,'scope':{'selected_source':request}}
        def transport(body,key):
            response = fixtures.fake_http(body,key)
            for name,q in json.loads(body)['questions'].items():
                if 'query_guard_' in name:
                    response['answers'][name]['noul'] = .49
                elif name.endswith('qualification_scope'):
                    response['answers'][name].update(choice='source',probabilities={k:float(k=='source') for k in q['criteria']})
                elif q['type']=='choice' and ('query_block_' in name or name.endswith('query_level')):
                    response['answers'][name].update(choice='omit',probabilities={k:float(k=='omit') for k in q['criteria']})
            return response
        provider = self.provider(transport)
        reps,_ = representations.build(self.store,provider,identity,[source],[source],self.cfg['epoch'])
        reps,plan,_ = query_context.build(self.store,provider,identity,request,{'read_revision':1},[source],[source],reps,self.cfg['epoch'])
        self.assertEqual(reps[0]['query']['text'],source['text'])
        self.assertEqual(plan['sources'][0]['decision'],'unresolved')
        self.assertTrue(plan['uncertainties'])


class NativeApiTests(unittest.TestCase):
    from test_query_context import QueryContextTests as _Fixture
    setUp = _Fixture.setUp
    tearDown = _Fixture.tearDown
    capture = _Fixture.capture
    provider = _Fixture.provider
    recover = _Fixture.recover
    focus = staticmethod(_Fixture.focus)

    def transport(self, body, key):
        result = self._Fixture.transport(self, body, key)
        payload = json.loads(body)
        for name,q in payload['questions'].items():
            item = payload['state']['items'][int(name.split('_')[0][1:])]
            old = 'OLD_UNRELATED' in str(item)
            field = name.split('_',1)[1]
            if field == 'needed':
                value = 'needed'
            elif field == 'delivery':
                value = 'continuation'
            elif field == 'applicability' and old:
                value = 'unrelated'
            elif field.startswith('query_block_'):
                value = 'omit' if old else 'core'
            elif field == 'query_level':
                value = 'omit' if old else 'brief'
            else:
                value = None
            if value is not None:
                result['answers'][name].update(choice=value,probabilities={k:float(k==value) for k in q['criteria']})
            if field.startswith('query_guard_'):
                result['answers'][name]['noul'] = 0 if old else 1
        return result

    def native(self):
        from jcm.adapter import register_transcript, recover_sources
        self.capture('history','goal','Continue pause recovery for /artifacts/current-A. No deployment approval. Preserve protocol 47.')
        path = self.logs / 'native.jsonl'
        items = [
            {'type':'CommandExecution','id':'current','command':['export'],'status':'completed','exit_code':0,
             'aggregated_output':'BLOCKER_47: /artifacts/current-A export failed; skeleton missing. Do not deploy.'},
            {'type':'CommandExecution','id':'old','command':['export'],'status':'completed','exit_code':0,
             'aggregated_output':'OLD_UNRELATED: resolved failure for /artifacts/retired-A on another project.'},
            {'type':'McpToolCall','id':'partial','server':'test','tool':'validate','status':'completed',
             'result':{'content':[{'type':'text','text':json.dumps({'artifact':'/artifacts/current-A', 'partial':True,
                'nested':{'ready':True,'qualification':{'hardware_verified':False}},
                'late_exception':'LATE_58: keep the interlock enabled.'})}]}},
            {'type':'McpToolCall','id':'error','server':'test','tool':'sign','status':'completed',
             'result':{'isError':True,'content':[{'type':'text','text':'SIGNATURE_62: /artifacts/current-A signing failed.'}]}},
            {'type':'McpToolCall','id':'pending','server':'test','tool':'hardware','status':'completed',
             'result':{'status':'pending','content':[{'type':'text','text':'PENDING_93: /artifacts/current-A hardware test has no result.'}]}},
            {'type':'AgentMessage','id':'report','phase':'final_answer',
             'content':[{'type':'text','text':'/artifacts/current-A is ready. This report has not been reconciled with export failure.'}]},
        ]
        records = [{'type':'session_meta','payload':{'id':'history','cwd':str(self.root)}}]
        records += [{'type':'event_msg','payload':{'type':'item_completed','turn_id':'goal','item':item}} for item in items]
        path.write_text('\n'.join(json.dumps(r) for r in records)+'\n')
        register_transcript(self.store,path,'history')
        result = recover_sources(self.store)
        self.assertEqual(result,6)

    def read_all(self, pack, handle=None):
        from jcm.coordinator import read_pack
        from jcm.delivery import reconstruct_pages
        pages = []; page = 1
        while True:
            response = read_pack(self.store,pack['pack_id'],page,retained_context=handle)
            pages.append(response)
            if not response.get('next_read_command'):
                break
            page += 1
        self.assertTrue(pages[-1]['required_context_complete'])
        return reconstruct_pages(pages), pages[-1]['context_handle'], pages

    def test_native_adapter_to_public_read_preserves_blockers_and_limits(self):
        self.native()
        pack = self.recover('consumer','Continue pause recovery for /artifacts/current-A.')
        brief,_,_ = self.read_all(pack)
        self.assertEqual(brief['delivery_contract']['status'],'validated')
        records = str(brief['selected_records'])
        for fact in ('BLOCKER_47','SIGNATURE_62','PENDING_93','LATE_58','hardware_verified','protocol 47'):
            self.assertIn(fact,records)
        self.assertNotIn('OLD_UNRELATED',records)
        self.assertFalse(representations.validate_contract(pack['query_context'],brief))
        for record in brief['selected_records']:
            self.assertFalse({**brief.get('record_defaults',{}),**record}['verification_currently_applicable'])

    def test_downstream_missing_span_state_and_defaults_repair_or_block(self):
        self.native()
        original = representations.compact_brief
        def drop(brief,frame):
            value = original(brief,frame)
            value['selected_records'] = [r for r in value['selected_records'] if 'BLOCKER_47' not in r['text']]
            return value
        with patch('jcm.representations.compact_brief',side_effect=drop):
            pack = self.recover('consumer','Continue pause recovery for /artifacts/current-A.')
        brief,_,_ = self.read_all(pack)
        self.assertIn('BLOCKER_47',str(brief['selected_records']))
        self.assertEqual((pack['quality'],brief['delivery_contract']['status']),('degraded','exact_span_fallback'))
        from jcm.coordinator import read_pack
        full = read_pack(self.store,pack['pack_id'],view='full')
        self.assertEqual(full.get('quality',full.get('pack',{}).get('quality')),'degraded')
        corrupted = copy.deepcopy(brief)
        corrupted['selected_records'][0]['verification_currently_applicable'] = True
        self.assertTrue(representations.validate_contract(pack['query_context'],corrupted))
        self.store.change_policy(enabled=False)
        blocked,errors = representations.enforce_contract(self.store,pack['query_context'],corrupted,self.cfg['epoch'])
        self.assertEqual(blocked['dispatch'],'blocked')
        self.assertIn('PROJECT_DISABLED',errors)

    def test_pages_chained_deltas_restore_exact_state_order_and_fresh_read_progress(self):
        from jcm.delivery import apply_delta
        from jcm.coordinator import read_pack
        self.native()
        self.store.config['pack_byte_ceiling'] = 5200
        base = self.recover('consumer','Continue pause recovery for /artifacts/current-A.')
        full,handle,pages = self.read_all(base)
        self.assertGreater(len(pages),1)
        reread = read_pack(self.store,base['pack_id'],1)
        self.assertFalse(reread['required_context_complete'])
        # Complete the new pass before using the explicitly retained base.
        full,handle,_ = self.read_all(base)
        for request in ('Correction: use delay 8 seconds. Preserve protocol 47. Continue pause recovery for /artifacts/current-A.',
                        'Continue pause recovery for /artifacts/current-A.'):
            pack = self.recover('consumer',request)
            change,new_handle,_ = self.read_all(pack,handle)
            full = apply_delta(full,change)
            expected,_,_ = self.read_all(pack)
            self.assertEqual(full,expected)
            handle = new_handle

    def test_provider_failure_keeps_affected_body_and_reports_degradation(self):
        from jcm.util import JCMError
        self.native()
        normal = self.transport
        def failure(body,key):
            if any('_query_level' in name for name in json.loads(body)['questions']):
                raise JCMError('PROVIDER_OFFLINE')
            return normal(body,key)
        self.transport = failure
        pack = self.recover('consumer','Continue pause recovery for /artifacts/current-A.')
        brief,_,_ = self.read_all(pack)
        self.assertEqual(pack['quality'],'degraded')
        self.assertIn('PROVIDER_OFFLINE',str(brief['coverage']))
        self.assertIn('BLOCKER_47',str(brief['selected_records']))

    def test_forgotten_or_changed_source_cannot_be_restored_by_fallback(self):
        from jcm.util import JCMError
        self.native()
        pack = self.recover('consumer','Continue pause recovery for /artifacts/current-A.')
        brief,_,_ = self.read_all(pack)
        corrupted = copy.deepcopy(brief); corrupted['selected_records'] = []
        source = pack['query_context']['sources'][0]
        self.store.db.execute('UPDATE events SET revision=revision+1 WHERE id=?',(source['event_id'],))
        blocked,errors = representations.enforce_contract(self.store,pack['query_context'],corrupted,self.cfg['epoch'])
        self.assertEqual(blocked['dispatch'],'blocked')
        self.assertTrue(errors)
        self.store.forget_session('history')
        blocked,errors = representations.enforce_contract(self.store,pack['query_context'],corrupted,self.cfg['epoch'])
        self.assertEqual(blocked['dispatch'],'blocked')
        self.assertEqual(blocked['selected_records'],[])
        from jcm.coordinator import read_pack
        with self.assertRaises(JCMError):
            read_pack(self.store,pack['pack_id'])

    def test_relation_endpoints_and_partial_correction_survive_state_loss_injection(self):
        self.native()
        self.capture('history','old-rule','Use delay 5 seconds. Preserve protocol 47 and keep the interlock enabled.')
        self.recover('consumer','Continue pause recovery for /artifacts/current-A.')
        original = representations.compact_brief
        def drop_state(brief,frame):
            value = original(brief,frame)
            value['task_frame']['assertions'] = []
            value['task_frame']['relations'] = []
            return value
        with patch('jcm.representations.compact_brief',side_effect=drop_state):
            pack = self.recover('consumer','Correction: use delay 8 seconds. Keep all other requirements. Continue pause recovery for /artifacts/current-A.')
        brief,_,_ = self.read_all(pack)
        self.assertEqual(brief['delivery_contract']['status'],'exact_span_fallback')
        self.assertTrue(brief['task_frame']['relations'])
        self.assertIn('interlock enabled',str(brief['selected_records']))
        self.assertIn('8 seconds',str(brief['selected_records']))
        ids = {a['id'] for a in brief['task_frame']['assertions']}
        for relation in brief['task_frame']['relations']:
            self.assertTrue({relation['older'],relation['newer']} <= ids)
        self.assertFalse(representations.validate_contract(pack['query_context'],brief))
