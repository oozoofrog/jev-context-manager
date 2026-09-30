"""Offline instrumentation gates for fair before/after consumer comparisons."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

import test_continuity as fixtures
import test_query_context as query_fixtures
from jcm.util import JCMError
from jcm.provider import JevProvider


ROOT=Path(__file__).resolve().parents[1]


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


prep=load('prepare_delivery',ROOT/'scripts/prepare_delivery_comparison.py')
host=load('host_delivery_binding',ROOT/'scripts/compare_host_delivery.py')


class ComparisonTests(unittest.TestCase):
    setUp=fixtures.ContinuityTests.setUp
    tearDown=fixtures.ContinuityTests.tearDown

    def fixture(self):
        question='Continue Cedar /builds/cedar-A. Return artifact as the exact current artifact path.'
        return {'version':1,'id':'cedar-development','initial_items':[
            {'type':'UserMessage','id':'goal','content':[{'type':'text','text':'Build Cedar /builds/cedar-A. Keep its interlock enabled. No publication approval.'}]},
            {'type':'CommandExecution','id':'blocker','command':['export','/builds/cedar-A'],'status':'completed','exit_code':0,
             'aggregated_output':'CEDAR_MESH_27: /builds/cedar-A lacks the mesh. Do not publish.'}],
            'steps':[{'name':'cold','request':question,'expected':{'artifact':'/builds/cedar-A'}},
                     {'name':'warm','request':question,'reuse_request_from':'cold','use_delta':True,'expected':{'artifact':'/builds/cedar-A'}}]}

    def prepare(self,fixture=None,provider=None):
        fixture=fixture or self.fixture();prep.validate_fixture(fixture)
        rows=[{'type':'session_meta','payload':{'id':fixture['id']+'-history-0','cwd':str(self.root)}}]
        rows += [{'type':'event_msg','payload':{'type':'item_completed','turn_id':'goal','item':item}} for item in fixture['initial_items']]
        path=self.logs/'native.jsonl';path.write_bytes(b'\n'.join(prep.encode(r) for r in rows)+b'\n')
        return prep.prepare_lane(fixture,self.base/'prepared',self.home/'profiles'/(self.cfg['repo_id']+'.json'),{0:str(path)},
            provider or (lambda store:JevProvider(store,fixtures.fake_http)))

    def case(self,result):
        return {'comparison_contract_version':1,'model_settings':{'model':'gpt-6.1-sol','model_reasoning_effort':'xhigh','service_tier':'default'},'steps':[
            {k:step[k] for k in ('name','prompt','expected','request_hashes')} |
            {lane+'_pages':step['pages'] for lane in ('baseline','candidate')} |
            {'source_bindings':{lane:step['source_binding'] for lane in ('baseline','candidate')},
             'page_hashes':{lane:step['page_hashes'] for lane in ('baseline','candidate')}} for step in result['steps']]}

    def test_same_snapshot_warm_and_delta_bindings_survive_later_lane_events(self):
        result=self.prepare()
        self.assertTrue(result['pass'])
        a,b=result['steps']
        self.assertEqual(a['source_snapshot_hash'],b['source_snapshot_hash'])
        self.assertEqual(a['request_token'],b['request_token'])
        self.assertEqual(a['request_hashes']['planner'],a['request_hashes']['consumer'])
        self.assertTrue(b['checks']['delta_equal_brief'])
        self.assertEqual(b['delivery'],'delta')
        self.assertIsNone(b['full_delivery_reason'])
        self.assertIsNone(b['full_transition'])
        from jcm.delivery import reconstruct_pages
        payload=reconstruct_pages([json.loads(Path(p).read_text()) for p in b['pages']])
        self.assertEqual(payload['view'],'brief')
        self.assertEqual(payload['delivery_mode'],b['delivery'])
        self.assertEqual(b['preparation_metrics']['source_units_evaluated'],0)
        self.assertGreater(b['preparation_metrics']['source_units_reused'],0)
        # Stage source bindings are independent of a journal that continues.
        self.store.capture(session='later',turn='new',kind='user_message',role='user',payload={'text':'Later private input'},
            snapshot={},source_key='later',identity='later')
        # Binding verification must not mutate the source journal/registry.
        journals=[p for step in result['steps'] for p in Path(json.loads(Path(step['source_binding']['profile']).read_text())['home']).rglob('*.sqlite')]
        before={str(p):host.file_hash(p) for p in journals}
        case=self.case(result);host.validate_case(case)
        self.assertEqual({str(p):host.file_hash(p) for p in journals},before)
        invalid=copy.deepcopy(case);invalid['steps'][0]['source_bindings']['baseline']['profile_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'profile changed'):host.validate_case(invalid)
        self.assertEqual({str(p):host.file_hash(p) for p in journals},before)

    def test_canonical_request_and_frozen_pages_cannot_drift(self):
        result=self.prepare();case=self.case(result)
        wrong=copy.deepcopy(case);wrong['steps'][0]['prompt']+=' Also answer a new question.'
        with self.assertRaisesRegex(ValueError,'semantic request mismatch'):host.validate_case(wrong)
        page=Path(case['steps'][0]['baseline_pages'][0]);page.write_text('{}')
        with self.assertRaisesRegex(ValueError,'API page changed'):host.validate_case(case)

    def test_optional_reads_use_current_stage_profile_and_reject_source_drift(self):
        result=self.prepare();binding=result['steps'][0]['source_binding']
        work=self.base/'reader';work.mkdir();(work/'read.py').write_text(host.READER)
        prep.write(work/'source-binding.json',binding)
        run=lambda:subprocess.run([sys.executable,str(work/'read.py'),'lookup','CEDAR_MESH_27'],text=True,capture_output=True)
        response=run();self.assertEqual(response.returncode,0,response.stderr)
        match=json.loads(response.stdout)['matches'][0]
        self.assertEqual(match['expand_command'],'python3 read.py source '+match['event_id']+' 1')
        from jcm.store import Store
        frozen=Store(json.loads(Path(binding['profile']).read_text()))
        frozen.db.execute('UPDATE events SET revision=revision+1 WHERE id=?',(match['event_id'],));frozen.close()
        rejected=run();self.assertNotEqual(rejected.returncode,0)
        self.assertIn('SEMANTIC_DEPENDENCY_CHANGED',rejected.stderr)

    def test_harness_stops_sequence_on_payment_required_and_preserves_failure(self):
        sent=[]
        def unavailable(body,key):
            sent.append(body);raise JCMError('PROVIDER_HTTP_402')
        result=self.prepare(provider=lambda store:JevProvider(store,unavailable))
        self.assertFalse(result['pass']);self.assertEqual(len(result['steps']),1)
        self.assertEqual(len(sent),1)
        self.assertFalse(result['steps'][0]['checks']['provider_available'])
        self.assertTrue((self.base/'prepared/result.json').exists())

    def test_false_warm_or_postplanning_answer_requirements_are_rejected(self):
        fixture=self.fixture();fixture['steps'][1]['request']+=' New focus.'
        with self.assertRaisesRegex(ValueError,'identical earlier request'):prep.validate_fixture(fixture)
        fixture=self.fixture();fixture['steps'][0]['consumer_prompt']='Additional question.'
        with self.assertRaisesRegex(ValueError,'before planning'):prep.validate_fixture(fixture)
        fixture=self.fixture()
        fixture['steps'].insert(1,{'name':'fresh','request':fixture['steps'][0]['request'],'expected':{'artifact':'/builds/cedar-A'}})
        with self.assertRaisesRegex(ValueError,'identical earlier request'):prep.validate_fixture(fixture)

    def test_distinct_a_b_a_uses_full_transitions_and_resets_the_delta_base(self):
        fixture=self.fixture()
        fixture['initial_items'].append({'type':'UserMessage','id':'goal-b',
            'content':[{'type':'text','text':'Build Birch /archive/birch-B. Keep its independent constraints.'}]})
        question_a=fixture['steps'][0]['request']
        question_b='Continue Birch /archive/birch-B. Return artifact as the exact current artifact path.'
        fixture['steps'] += [
            {'name':'switch_b','request':question_b,'use_delta':True,'expected':{'artifact':'/archive/birch-B'}},
            {'name':'warm_b','request':question_b,'reuse_request_from':'switch_b','use_delta':True,'expected':{'artifact':'/archive/birch-B'}},
            {'name':'return_a','request':question_a,'use_delta':True,'expected':{'artifact':'/builds/cedar-A'}},
            {'name':'warm_a','request':question_a,'reuse_request_from':'return_a','use_delta':True,'expected':{'artifact':'/builds/cedar-A'}}]
        def scoped(body,key):
            response=fixtures.fake_http(body,key);payload=json.loads(body)
            for name,q in payload['questions'].items():
                item=payload['state']['items'][int(name.split('_')[0][1:])]
                field=name.split('_',1)[1]
                if 'anchor' not in item or field not in ('scope','anchor','same_goal'):
                    continue
                anchor=item['anchor']['text'];request=payload['state']['context']['request']
                same=any(path in anchor and path in request for path in ('/builds/cedar-A','/archive/birch-B'))
                value=('same' if same else 'changed') if field=='scope' else (
                    'yes' if same and anchor.startswith('Build ') else 'no') if field=='anchor' else ('same' if same else 'different')
                response['answers'][name].update(choice=value,probabilities={k:float(k==value) for k in q['criteria']})
            return response
        result=self.prepare(fixture,lambda store:JevProvider(store,scoped))
        self.assertTrue(result['pass'])
        a,aw,b,bw,returned,rw=result['steps']
        self.assertNotEqual(a['task_id'],b['task_id'])
        self.assertEqual(a['task_id'],returned['task_id'])
        from jcm.delivery import reconstruct_pages,apply_delta
        previous=None;handle=None
        for step in result['steps']:
            payload=reconstruct_pages([json.loads(Path(p).read_text()) for p in step['pages']])
            exact=reconstruct_pages([json.loads(p.read_text()) for p in sorted((self.base/'prepared'/step['name']).glob('brief-*.json'))])
            self.assertEqual(step['delivery'],payload.get('delivery_mode','brief'))
            if step['delivery']=='delta':
                self.assertEqual(payload['retained_context'],handle)
                decoded=apply_delta(previous,payload)
                self.assertIsNone(step['full_transition'])
            else:
                decoded=payload
                if step['name']!='cold':
                    self.assertEqual(step['full_transition']['changed_fields'],['task_id'])
                    self.assertEqual(step['full_delivery_reason'],'verified_identity_transition')
                    self.assertTrue(step['checks']['full_transition_equal_brief'])
                    self.assertFalse(list((self.base/'prepared'/step['name']).glob('delta-*.json')))
                    from jcm.store import Store
                    from jcm.coordinator import read_pack
                    frozen=Store(json.loads(Path(step['source_binding']['profile']).read_text()))
                    try:
                        with self.assertRaisesRegex(JCMError,'RETAINED_CONTEXT_NOT_APPLICABLE'):
                            read_pack(frozen,step['source_binding']['pack_id'],retained_context=handle)
                    finally:frozen.close()
            self.assertEqual(decoded,exact)
            last=json.loads(Path(step['pages'][-1]).read_text())
            handle=last['context_handle'];previous=decoded
        self.assertEqual([s['delivery'] for s in result['steps']],['brief','delta','brief','delta','brief','delta'])
        host.validate_case(self.case(result))


class RetainedTransitionTests(unittest.TestCase):
    setUp=query_fixtures.QueryContextTests.setUp
    tearDown=query_fixtures.QueryContextTests.tearDown
    capture=query_fixtures.QueryContextTests.capture
    provider=query_fixtures.QueryContextTests.provider
    recover=query_fixtures.QueryContextTests.recover
    transport=query_fixtures.QueryContextTests.transport
    focus=staticmethod(query_fixtures.QueryContextTests.focus)
    corpus=query_fixtures.QueryContextTests.corpus

    def contexts(self):
        from jcm.coordinator import read_pack
        from jcm.delivery import reconstruct_pages
        self.corpus();base=self.recover('consumer','Continue pause recovery.')
        pages=[];number=1
        while True:
            page=read_pack(self.store,base['pack_id'],number);pages.append(page)
            if not page.get('next_read_command'):break
            number+=1
        current=self.recover('other-consumer','Continue pause recovery.')
        self.assertEqual(base['task_frame']['task_id'],current['task_frame']['task_id'])
        return base,current,pages[-1]['context_handle'],reconstruct_pages(pages)

    def test_verified_session_transition_keeps_production_delta_rejection(self):
        base,current,handle,brief=self.contexts()
        transition=prep.full_transition(self.store,current,handle,brief)
        self.assertEqual(transition['changed_fields'],['session_id'])
        from jcm.coordinator import read_pack
        with self.assertRaisesRegex(JCMError,'RETAINED_CONTEXT_NOT_APPLICABLE'):
            read_pack(self.store,current['pack_id'],retained_context=handle)

    def test_hash_receipt_and_retained_content_errors_are_not_transitions(self):
        base,current,handle,brief=self.contexts()
        with self.assertRaisesRegex(JCMError,'INVALID_RETAINED_CONTEXT'):
            prep.full_transition(self.store,current,base['pack_id'],brief)
        with self.assertRaisesRegex(JCMError,'RETAINED_CONTEXT_NOT_APPLICABLE'):
            prep.full_transition(self.store,current,base['pack_id']+':'+('0'*64),brief)
        corrupted=copy.deepcopy(brief);corrupted['selected_records']=[]
        with self.assertRaisesRegex(JCMError,'HARNESS_RETAINED_CONTENT_MISMATCH'):
            prep.full_transition(self.store,current,handle,corrupted)
        self.store.db.execute('DELETE FROM meta WHERE key=?',('required_read:'+base['pack_id'],))
        with self.assertRaisesRegex(JCMError,'RETAINED_CONTEXT_NOT_APPLICABLE'):
            prep.full_transition(self.store,current,handle,brief)

    def test_source_revision_errors_are_not_transitions(self):
        _,current,handle,brief=self.contexts()
        self.store.db.execute('UPDATE events SET revision=revision+1 WHERE id=?',(self.rule,))
        with self.assertRaisesRegex(JCMError,'SEMANTIC_DEPENDENCY_CHANGED'):
            prep.full_transition(self.store,current,handle,brief)

    def test_policy_invalidation_is_not_a_transition(self):
        _,current,handle,brief=self.contexts()
        self.store.change_policy(enabled=False)
        with self.assertRaisesRegex(JCMError,'PACK_MISSING_OR_INVALIDATED|PROJECT_DISABLED'):
            prep.full_transition(self.store,current,handle,brief)

    def test_forgotten_source_is_not_a_transition(self):
        _,current,handle,brief=self.contexts()
        self.store.forget_session('history')
        with self.assertRaisesRegex(JCMError,'PACK_MISSING_OR_INVALIDATED|EVENT_NOT_FOUND'):
            prep.full_transition(self.store,current,handle,brief)

    def test_contract_errors_are_not_transitions(self):
        base,current,handle,brief=self.contexts()
        changed=copy.deepcopy(base);changed['context_views']['brief']['selected_records']=[]
        self.store.db.execute('UPDATE packs SET blob=? WHERE id=?',(self.store.put_blob(changed),base['pack_id']))
        with self.assertRaisesRegex(JCMError,'DELIVERY_CONTRACT_VIOLATION'):
            prep.full_transition(self.store,current,handle,brief)
        changed=copy.deepcopy(base);changed['query_context']['contract_version']=0
        self.store.db.execute('UPDATE packs SET blob=? WHERE id=?',(self.store.put_blob(changed),base['pack_id']))
        with self.assertRaisesRegex(JCMError,'RETAINED_CONTEXT_CONTRACT_UNVERIFIED'):
            prep.full_transition(self.store,current,handle,brief)


if __name__=='__main__':unittest.main()
