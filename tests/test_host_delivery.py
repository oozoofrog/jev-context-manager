"""Host usage must not count cumulative resume snapshots as new tokens."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('host_delivery', Path(__file__).resolve().parents[1]/'scripts/compare_host_delivery.py')
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


class HostDeliveryMeteringTests(unittest.TestCase):
    def test_served_pages_hidden_in_exec_memory_do_not_attest_model_inputs(self):
        pages = [{'origin':'jcm','pack_id':'pack','entries':[{'path':['text'],'value':f'page {i}'}]} for i in range(3)]
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); (home/'sessions').mkdir()
            path = home/'sessions/rollout-thread.jsonl'
            def output(identity, value):
                return {'type':'response_item','payload':{'id':identity,'type':'function_call_output','output':value}}
            # Child command logs can contain all pages while the outer cell only
            # emits its last result. Only the outer output is a model input.
            rows = [{'type':'event_msg','payload':{'type':'command_execution','aggregated_output':json.dumps(page)}} for page in pages]
            rows.append(output('last', [{'type':'input_text','text':json.dumps({'pages_received':3,'last_response':{'output':json.dumps(pages[-1])}})}]))
            path.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
            result = harness.model_visible_pages(home,'thread',pages,set())
            self.assertFalse(result['complete'])
            self.assertEqual(result['visible_pages'],['3'])
            self.assertEqual(result['missing_pages'],['1','2'])

    def test_complete_reread_recovers_truncation_and_prior_turn_outputs_are_not_reused(self):
        page = {'origin':'jcm','pack':{'text':'exact required source'}}
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); (home/'sessions').mkdir()
            path = home/'sessions/rollout-thread.jsonl'
            def emit(identity, text):
                with path.open('a') as stream:
                    stream.write(json.dumps({'type':'response_item','payload':{'id':identity,
                        'type':'function_call_output','output':[{'type':'input_text','text':text}]}})+'\n')
            emit('truncated', json.dumps({'output':json.dumps(page)})[:-10]+' [truncated]')
            seen=set()
            self.assertFalse(harness.model_visible_pages(home,'thread',[page],seen)['complete'])
            emit('reread', 'Script completed\nOutput:\n'+json.dumps({'output':json.dumps(page)}))
            self.assertTrue(harness.model_visible_pages(home,'thread',[page],seen)['complete'])
            self.assertFalse(harness.model_visible_pages(home,'thread',[page],seen)['complete'])

    def test_response_usage_is_unique_and_incremental_across_resumed_turns(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory); (home/'sessions').mkdir()
            path = home/'sessions/rollout-thread.jsonl'
            def response(identity, input_tokens, cached):
                return {'type':'token_usage_record','payload':{'response_id':identity,
                    'usage':{'input_tokens':input_tokens,'cached_input_tokens':cached,'output_tokens':10}}}
            first = response('one',100,40)
            path.write_text(json.dumps(first)+'\n')
            seen = {}
            self.assertEqual(harness.usage_audit([first],seen,True)['known_subtotal']['input_tokens'],100)
            with path.open('a') as stream:
                for record in (first,{'type':'turn.completed','usage':{'input_tokens':300}},response('two',200,170)):
                    stream.write(json.dumps(record)+'\n')
            records,_=harness.native_records(home,'thread')
            usage = harness.usage_audit(records,seen,True)['known_subtotal']
            self.assertEqual(usage,{'input_tokens':200,'cached_input_tokens':170,'uncached_input_tokens':30,'output_tokens':10})
            self.assertFalse(harness.usage_audit(records,seen,True)['complete'])


def receipt(identity='response', **changes):
    usage={'input_tokens':100,'cached_input_tokens':40,'output_tokens':10};usage.update(changes)
    return {'type':'token_usage_record','payload':{'response_id':identity,'usage':usage}}


def context(work, roots=(), turn='turn'):
    return {'turn_id':turn,'cwd':str(work),'approval_policy':'never','model':'gpt-6.1-sol','effort':'xhigh',
            'sandbox_policy':{'type':'workspace-write','network_access':False,'writable_roots':list(roots)},
            'permission_profile':{'type':'managed','network':'restricted','file_system':{'type':'restricted',
                'entries':[{'access':'write','path':{'type':'path','path':p}} for p in [str(work),*roots]]}}}


def tool_output(call, values):
    return {'type':'response_item','payload':{'type':'custom_tool_call_output','call_id':call,
            'output':[{'type':'input_text','text':'Script completed\nOutput:\n'},
                      *[{'type':'input_text','text':json.dumps(v)} for v in values]]}}


def command_call(call, source='await tools.exec_command(...)'):
    return {'type':'response_item','payload':{'type':'custom_tool_call','call_id':call,'name':'exec','input':source}}


class HostDeliveryGateTests(unittest.TestCase):
    settings={'model':'gpt-6.1-sol','model_reasoning_effort':'xhigh','service_tier':'default'}

    def test_usage_partial_identity_invalid_fields_and_interruption_preserve_known_subtotals(self):
        partial=receipt('partial');partial['payload']['usage'].pop('input_tokens')
        unknown=receipt();unknown['payload'].pop('response_id')
        for bad in (partial,unknown,receipt('bad',input_tokens=True),receipt('bad',output_tokens=-1),
                    receipt('bad',cached_input_tokens=101)):
            with self.subTest(bad=bad):
                audit=harness.usage_audit([receipt('valid'),bad],{},True)
                self.assertFalse(audit['complete']);self.assertGreaterEqual(audit['known_subtotal']['input_tokens'],100)
                self.assertTrue(audit['diagnostics'])
        audit=harness.usage_audit([receipt()],{},False)
        self.assertFalse(audit['complete']);self.assertEqual(audit['known_subtotal']['input_tokens'],100)
        self.assertEqual(harness.usage_audit([],{},True)['known_subtotal'],{})

    def test_duplicate_receipts_count_once_conflicts_do_not_choose_one(self):
        seen={};audit=harness.usage_audit([receipt(),receipt()],seen,True)
        self.assertTrue(audit['complete']);self.assertEqual(audit['known_subtotal']['input_tokens'],100)
        self.assertEqual(audit['exact_duplicate_receipts'],1)
        audit=harness.usage_audit([receipt(input_tokens=200),receipt('other')],seen,True)
        self.assertFalse(audit['complete']);self.assertEqual(audit['known_subtotal']['input_tokens'],100)
        self.assertIn('CONFLICTING_USAGE_RECEIPTS',[d['code'] for d in audit['diagnostics']])
        self.assertFalse(harness.usage_audit([receipt(),receipt(input_tokens=100.0)],{},True)['complete'])
        self.assertTrue(harness.usage_audit([receipt(input_tokens=0,cached_input_tokens=0,output_tokens=0)],{},True)['complete'])

    def test_missing_native_response_receipt_is_explicit_and_cumulative_counts_are_not_added(self):
        rows=[receipt(),{'type':'event_msg','payload':{'type':'token_count','info':{'last_token_usage':{},'total_token_usage':{'input_tokens':900}}}},
              {'type':'event_msg','payload':{'type':'token_count','info':{'last_token_usage':{},'total_token_usage':{'input_tokens':950}}}}]
        audit=harness.usage_audit(rows,{},True)
        self.assertFalse(audit['complete']);self.assertEqual(audit['known_subtotal']['input_tokens'],100)
        self.assertIn('RESPONSE_RECEIPT_COUNT_MISMATCH',[d['code'] for d in audit['diagnostics']])

    def test_initial_resume_share_supported_config_and_effective_roots(self):
        work=Path('/fixture/work');roots=['/fixture/stage-one','/fixture/stage-two']
        a=harness.consumer_argv(work,roots,work/'answer.json',self.settings)
        b=harness.consumer_argv(work,roots,work/'answer.json',self.settings,'thread')
        def configs(argv):return [argv[i+1] for i,v in enumerate(argv) if v=='-c']
        self.assertEqual(configs(a),configs(b));self.assertNotIn('--add-dir',a+b)
        self.assertNotIn('-C',b);self.assertNotIn('danger-full-access',' '.join(a+b))
        good=context(work,roots)
        audit=harness.policy_audit([good],work,roots,self.settings)
        self.assertTrue(audit['complete']);self.assertFalse(audit['native_service_tier_observed'])
        for field,value in [('cwd','/different'),('effort','low'),('service_tier','priority')]:
            bad={**good,field:value};self.assertFalse(harness.policy_audit([bad],work,roots,self.settings)['complete'])
        bad={**good,'sandbox_policy':{**good['sandbox_policy'],'writable_roots':[]}}
        self.assertFalse(harness.policy_audit([bad],work,roots,self.settings)['complete'])
        self.assertFalse(harness.policy_audit([],work,roots,self.settings)['complete'])
        bad={**good,'permission_profile':{**good['permission_profile'],'file_system':{'type':'restricted',
            'entries':[{'access':'write','path':{'type':'special','value':'malformed'}}]}}}
        self.assertFalse(harness.policy_audit([bad],work,roots,self.settings)['complete'])

    def test_native_custom_envelope_multiple_commands_dedup_and_unknown_are_observed(self):
        values=[{'chunk_id':'first','exit_code':1,'output':'failed read'},
                {'chunk_id':'second','exit_code':0,'output':json.dumps({'exit_code':12,'chunk_id':'source-data'})}]
        rows=[command_call('first'),tool_output('first',[values[0]]),command_call('second'),tool_output('second',[values[1]]),
              tool_output('first',[values[0]]),tool_output('second',[values[1]])]
        audit=harness.command_outcomes(rows)
        self.assertTrue(audit['complete']);self.assertEqual(audit['observed_commands'],2)
        self.assertEqual(audit['failed_commands'],1);self.assertEqual(audit['exact_duplicate_results'],2)
        unknown=harness.command_outcomes([command_call('running'),tool_output('running',[{'chunk_id':'pending','session_id':8}])])
        self.assertFalse(unknown['complete']);self.assertIsNone(unknown['failed_commands'])
        ambiguous=harness.command_outcomes([{'type':'response_item','payload':{'type':'custom_tool_call','call_id':'x','name':'exec','input':'await tools.exec_command(...)'}},tool_output('x',[])])
        self.assertFalse(ambiguous['complete']);self.assertIsNone(ambiguous['failed_commands'])

    def test_native_running_session_can_be_resolved_by_one_identified_wait_and_cli_fallback_dedups(self):
        rows=[command_call('start'),tool_output('start',[{'chunk_id':'initial','session_id':8}]),
              {'type':'response_item','payload':{'type':'custom_tool_call','call_id':'wait','name':'exec','input':'await tools.write_stdin({session_id:8})'}},
              tool_output('wait',[{'chunk_id':'finished','exit_code':0}])]
        audit=harness.command_outcomes(rows);self.assertTrue(audit['complete']);self.assertEqual(audit['observed_commands'],1)
        row={'type':'item.completed','item':{'type':'command_execution','id':'cmd','exit_code':1}}
        audit=harness.command_outcomes([], [row,row]);self.assertEqual(audit['failed_commands'],1)
        self.assertEqual(audit['exact_duplicate_results'],1)

    def test_relative_page_profile_runtime_and_source_page_fail_before_subprocess(self):
        for key in ('baseline_pages','profile','runtime_source','source_pages'):
            case={'baseline_pages':['/absolute/page'],'candidate_pages':['/absolute/page'],'expected':{},'prompt':'same'}
            if key in ('profile','runtime_source'):
                case['source_binding']={'profile':'/absolute/profile','runtime_source':'/absolute/runtime'}
                case['source_binding'][key]='relative/value'
            elif key=='source_pages':case[key]={'record':['relative/page']}
            else:case[key]=['relative/page']
            with self.subTest(key=key),patch.object(harness.subprocess,'run') as run:
                with self.assertRaisesRegex(ValueError,'absolute'):harness.validate_case(case)
                run.assert_not_called()

    def test_strict_settings_and_relative_profile_internals_fail_preflight(self):
        with self.assertRaisesRegex(ValueError,'explicit frozen'):
            harness.validate_case({'comparison_contract_version':1,'steps':[]})
        with tempfile.TemporaryDirectory() as directory:
            profile=Path(directory).resolve()/'profile.json';profile.write_text(json.dumps({'home':'relative/home','root':'/absolute/root'}))
            case={'baseline_pages':['/absolute/page'],'candidate_pages':['/absolute/page'],'expected':{},'prompt':'same',
                  'source_binding':{'profile':str(profile),'runtime_source':'/absolute/runtime'}}
            with patch.object(harness.subprocess,'run') as run:
                with self.assertRaisesRegex(ValueError,'home/root must be absolute'):harness.validate_case(case)
                run.assert_not_called()


class HostDeliveryFailStopTests(unittest.TestCase):
    def run_pair(self, directory, fault=None, fault_at=1, reverse=False, continue_pair=False, prefix_fault=None):
        base=Path(directory).resolve();user=base/'user';user.mkdir()
        (user/'config.toml').write_text('model="gpt-6.1-sol"\nmodel_reasoning_effort="xhigh"\nservice_tier="default"\n')
        (user/'auth.json').write_text('{}')
        page=base/'page.json';page.write_text(json.dumps({'origin':'jcm','entries':[{'path':['text'],'value':'fixture'}]}))
        case=base/'case.json';case.write_text(json.dumps({'steps':[{'name':str(i),'prompt':'Return current fixture value.','expected':{'value':1},
            'baseline_pages':[str(page)],'candidate_pages':[str(page)]} for i in range(3)]}))
        output=base/'output';calls=[]
        def execute(argv, **kwargs):
            calls.append(argv);work=Path(kwargs['cwd']);home=Path(kwargs['env']['CODEX_HOME']);count=len(calls)
            bad=fault if count==fault_at else None;turn='turn-'+str(count);ctx=context(work,turn=turn)
            if bad=='policy':ctx['sandbox_policy']['writable_roots']=['/unexpected']
            value=json.loads((work/'data/1.json').read_text());read_log=work/'reads.jsonl'
            if bad!='required_read':read_log.write_text(json.dumps({'name':'1','kind':'required','bytes':len(json.dumps(value))})+'\n')
            command='python3 read.py source '+'a'*32+' 2' if bad=='expansions' else 'python3 read.py 1'
            envelope={'chunk_id':'chunk-'+turn,'exit_code':0,'output':json.dumps(value) if bad!='exposure' else 'short output'}
            if bad=='operation':envelope={'output':json.dumps(value)}
            records=[{'type':'event_msg','payload':{'type':'task_started','turn_id':turn}},
                     {'type':'turn_context','payload':ctx},
                     {'type':'response_item','payload':{'type':'custom_tool_call','call_id':turn,'name':'exec','input':'await tools.exec_command({cmd:'+json.dumps(command)+'})'}},
                     tool_output(turn,[envelope]),receipt(turn)]
            if bad=='usage':records[-1]['payload']['usage'].pop('input_tokens')
            records[-1]['payload']['turn_id']=turn
            if bad=='command_failure':records[3:3]=[command_call(turn+'-fail'),tool_output(turn+'-fail',[{'chunk_id':'failed-'+turn,'exit_code':1,'output':'first read failed'}])]
            if bad!='timeout':records.append({'type':'event_msg','payload':{'type':'task_complete','turn_id':turn}})
            sessions=home/'sessions';sessions.mkdir(exist_ok=True);thread='thread-'+work.name
            native=sessions/('rollout-'+thread+'.jsonl')
            if not native.exists():native.write_text(json.dumps({'type':'session_meta','payload':{'id':thread}})+'\n')
            with native.open('a') as stream:
                stream.write(''.join(json.dumps(r)+'\n' for r in records))
            kwargs['stdout'].write(json.dumps({'type':'thread.started','thread_id':thread})+'\n')
            answer=Path(argv[argv.index('-o')+1]);answer.write_text(json.dumps({'value':True if bad=='typed_answer' else -1 if bad=='answer' else 1}))
            if bad=='settings_drift':(user/'config.toml').write_text((user/'config.toml').read_text().replace('default','priority'))
            if bad=='metadata_drift':
                with (user/'config.toml').open('a') as stream:stream.write('# unrelated metadata\n')
            if bad=='isolated_policy':(home/'config.toml').write_text((home/'config.toml').read_text().replace('network_access = false','network_access = true'))
            if bad=='timeout':raise subprocess.TimeoutExpired(argv,1800)
            return subprocess.CompletedProcess(argv,0)
        args=['comparator','--case',str(case),'--output-dir',str(output)]
        if reverse:args+=['--lane-order','candidate,baseline']
        with patch.dict(os.environ,{'CODEX_HOME':str(user)}),patch.object(sys,'argv',args), \
             patch.object(harness.subprocess,'run',side_effect=execute), \
             patch.object(harness.subprocess,'check_output',return_value='codex fixture'),patch('builtins.print'):
            try:harness.main()
            except SystemExit:pass
            if continue_pair:
                saved=output/'result.json';original=json.loads(saved.read_text())
                if prefix_fault=='usage':original['lanes']['candidate']['usage']['input_tokens']+=1
                if prefix_fault=='failed_stage':original['lanes']['candidate']['steps']['1']['stage_pass']=False
                harness.write(saved,original)
                native=[];bindings={str(saved):harness.file_hash(saved),str(case):harness.file_hash(case)}
                for lane in ('baseline','candidate'):
                    home=output/(lane+'-codex');work=output/lane
                    path=next((home/'sessions').glob('*.jsonl'));snapshot=base/(lane+'-prefix.jsonl');snapshot.write_bytes(path.read_bytes())
                    native.append({'path':str(path),'snapshot':str(snapshot),'prefix_bytes':path.stat().st_size})
                    for p in (work/'read.py',home/'config.toml'):bindings[str(p)]=harness.file_hash(p)
                snapshot_manifest=base/'snapshots.sha256'
                snapshot_manifest.write_text(''.join(harness.file_hash(row['snapshot'])+'  '+row['snapshot']+'\n' for row in native))
                clones=base/'clones.sha256';clones.write_text('')
                for p in (snapshot_manifest,clones):bindings[str(p)]=harness.file_hash(p)
                handoff=base/'handoff.json';harness.write(handoff,{
                    'execution':{'paid_process_running':False,'last_candidate_step_started':False,'pending_lane':'candidate',
                        'pending_step':'2','candidate_thread_id':'thread-candidate','baseline_thread_id':'thread-baseline'},
                    'inputs':{'original_case':str(case),'case_sha256':harness.file_hash(case),'original_result':str(saved)},
                    'live_state':{**{lane+'_'+kind:str(output/(lane+'-codex' if kind=='home' else lane))
                        for lane in ('baseline','candidate') for kind in ('home','work')},'native':native},
                    'preservation':{'file_bindings':bindings,'snapshot_hash_manifest':str(snapshot_manifest),'live_clone_hash_manifest':str(clones)},
                    'ownership':{'evidence_writing_stopped':True}})
                (user/'config.toml').write_text((user/'config.toml').read_text().replace('priority','default'))
                resumed=base/'continued'
                with patch.object(sys,'argv',['comparator','--case',str(case),'--output-dir',str(resumed),
                        '--continue-handoff',str(handoff),'--continue-handoff-sha256',harness.file_hash(handoff)]):
                    if prefix_fault:
                        with self.assertRaisesRegex(ValueError,'Continuation rejected'):harness.main()
                    else:harness.main()
                self.assertEqual(harness.file_hash(saved),bindings[str(saved)])
                if not prefix_fault:output=resumed
        return json.loads((output/'result.json').read_text()),calls

    def test_exit_zero_gate_failure_stops_entire_pair_before_next_paid_stage_or_lane(self):
        for fault in ('required_read','exposure','usage','policy','answer','typed_answer','expansions','timeout'):
            with self.subTest(fault=fault),tempfile.TemporaryDirectory() as d:
                result,calls=self.run_pair(d,fault)
                self.assertEqual(len(calls),1);self.assertEqual(result['stopped_at']['lane'],'baseline')
                self.assertFalse(result['pass']);self.assertIsNone(result['input_reduction'])
                self.assertNotIn('candidate',result['lanes'])
                self.assertTrue(Path(d,'output/result.json').is_file())
                if fault=='timeout':self.assertEqual(result['lanes']['baseline']['steps']['0']['usage']['input_tokens'],100)

    def test_unavailable_command_counter_does_not_block_valid_context_answer_and_usage(self):
        with tempfile.TemporaryDirectory() as d:
            result,calls=self.run_pair(d,'operation')
            self.assertEqual(len(calls),6);self.assertTrue(result['pass'])
            first=result['lanes']['baseline']['steps']['0']
            self.assertFalse(first['command_outcomes']['complete']);self.assertIsNone(first['failed_commands'])
            self.assertTrue(first['required_complete'] and first['model_visible_required_complete'] and first['usage_available'])

    def test_disclosed_source_payloads_cannot_create_command_failures(self):
        root=Path(__file__).resolve().parents[1]
        for name in ('commands-source-json-input.json','commands-source-json-exec-input.json'):
            path=root/'tests/fixtures/disclosed-command-outcomes'/name
            value=json.loads(path.read_text());audit=harness.command_outcomes(value['records'])
            self.assertTrue(audit['complete']);self.assertEqual(audit['observed_commands'],1);self.assertEqual(audit['failed_commands'],0)

    def test_valid_initial_then_invalid_resume_stops_before_third_stage_and_other_lane(self):
        with tempfile.TemporaryDirectory() as d:
            result,calls=self.run_pair(d,'exposure',2)
            self.assertEqual(len(calls),2);self.assertIn('resume',calls[1]);self.assertFalse(result['pass'])
            self.assertTrue(result['lanes']['baseline']['steps']['0']['stage_pass'])

    def test_known_command_failure_can_be_followed_by_complete_valid_read_and_reverse_order_is_symmetric(self):
        with tempfile.TemporaryDirectory() as d:
            result,calls=self.run_pair(d,'command_failure',1,True)
            self.assertEqual(len(calls),6);self.assertTrue(result['pass'])
            self.assertEqual(result['measured_lanes'],['candidate','baseline'])
            self.assertEqual(result['lanes']['candidate']['steps']['0']['failed_commands'],1)
            self.assertEqual(result['lanes']['baseline']['usage']['input_tokens'],300)

    def test_global_settings_drift_stops_before_next_model_subprocess(self):
        with tempfile.TemporaryDirectory() as d:
            result,calls=self.run_pair(d,'settings_drift')
            self.assertEqual(len(calls),1);self.assertFalse(result['global_config_unchanged'])
            self.assertTrue(result['stopped_at']['before_paid_execution'])

    def test_unrelated_global_metadata_drift_is_observed_but_does_not_stop(self):
        with tempfile.TemporaryDirectory() as d:
            result,calls=self.run_pair(d,'metadata_drift')
            self.assertEqual(len(calls),6);self.assertTrue(result['pass'])
            self.assertFalse(result['global_config_unchanged'])
            self.assertTrue(result['comparison_settings_unchanged'] and result['isolated_policy_unchanged'])

    def test_isolated_policy_drift_stops_before_next_paid_call(self):
        with tempfile.TemporaryDirectory() as d:
            result,calls=self.run_pair(d,'isolated_policy')
            self.assertEqual(len(calls),1);self.assertFalse(result['pass'])
            self.assertEqual(result['stopped_at']['failed_gates'],['ISOLATED_POLICY_DRIFT'])
            self.assertTrue(result['stopped_at']['before_paid_execution'])

    def test_bound_prefix_resumes_only_last_candidate_and_counts_new_usage_once(self):
        with tempfile.TemporaryDirectory() as d:
            result,calls=self.run_pair(d,'settings_drift',5,continue_pair=True)
            self.assertEqual(len(calls),6);self.assertTrue(result['pass'])
            self.assertIn('resume',calls[-1]);self.assertIn('thread-candidate',calls[-1])
            self.assertEqual(result['continuation']['initial_completed_stages'],5)
            self.assertEqual(result['continuation']['new_stages'],1)
            self.assertEqual(result['continuation']['prior_completed_usage']['input_tokens'],500)
            self.assertEqual(result['continuation']['incremental_usage']['input_tokens'],100)
            self.assertEqual(sum(v['usage']['input_tokens'] for v in result['lanes'].values()),600)
            self.assertTrue(result['continuation']['native_prefix_preserved'])
            self.assertFalse(json.loads(Path(d,'output/result.json').read_text())['pass'])

    def test_corrupt_or_failed_saved_prefix_is_rejected_before_model_call(self):
        for fault in ('usage','failed_stage'):
            with self.subTest(fault=fault),tempfile.TemporaryDirectory() as d:
                result,calls=self.run_pair(d,'settings_drift',5,continue_pair=True,prefix_fault=fault)
                self.assertEqual(len(calls),5);self.assertFalse(result['pass'])
                self.assertFalse(Path(d,'continued').exists())
