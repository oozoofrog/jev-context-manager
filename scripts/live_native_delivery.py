#!/usr/bin/env python3
"""Frozen native adapter -> real Jev -> public reads, with Codex consumer cases.

Evaluation data are fixed independently of P1 mocks. Every stage saves exact API
responses and the correctness oracle; no prompt/threshold tuning occurs here.
"""
import argparse
import copy
import json
import os
import time
from pathlib import Path
import jcm

from jcm import config
from jcm.adapter import register_transcript, recover_sources
from jcm.coordinator import dispatch, read_pack
from jcm.delivery import reconstruct_pages, apply_delta
from jcm.metrics import delivery_metrics
from jcm.representations import validate_contract
from jcm.snapshot import snapshot
from jcm.store import Store
from jcm.util import digest, encode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--fixture', type=Path, default=Path(__file__).resolve().parents[1]/'tests/fixtures/native_delivery_eval.json')
    args = parser.parse_args()
    if not os.environ.get('TYPESAFE_API_KEY'):
        raise SystemExit('TYPESAFE_API_KEY unavailable')
    oracle = json.loads(args.fixture.read_text())
    output = args.output_dir.resolve(); output.mkdir(parents=True, exist_ok=False)
    (output/'frozen-oracle.json').write_bytes(args.fixture.read_bytes())
    root = output/'workspace'; root.mkdir()
    transcripts = output/'transcripts'; transcripts.mkdir()
    cfg = config.enable(output/'storage',root,transcript_roots=[transcripts])
    store = Store(cfg)
    def capture(turn,text,session='consumer',role='user'):
        return store.capture(session=session,turn=turn,kind=role+'_message',role=role,
            payload={'text':text},snapshot=snapshot(root),source_key=session+':'+turn,identity=[session,turn,text])
    def native():
        items = [
            {'type':'UserMessage','id':'goal','content':[{'type':'text','text':oracle['goal']}]},
            {'type':'AgentMessage','id':'report','phase':'final_answer','content':[{'type':'text','text':oracle['report']}]},
            {'type':'CommandExecution','id':'blocker','command':['export','/artifacts/harbor-A'],'cwd':str(root),'status':'completed','exit_code':0,'aggregated_output':oracle['command']},
            {'type':'McpToolCall','id':'partial','server':'fixture','tool':'validate','arguments':{'artifact':'/artifacts/harbor-A'},'status':'completed','result':{'structuredContent':oracle['mcp'],'content':[]}},
            {'type':'McpToolCall','id':'pending','server':'fixture','tool':'hardware','arguments':{'artifact':'/artifacts/harbor-A'},'status':'completed','result':{'status':'pending','content':[{'type':'text','text':oracle['pending']}]}},
            {'type':'McpToolCall','id':'error','server':'fixture','tool':'sign','arguments':{'artifact':'/artifacts/harbor-A'},'status':'completed','result':{'isError':True,'content':[{'type':'text','text':oracle['error']}]}},
            {'type':'CommandExecution','id':'old','command':['render','/archive/orchard-A'],'cwd':str(root),'status':'completed','exit_code':0,'aggregated_output':oracle['old']},
        ]
        records = [{'type':'session_meta','payload':{'id':'harbor-history','cwd':str(root)}}]
        records += [{'type':'event_msg','payload':{'type':'item_completed','turn_id':'harbor','item':item}} for item in items]
        path = transcripts/'native.jsonl'; path.write_bytes(b'\n'.join(encode(r) for r in records)+b'\n')
        register_transcript(store,path,'harbor-history')
        return recover_sources(store)
    def reads(pack,stage,view='brief',handle=None):
        pages=[]; paths=[]; n=1
        while True:
            page = read_pack(store,pack['pack_id'],n,view,retained_context=handle)
            path = output/f'{stage}-{view}-{n}.json'; path.write_bytes(encode(page))
            pages.append(page); paths.append(str(path))
            if not page.get('next_read_command'):
                break
            n+=1
        return reconstruct_pages(pages), paths, pages[-1]
    runtime_source = Path(jcm.__file__).resolve().parent.parent
    runtime_hash = lambda: digest({p.name:digest(p.read_bytes()) for p in (runtime_source/'jcm').glob('*.py')})
    start_runtime_hash = runtime_hash()
    consumer_steps = []
    result = {'runtime_hash':start_runtime_hash, 'pass':False,'scope':'Synthetic native transcript via adapter; actual Jev; final public API reads. Consumer cases require separate real Codex run.',
              'oracle_hash':digest(oracle),'stages':{},'captured_native_events':native()}
    retained = None; retained_brief = None
    try:
        for stage,request in [('cold',oracle['request']),('warm',oracle['request']),
                              ('correction',oracle['correction']+' '+oracle['request']),
                              ('artifact_b',oracle['request_b']),('return_a',oracle['request'])]:
            if stage == 'artifact_b':
                capture('new-b',oracle['artifact_b'],session='harbor-b')
            request = request+'\n\n'+oracle['consumer_prompt']
            began = time.perf_counter()
            current = capture(stage,request)
            route = dispatch(store,store.request('consumer',current))
            pack = store.blob(store.db.execute('SELECT blob FROM packs WHERE id=?',(route['pack_id'],)).fetchone()[0])
            (output/f'{stage}-pack.json').write_bytes(encode(pack))
            brief, candidate, last = reads(pack,stage)
            _, baseline, _ = reads(pack,stage,'full')
            expected = copy.deepcopy(oracle['expected'])
            required = list(oracle['required'])
            if stage in ('correction','return_a'):
                expected['protocol']=63; required.remove('protocol 62');required.append('protocol 63')
            if stage == 'artifact_b':
                expected.update(artifact='/artifacts/harbor-B',protocol=81,blockers=['HARBOR_BATTERY_36'],
                    keep_interlock=False,unresolved_report_conflict=False)
                required=['/artifacts/harbor-B','81','HARBOR_BATTERY_36']
            records = json.dumps({'records':brief.get('selected_records',[]),
                                  'goal':brief.get('task_frame',{}).get('goal')},ensure_ascii=False)
            checks = {'mechanical_contract':not validate_contract(pack['query_context'],brief),
                      'required_read_complete':last.get('required_context_complete',False),
                      'required_facts':all(fact in records for fact in required),
                      'old_unrelated_deferred':all(fact not in records for fact in oracle['forbidden_required']),
                      'verification_not_promoted':all(not {**brief.get('record_defaults',{}),**r}.get('verification_currently_applicable') for r in brief.get('selected_records',[])),
                      'not_blocked':pack['dispatch']!='blocked'}
            delta_result = None
            if retained and stage in ('warm','correction'):
                change,delta_paths,_ = reads(pack,stage+'-delta',handle=retained)
                decoded = apply_delta(retained_brief,change)
                checks['delta_equal_full'] = decoded == brief
                delta_result={'paths':delta_paths,'bytes':sum(Path(p).stat().st_size for p in delta_paths)}
            retained=last.get('context_handle');retained_brief=brief
            case={'prompt':request,'expected':expected,
                  'request_hashes':{'planner':digest(pack['request'].encode()),'consumer':digest(request.encode())},
                  'baseline_pages':baseline,'candidate_pages':candidate,
                  'source_binding':{'runtime_source':str(runtime_source),
                                    'profile':str(output/'storage/profiles'/(cfg['repo_id']+'.json')),'pack_id':pack['pack_id']}}
            (output/f'{stage}-consumer-case.json').write_bytes(encode(case))
            consumer_steps.append({'name':stage, **case, 'candidate_pages':delta_result['paths'] if delta_result else candidate})
            result['stages'][stage]={'checks':checks,'pass':all(checks.values()),'quality':pack['quality'],
                'dispatch':pack['dispatch'],'gaps':brief.get('coverage',{}).get('gaps'),
                'elapsed_seconds':time.perf_counter()-began,'metrics':pack['metrics'],
                'delivery_metrics':delivery_metrics(store,pack['pack_id']),'delta':delta_result,
                'candidate_bytes':sum(Path(p).stat().st_size for p in candidate),
                'baseline_bytes':sum(Path(p).stat().st_size for p in baseline)}
            (output/'result.json').write_bytes(encode(result))
            print(json.dumps({'stage':stage,'checks':checks,'quality':pack['quality'],'calls':pack['metrics']['transport_calls'],'elapsed':result['stages'][stage]['elapsed_seconds']}),flush=True)
        result['runtime_unchanged'] = runtime_hash() == start_runtime_hash
        result['pass']=all(s['pass'] for s in result['stages'].values()) and result['runtime_unchanged']
        (output/'sequence-consumer-case.json').write_bytes(encode({'steps':consumer_steps}))
    finally:
        # Keep this isolated fixture enabled for pack-bound consumer expansion.
        # The caller disables it after consumption; there are no installed hooks.
        result['fixture_profile']=str(output/'storage/profiles'/(cfg['repo_id']+'.json'))
        result['fixture_requires_disable_after_consumption']=True
        store.close();(output/'result.json').write_bytes(encode(result))
    return 0 if result['pass'] else 1


if __name__=='__main__':
    raise SystemExit(main())
