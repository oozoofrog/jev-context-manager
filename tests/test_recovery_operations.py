import json
import unittest
from unittest.mock import patch

import test_continuity as fixtures
from test_context_reuse import builder
from jcm import detail_plan, local_index
from jcm.coordinator import dispatch
from jcm.semantic_cache import evaluate_items, source_items
from jcm.util import digest


class RecoveryOperationsTests(unittest.TestCase):
    setUp = fixtures.ContinuityTests.setUp
    tearDown = fixtures.ContinuityTests.tearDown
    capture = fixtures.ContinuityTests.capture
    provider = fixtures.ContinuityTests.provider
    request = fixtures.ContinuityTests.request

    def test_completed_judgments_survive_runtime_epoch_but_not_question_or_model_changes(self):
        event = self.capture('prior', 'one', 'Keep user pause state.')
        context = {'goal': 'pause'}
        items = source_items(self.store.material(self.store.event(event)), context, builder)
        first = evaluate_items(self.store, self.provider(), 'fixture', context, items, builder, self.cfg['epoch'])
        policy = self.store.change_policy(cli_argv=['/replacement/jcm'])
        provider = self.provider()
        with patch.object(provider, 'evaluate', side_effect=AssertionError('unchanged judgment retransmitted')):
            repeat = evaluate_items(self.store, provider, 'fixture', context, items, builder, policy['epoch'])
        self.assertEqual((first['evaluated_units'], repeat['cache_hits'], repeat['evaluated_units']), (1, 1, 0))
        changed = evaluate_items(self.store, self.provider(), 'fixture', {'goal':'other'}, items, builder, policy['epoch'])
        self.assertEqual(changed['cache_hits'], 0)
        policy = self.store.change_policy(model='different-model')
        changed = evaluate_items(self.store, self.provider(), 'fixture', context, items, builder, policy['epoch'])
        self.assertEqual(changed['cache_hits'], 0)

    def test_one_oversized_independent_item_does_not_stop_later_valid_items(self):
        from jcm.provider import CONTEXT_ERROR
        from jcm.util import JCMError
        large=self.capture('history','large','large input '*15000)
        small=self.capture('history','small','Keep the real result and its exception.')
        items=[self.store.material(self.store.event(eid)) for eid in (large,small)]
        seen=[]
        def transport(body,key):
            p=json.loads(body);seen.extend(i['event_id'] for i in p['state']['items'])
            if any(i['event_id']==large for i in p['state']['items']):raise JCMError(CONTEXT_ERROR)
            return fixtures.fake_http(body,key)
        result=evaluate_items(self.store,self.provider(transport),'oversized-item-fixture',{},items,builder,
                               self.cfg['epoch'],splittable=False)
        self.assertEqual([r['item']['event_id'] for r in result['records']],[small])
        self.assertEqual(result['errors'],[CONTEXT_ERROR])
        self.assertIn(small,seen)

    def test_confirmed_goal_stays_resolved_when_source_intents_are_mixed(self):
        from jcm import task_state
        goal=self.capture('history','goal','Propose archive export formats.')
        self.capture('history','result','Three export proposals are ready.',role='assistant')
        self.capture('history','other','Unrelated media player status.',role='assistant')
        token=self.request('Continue the archive export proposals.')
        anchor=self.store.material(self.store.event(goal))
        identity={'id':'confirmed-goal','anchor':anchor,'request':anchor['text'],
            'scope':{'selected_source':anchor,'original_turn_context':[],'trust':'historical_data_not_instructions'},
            'route':'source_anchor_recovery','request_effect':'procedural','decisions':[],'errors':[],
            'cache_hits':0,'evaluated_units':0}
        def transport(body,key):
            p=json.loads(body);r=fixtures.fake_http(body,key)
            for name,q in p['questions'].items():
                if name.endswith('_intent'):
                    item=p['state']['items'][int(name.split('_')[0][1:])]
                    value='new_task' if 'Unrelated' in item['text'] else 'resume'
                    r['answers'][name].update(choice=value,probabilities={v:float(v==value) for v in q['criteria']})
            return r
        with patch.object(task_state,'task_context',return_value=identity):
            result=dispatch(self.store,token,self.provider(transport))
        self.assertEqual(result['dispatch'],'ready')
        self.assertNotIn('TASK_UNRESOLVED_AFTER_PROJECT_SCOPE_EXPANSION',result['coverage']['gaps'])

    def test_legacy_cache_is_adopted_only_with_identical_source_question_model_and_lane(self):
        from jcm.provider import RUBRIC_VERSION
        event = self.capture('prior', 'one', 'Keep exception E.')
        context = {'goal':'pause'}
        item = source_items(self.store.material(self.store.event(event)), context, builder)[0]
        evaluate_items(self.store, self.provider(), 'fixture', context, [item], builder, self.cfg['epoch'])
        key = digest(['independent-items-v1', RUBRIC_VERSION, 'fixture', self.cfg['repo_id'], self.cfg['epoch'],
                      self.cfg['model'], 'mock', context, item, builder('`state.items[0]`', item)])
        self.store.db.execute('UPDATE semantic_items SET key=?', (key,))
        policy = self.store.change_policy()
        provider = self.provider()
        with patch.object(provider, 'evaluate', side_effect=AssertionError('legacy judgment retransmitted')):
            repeat = evaluate_items(self.store, provider, 'fixture', context, [item], builder, policy['epoch'])
        self.assertEqual(repeat['cache_hits'], 1)

    def test_index_batches_policy_work_and_preserves_all_sources(self):
        ids = [self.capture('prior', str(i), f'필수 조건 {i}') for i in range(260)]
        materials = [self.store.material(self.store.event(i)) for i in ids]
        with patch.object(self.store, 'policy', wraps=self.store.policy) as checks:
            self.assertEqual(local_index.refresh(self.store, materials, self.cfg['epoch']), 260)
            self.assertLess(checks.call_count, 10)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM source_index').fetchone()[0], 260)
        self.assertEqual(set(local_index.search(self.store, '필수')), set(ids))

    def test_interrupt_records_terminal_status_and_preserves_successful_cache(self):
        self.capture('prior', 'one', 'First source')
        self.capture('prior', 'two', 'Second source')
        token = self.request()
        count = 0
        def transport(body, key):
            nonlocal count
            count += 1
            if count == 2:
                raise KeyboardInterrupt()
            return fixtures.fake_http(body, key)
        with self.assertRaises(KeyboardInterrupt):
            dispatch(self.store, token, self.provider(transport))
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM decisions WHERE status='pending'").fetchone()[0], 0)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM calls WHERE status='reserved'").fetchone()[0], 0)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM calls WHERE status='interrupted'").fetchone()[0], 1)
        self.assertGreater(self.store.db.execute('SELECT count(*) FROM semantic_items').fetchone()[0], 0)
        meta = json.loads(self.store.db.execute("SELECT value FROM meta WHERE key LIKE 'bootstrap_new:%'").fetchone()[0])
        self.assertEqual((meta['stage'],meta['error']), ('interrupted','RECOVERY_INTERRUPTED'))
        self.assertTrue(meta['provider_called'])

    def test_tool_detail_gate_never_defers_unknown_failed_user_or_established_evidence(self):
        text = 'Optional trace ' * 1000
        ids=[]
        for turn,role,status,code in [('ok','tool','completed',0),('bad','tool','failed',1),('user','user','completed',0)]:
            ids.append(self.store.capture(session='prior',turn=turn,kind='tool_result' if role=='tool' else 'user_message',role=role,
                payload={'text':text,'public_item':{'type':'CommandExecution','command':['echo','hello'],'status':status,'exit_code':code}},
                snapshot={},source_key='detail:'+turn,identity=['detail',turn]))
        unknown=self.capture('prior','unknown',text,role='tool');ids.append(unknown)
        events=[self.store.event(i) for i in ids]
        identity={'id':'fixture','scope':{'selected_source':{'text':'Plan a design'}}}
        provider=self.provider()
        kept,deferred,result=detail_plan.plan(self.store,provider,events,identity,'Plan a design',self.cfg['epoch'])
        self.assertEqual({d['event_id'] for d in deferred},{ids[0]})
        self.assertEqual({e['id'] for e in kept},set(ids[1:]))
        self.assertEqual(deferred[0]['assessment'],'metadata_only_not_source_review')
        kept,_,_=detail_plan.plan(self.store,provider,events,identity,'Plan a design',self.cfg['epoch'],[ids[0]])
        self.assertEqual({e['id'] for e in kept},set(ids))

    def test_completed_mcp_error_and_stderr_are_not_optional_success(self):
        item = {'type':'McpToolCall', 'tool':'read', 'status':'completed',
                'result':{'jcm_text_field':'result'}}
        for result in ({'isError':True,'content':[]}, None, {'error':'failed'}):
            self.assertIsNone(detail_plan.descriptor({'public_item':item,'text':json.dumps({'result':result})}))
        self.assertIsNotNone(detail_plan.descriptor({'public_item':item,'text':json.dumps({'result':{'content':[]}})}))
        self.assertIsNone(detail_plan.descriptor({'public_item':{'type':'CommandExecution',
            'status':'completed','exit_code':0,'command':['build'],'stderr':'Build skipped: unsupported target'}}))

    def test_nested_partial_pending_failed_and_unknown_wrappers_force_expansion(self):
        statuses=[{'session_id':123,'status':'running'},
                  {'chunk_id':'sample','wall_time_seconds':1.0,'session_id':123,'output':'building'},
                  {'chunk_id':'sample','session_id':123,'exit_code':None,'output':'building'},
                  {'isError':True,'error':'failed'},
                  {'coverage':'partial','recovery_success':'not_attested'}, {'truncated':True},
                  {'status':'failed','failed_tests':3}]
        for status in statuses:
            with self.subTest(status=status):
                result={'isError':False,'content':[{'type':'text','text':json.dumps(status)}]}
                self.assertIsNone(detail_plan.descriptor({'public_item':{'type':'McpToolCall',
                    'status':'completed','tool':'fixture','result':result}}))
                self.assertIsNone(detail_plan.descriptor({'public_item':{'type':'CommandExecution',
                    'status':'completed','exit_code':0,'command':['fixture'], 'aggregated_output':json.dumps(status)}}))
        self.assertIsNone(detail_plan.descriptor({'public_item':{'type':'McpToolCall','status':'completed',
            'tool':'unknown','result':{}}}))
        # These words in ordinary prose are not structured status fields.
        self.assertIsNotNone(detail_plan.descriptor({'public_item':{'type':'CommandExecution',
            'status':'completed','exit_code':0,'command':['cat','guide.md'],
            'aggregated_output':'The guide explains pending and failed job states.'}}))

    def test_progress_is_stderr_only_and_forget_does_not_resurrect_status(self):
        import contextlib
        import io
        from jcm.progress import Recovery
        token=self.request()
        self.store.progress_output=True
        stdout,stderr=io.StringIO(),io.StringIO()
        with contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
            with Recovery(self.store,token) as r:
                r.update(force=True,phase='fixture',provider_calls=2)
                self.store.forget_session(self.store.resolve_request(token)['session'])
                r.update(force=True,stage='complete')
        self.assertEqual(stdout.getvalue(),'')
        self.assertIn('"phase": "fixture"',stderr.getvalue())
        self.assertNotIn(token,stderr.getvalue())
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM meta WHERE key LIKE 'bootstrap_new:%'").fetchone()[0],0)

    def test_relationship_expansion_keeps_distant_target_and_uncertainty_without_cartesian_pairs(self):
        from jcm.relation_candidates import candidates
        assertions=[];ordered={}
        for i in range(65):
            text='Correction: delay is now 8.' if i==64 else 'Delay remains 5.' if i==0 else f'Unchanged unrelated property {i}.'
            eid=self.capture('prior',str(i),text)
            ordered[eid]=i
            assertions.append({'id':str(i),'event_id':eid,'revision':1,'span':{'start':0,'end':len(text)},
                'text':text,'surrounding_text':text,'role':'user','basis':'source_observed',
                'categories':['requirement','correction']})
        def transport(body,key):
            payload=json.loads(body);response=fixtures.fake_http(body,key)
            for name,question in payload['questions'].items():
                item=payload['state']['items'][int(name.split('_')[0][1:])]
                if name.endswith('_change_trigger'):
                    selected='possible' if item['id']=='64' else 'ordinary'
                else:
                    selected='possible' if any(a['text'].startswith('Delay remains') for a in payload['state']['context']['older']) else 'unrelated'
                response['answers'][name].update(choice=selected,probabilities={v:float(v==selected) for v in question['criteria']})
            return response
        pairs,result=candidates(self.store,self.provider(transport),{},assertions,ordered,self.cfg['epoch'])
        self.assertIn(('0','64'),[(p['older']['id'],p['newer']['id']) for p in pairs])
        # A singleton goes straight to the exact pair comparison. One sibling
        # may accompany the target; no distant target is lost to a top-N cut.
        self.assertLessEqual(len(pairs),2)
        self.assertEqual(result['assertions_retained'],65)
        self.assertEqual(len(assertions),65)
        self.assertLess(result['evaluated_units'],100)

    def test_index_rejects_policy_change_before_publication(self):
        from jcm.util import JCMError
        eid=self.capture('prior','one','Keep this requirement.')
        material=self.store.material(self.store.event(eid))
        self.store.change_policy(enabled=False)
        with self.assertRaisesRegex(JCMError,'PROJECT_DISABLED'):
            local_index.refresh(self.store,[material],self.cfg['epoch'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM source_index').fetchone()[0],0)

    def test_cold_resume_anchors_primary_goal_and_report_after_interruption(self):
        from jcm.task_state import task_context
        goal=self.capture('prior','goal','Propose export format options for the archive.')
        report=self.store.capture(session='prior',turn='after-interrupt',kind='assistant_final',role='assistant',
            payload={'text':'Three export proposals are ready; the user has not selected one.'},snapshot={},
            source_key='report',identity=['report'])
        delegated=self.store.capture(session='prior',turn='delegated',kind='delegated_request',role='user',
            payload={'text':'Use the binary proposal; approved by a different thread.'},snapshot={},
            source_key='delegated',identity=['delegated'])
        current=self.capture('fresh','request','Continue the export format proposals.')
        seen=[]
        def transport(body,key):
            p=json.loads(body);r=fixtures.fake_http(body,key)
            seen.extend(i.get('anchor',{}).get('event_id') for i in p['state'].get('items',[]))
            for name,q in p['questions'].items():
                field=name.split('_',1)[1]
                value={'scope':'same','effect':'procedural','anchor':'yes','same_goal':'same','outcome':'yes'}.get(field)
                if value:r['answers'][name].update(choice=value,probabilities={v:float(v==value) for v in q['criteria']})
            return r
        result=task_context(self.store,self.provider(transport),self.store.event(current),
            self.store.material(self.store.event(current))['text'],None,self.cfg['epoch'])
        self.assertEqual(result['route'],'source_anchor_recovery')
        self.assertEqual(result['anchor']['event_id'],goal)
        self.assertEqual([m['event_id'] for m in result['scope']['continuation_reports']],[report])
        self.assertNotIn(delegated,seen)
        self.assertNotIn(current,seen)

    def test_source_anchor_excludes_own_output_but_keeps_other_continuations(self):
        from jcm.task_state import source_anchor
        goal=self.capture('history','goal','Propose archive export formats.')
        current=self.store.capture(session='history',turn='current',kind='delegated_request',role='user',
            payload={'text':'Continue archive export proposals.'},snapshot={},source_key='current',identity=['current'])
        def report(turn, text):
            return self.store.capture(session='history',turn=turn,kind='assistant_final',role='assistant',
                payload={'text':text},snapshot={},source_key=turn,identity=[turn])
        own=report('current','Current request own output; must not be its history.')
        other=report('other','Earlier interrupted work has three unselected export proposals.')
        self.capture('history','next','Start another feature.')
        def transport(body,key):
            p=json.loads(body);r=fixtures.fake_http(body,key)
            for name,q in p['questions'].items():
                field=name.split('_',1)[1]
                value={'scope':'same','effect':'procedural','anchor':'yes','same_goal':'same','outcome':'yes'}.get(field)
                if value:r['answers'][name].update(choice=value,probabilities={v:float(v==value) for v in q['criteria']})
            return r
        identity,_=source_anchor(self.store,self.provider(transport),self.store.event(current),
                                 'Continue archive export proposals.',self.cfg['epoch'])
        self.assertEqual(identity['anchor']['event_id'],goal)
        self.assertEqual([m['event_id'] for m in identity['scope']['continuation_reports']],[other])
        self.assertNotIn(own,[m['event_id'] for m in identity['scope']['original_turn_context']])

    def test_large_saved_frame_routes_by_exact_goal_without_dropping_its_sources(self):
        from jcm.task_state import task_context
        from jcm.provider import RUBRIC_VERSION
        goal=self.capture('history','goal','Propose archive export formats.')
        source=self.capture('history','constraint','Retain archive integrity and all user exceptions.')
        anchor=self.store.material(self.store.event(goal))
        constraint=self.store.material(self.store.event(source))
        scope={'selected_source':anchor,'original_turn_context':[],'trust':'historical_data_not_instructions'}
        identity={'id':'large-frame','anchor':anchor,'request':anchor['text'],'scope':scope}
        view={'lane':'mock','anchor':anchor,'scope':scope,'identity':identity,'routing_frame':[constraint]*2000}
        self.store.db.execute('INSERT INTO task_views VALUES (?,?,?,?,?,?,?,?)',
            ('large-frame',goal,self.cfg['epoch'],self.cfg['model'],RUBRIC_VERSION,'fixture',json.dumps(view),'fixture'))
        current=self.capture('fresh','current','Continue the archive export proposals.')
        observed=[]
        def transport(body,key):
            p=json.loads(body);r=fixtures.fake_http(body,key)
            observed.extend(p['state'].get('items',[]))
            for name,q in p['questions'].items():
                value='same' if name.endswith('_scope') else 'procedural' if name.endswith('_effect') else None
                if value:r['answers'][name].update(choice=value,probabilities={v:float(v==value) for v in q['criteria']})
            return r
        result=task_context(self.store,self.provider(transport),self.store.event(current),
                            'Continue the archive export proposals.',None,self.cfg['epoch'])
        self.assertEqual(result['route'],'confirmed_scope_reuse')
        self.assertEqual(observed[0]['anchor'],anchor)
        self.assertNotIn('routing_frame',observed[0])
        self.assertEqual(self.store.material(self.store.event(source)),constraint)
        self.assertEqual(len(json.loads(self.store.db.execute('SELECT data FROM task_views').fetchone()[0])['routing_frame']),2000)


import test_context_reuse as reuse_fixtures


class RuntimeReuseTests(unittest.TestCase):
    tearDown=reuse_fixtures.TaskReuseTests.tearDown
    capture=reuse_fixtures.TaskReuseTests.capture
    provider=reuse_fixtures.TaskReuseTests.provider
    recover=reuse_fixtures.TaskReuseTests.recover
    transport=reuse_fixtures.TaskReuseTests.transport

    def setUp(self):
        fixtures.ContinuityTests.setUp(self)
        self.sent=[]

    def test_runtime_update_reuses_task_and_query_but_preserves_confirmed_legacy_relation(self):
        from jcm.task_state import confirm,VERSION
        from jcm.provider import RUBRIC_VERSION
        from jcm.source_read import inspect_source
        old=self.capture('history','one','Use delay 5 seconds.')
        new=self.capture('history','two','Correction: use delay 8 seconds.')
        first=self.recover('session-a','Continue pause recovery.')
        rel=first['task_frame']['relations'][0]
        inspect_source(self.store,old);inspect_source(self.store,new)
        confirm(self.store,rel['id'],'confirmed')
        row=self.store.db.execute('SELECT request_blob FROM decisions WHERE id=?',(rel['decisions'][0]['id'],)).fetchone()
        items=self.store.blob(row[0])['payload']['state']['items']
        pair=next(i for i in items if i['older']['id']==rel['older'] and i['newer']['id']==rel['newer'])
        pair['dependencies']=rel['dependencies']
        legacy=digest([VERSION,rel['task_id'],pair,rel['kind'],self.cfg['model'],self.cfg['epoch'],RUBRIC_VERSION])
        saved=json.loads(self.store.db.execute('SELECT data FROM state_relations WHERE id=?',(rel['id'],)).fetchone()[0])
        saved['id']=legacy
        self.store.db.execute('UPDATE state_relations SET id=?,data=? WHERE id=?',(legacy,json.dumps(saved),rel['id']))
        policy=self.store.change_policy(cli_argv=['/new/runtime/jcm'])
        from jcm.store import Store
        self.store.close()
        self.store=Store(policy)
        second=self.recover('session-b','Continue pause recovery.')
        self.assertEqual(second['metrics']['task_route'],'confirmed_scope_reuse')
        self.assertEqual(second['metrics']['source_units_evaluated'],0)
        self.assertEqual(second['task_frame']['task_id'],first['task_frame']['task_id'])
        self.assertEqual(second['query_context']['route'],'same_question')
        relation=next(r for r in second['task_frame']['relations'] if r['id']==legacy)
        self.assertEqual(relation['status'],'confirmed')
        self.assertEqual(next(a['state'] for a in second['task_frame']['assertions'] if a['event_id']==old),'superseded')
        self.assertIn('/new/runtime/jcm',relation['review_command'])

    def test_implicit_value_change_without_correction_marker_is_compared_and_disputed(self):
        old=self.capture('history','one','Use coral for the character.')
        new=self.capture('history','two','Use blue for the character.')
        original=self.transport
        def transport(body,key):
            response=original(body,key);payload=json.loads(body)
            for name,question in payload['questions'].items():
                if name.endswith('_relation'):
                    response['answers'][name].update(choice='contradicts',
                        probabilities={v:float(v=='contradicts') for v in question['criteria']})
                if name.endswith('_affects_assertion'):
                    response['answers'][name]['noul']=1
            return response
        self.transport=transport
        pack=self.recover('session-a','Continue character design.')
        self.assertTrue(any(r['from']==old and r['to']==new and r['kind']=='contradicts'
                            for r in pack['task_frame']['relations']))
        self.assertEqual(next(a['state'] for a in pack['task_frame']['assertions'] if a['event_id']==old),'disputed')
