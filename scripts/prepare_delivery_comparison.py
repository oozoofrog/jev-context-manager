#!/usr/bin/env python3
"""Prepare matched fictional native sequences against two frozen JCM runtimes.

Fixture v1: id, initial_items (Codex public native items), steps containing name,
request (the entire semantic question), optional add_items, reuse_request_from,
use_delta, and expected. Expected values never enter dispatch or source capture.
Each lane runs in a separate interpreter, with the same seed identities and
native transcript files, independent journals, and per-stage frozen profiles.
No global settings, hooks, installs or historical sources are used.
"""
import argparse
from contextlib import closing
import copy
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time


def encode(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()


def digest(value):
    return hashlib.sha256(value if isinstance(value,bytes) else encode(value)).hexdigest()


def write(path,value):
    path.write_bytes(encode(value)+b'\n')


def runtime_hash(source):
    return digest({p.name:digest(p.read_bytes()) for p in (Path(source)/'jcm').glob('*.py')})


def validate_fixture(fixture):
    if fixture.get('version') != 1 or not fixture.get('initial_items') or not fixture.get('steps'):
        raise ValueError('Expected native sequence fixture version 1')
    seen={};frontiers={};frontier=0
    for step in fixture['steps']:
        name=step['name']
        if not re.fullmatch(r'[A-Za-z0-9_-]+',name) or name in seen:
            raise ValueError('Invalid/duplicate step name')
        if not step.get('request','').strip() or not isinstance(step.get('expected'),dict):
            raise ValueError('Every stage requires its entire semantic request and separate expected answer')
        if step.get('consumer_prompt'):
            raise ValueError('Put all answer requirements in request before planning')
        previous=step.get('reuse_request_from')
        if previous:
            if previous not in seen or seen[previous]['request']!=step['request'] or step.get('add_items') or frontiers.get(previous)!=frontier:
                raise ValueError('Unchanged-snapshot warm stage must reuse an identical earlier request with no additions')
        else:
            frontier+=1
        seen[name]=step
        frontiers[name]=frontier


def clone_storage(profile, target, active_store=None):
    """SQLite backups preserve a consistent journal while a lane is active."""
    cfg=json.loads(Path(profile).read_text());old=Path(cfg['home']);target=Path(target)
    target.mkdir(parents=True,exist_ok=False)
    for path in old.rglob('*'):
        relative=path.relative_to(old);dest=target/relative
        if path.is_dir():dest.mkdir(exist_ok=True)
        elif path.name.endswith(('-wal','-shm')):continue
        elif path.suffix=='.sqlite':
            dest.parent.mkdir(parents=True,exist_ok=True)
            own=not (active_store and path==active_store.path/'journal.sqlite')
            connection=sqlite3.connect(path) if own else active_store.db
            with closing(sqlite3.connect(dest)) as backup:connection.backup(backup)
            if own:connection.close()
        else:shutil.copy2(path,dest)
    cfg['home']=str(target.resolve())
    cfg['cli_argv']=[str(target.resolve()) if word==str(old) else word for word in cfg['cli_argv']]
    frozen=target/'profiles'/(cfg['repo_id']+'.json');write(frozen,cfg)
    return frozen


def source_snapshot(store):
    return digest([{'event_id':e['id'],'revision':e['revision'],'text_hash':digest(store.material(e)['text'])} for e in store.events()])


def full_transition(store, current, retained, retained_brief):
    """Only a valid exact base may become a full task/session transition.

    RETAINED_CONTEXT_NOT_APPLICABLE also means bad hashes or missing receipts.
    Verify those and the source/policy/contracts before distinguishing identity;
    never catch a failed delta and infer the reason from its error string.
    """
    from jcm.source_read import bound_pack
    from jcm.representations import validate_contract
    from jcm.util import JCMError
    base_id, separator, content_hash = retained.partition(':')
    if not separator:
        raise JCMError('INVALID_RETAINED_CONTEXT')
    base = bound_pack(store, base_id)
    current = bound_pack(store, current['pack_id'])
    old = base.get('context_views', {}).get('brief', {})
    if (not old or old.get('page_manifest', {}).get('content_hash') != content_hash or
            not store.db.execute('SELECT 1 FROM meta WHERE key=?', ('required_read:' + base_id,)).fetchone()):
        raise JCMError('RETAINED_CONTEXT_NOT_APPLICABLE')
    if (base.get('query_context', {}).get('contract_version') != 1 or
            current.get('query_context', {}).get('contract_version') != 1):
        raise JCMError('RETAINED_CONTEXT_CONTRACT_UNVERIFIED')
    if (validate_contract(base['query_context'], old) or
            validate_contract(current['query_context'], current['context_views']['brief'])):
        raise JCMError('DELIVERY_CONTRACT_VIOLATION')
    if retained_brief != {k:v for k,v in old.items() if k != 'page_manifest'}:
        raise JCMError('HARNESS_RETAINED_CONTENT_MISMATCH')
    changed = [key for key, previous, present in (
        ('task_id', base['task_frame']['task_id'], current['task_frame']['task_id']),
        ('session_id', base['session_id'], current['session_id'])) if previous != present]
    if not changed:
        return None
    return {'reason':'verified_identity_transition', 'changed_fields':changed,
        'base_pack_id':base_id, 'current_pack_id':current['pack_id'],
        'previous_task_id':base['task_frame']['task_id'], 'current_task_id':current['task_frame']['task_id'],
        'previous_session_id':base['session_id'], 'current_session_id':current['session_id']}


def inventory_lane(fixture,output,profile,transcript_files):
    """No transport: scheduling probes, not predictions of semantic decisions."""
    import jcm
    from jcm.adapter import register_transcript,recover_sources
    from jcm.batching import batches,context_fits,request_size
    from jcm.reusable_selection import source_questions
    from jcm.semantic_cache import source_items,request
    from jcm.source_units import units
    from jcm.store import Store
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    cfg=json.loads(Path(profile).read_text());store=Store(cfg);stages=[]
    try:
        for index,step in enumerate(fixture['steps']):
            number=0 if index==0 else index+1
            if index==0 or step.get('add_items'):
                register_transcript(store,Path(transcript_files[number]),fixture['id']+'-history-'+str(number))
                recover_sources(store)
            materials=[store.material(e) for e in store.events()]
            context={'request':step['request'],'task_scope':None}
            parts=[p for m in materials for p in source_items(m,context,source_questions)]
            sizes=[request_size(cfg,*request(context,batch,source_questions)) for batch in
                   batches(parts,lambda batch:context_fits(*request(context,batch,source_questions)))]
            stages.append({'name':step['name'],'semantic_request_sha256':digest(step['request'].encode()),
                'source_snapshot_hash':source_snapshot(store),'source_events':len(materials),
                'source_chars':sum(len(m['text']) for m in materials),'structural_units':sum(len(list(units(m))) for m in materials),
                'retrieval_units':len(parts),'estimated_batch_bytes':sizes,
                'largest_individual_request_bytes':max((request_size(cfg,*request(context,[p],source_questions)) for p in parts),default=0),
                'semantic_cache_entries':store.db.execute('SELECT count(*) FROM semantic_items').fetchone()[0],
                'cache_hits':0,'cache_misses':len(parts),
                'limit':'Fresh seed inventory; no judgments have been populated. Probes use task_scope=None and exclude current request '
                    'capture, task routing, pair relations and the eventual query plan. Actual prepared pack metrics report those costs and warm hits.'})
    finally:store.close()
    source=Path(jcm.__file__).resolve().parent.parent
    result={'pass':True,'inventory_only':True,'paid_calls':0,'runtime_source':str(source),'runtime_hash':runtime_hash(source),'stages':stages}
    write(output/'result.json',result);return result


def prepare_lane(fixture, output, profile, transcript_files, provider=None):
    import jcm
    from jcm.adapter import register_transcript,recover_sources
    from jcm.coordinator import dispatch,read_pack
    from jcm.delivery import reconstruct_pages,apply_delta
    from jcm.representations import validate_contract
    from jcm.snapshot import snapshot
    from jcm.store import Store
    cfg=json.loads(Path(profile).read_text());store=Store(cfg)
    if callable(provider) and not hasattr(provider,'evaluate'):
        provider=provider(store)
    source=Path(jcm.__file__).resolve().parent.parent
    initial_hash=runtime_hash(source)
    result={'runtime_source':str(source),'runtime_hash':initial_hash,'steps':[],'pass':False,
            'scope':'Synthetic native preparation only. Mock providers do not establish semantic recall.',
            'provider':'injected_local_mock' if provider else 'real_jev'}
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    requests={};request_snapshots={};retained=None;retained_brief=None
    try:
        register_transcript(store,Path(transcript_files[0]),fixture['id']+'-history-0')
        recover_sources(store)
        for index,step in enumerate(fixture['steps']):
            stage=output/step['name'];stage.mkdir()
            if step.get('add_items'):
                register_transcript(store,Path(transcript_files[index+1]),fixture['id']+'-history-'+str(index+1))
                recover_sources(store)
            if step.get('reuse_request_from'):
                # Do not recapture a procedural request: the exact token and
                # source frontier must be identical for true warm reuse.
                token=requests[step['reuse_request_from']]
            else:
                event=store.capture(session=fixture['id']+'-consumer',turn=step['name'],kind='user_message',role='user',
                    payload={'text':step['request']},snapshot=snapshot(cfg['root']),source_key=step['name'],
                    identity=['request',step['name'],step['request']])
                token=store.request(fixture['id']+'-consumer',event)
            requests[step['name']]=token
            before=source_snapshot(store)
            if step.get('reuse_request_from') and before!=request_snapshots[step['reuse_request_from']]:
                raise ValueError('Warm source snapshot changed before dispatch')
            request_snapshots[step['name']]=before
            began=time.perf_counter()
            route=dispatch(store,token,provider)
            row=store.db.execute('SELECT blob FROM packs WHERE id=?',(route['pack_id'],)).fetchone()
            pack=store.blob(row[0]);write(stage/'pack.json',pack)
            def read_pages(handle=None,prefix='brief'):
                pages=[];paths=[];number=1
                while True:
                    page=read_pack(store,pack['pack_id'],number,'brief',retained_context=handle)
                    path=stage/(prefix+'-'+str(number)+'.json');write(path,page)
                    pages.append(page);paths.append(str(path))
                    if not page.get('next_read_command'):break
                    number+=1
                return reconstruct_pages(pages),paths,pages[-1]
            brief,paths,last=read_pages()
            checks={'contract_valid':not validate_contract(pack['query_context'],brief),
                    'request_matches':pack['request']==step['request'] and pack['query_context']['request']==step['request'],
                    'required_complete':bool(last.get('required_context_complete')),
                    'provider_available':not any(error in str(pack) for error in ('PROVIDER_HTTP_402','PROVIDER_HTTP_401','PROVIDER_HTTP_403','PROVIDER_CREDENTIAL_UNAVAILABLE')),
                    'not_blocked':pack['dispatch']!='blocked', 'snapshot_unchanged_during_dispatch':before==source_snapshot(store)}
            kind=brief.get('delivery_mode','brief')
            if kind != 'brief':
                raise ValueError('Full required view unexpectedly uses '+kind)
            transition=None
            full_reason='initial_context' if not retained else 'delta_not_requested'
            if retained and step.get('use_delta',False):
                transition=full_transition(store,pack,retained,retained_brief)
                if transition:
                    full_reason=transition['reason']
                    checks['full_transition_equal_brief']=brief == {
                        k:v for k,v in pack['context_views']['brief'].items() if k != 'page_manifest'}
                else:
                    change,paths,last=read_pages(retained,'delta')
                    kind=change.get('delivery_mode','brief')
                    if kind != 'delta':
                        raise ValueError('Retained read unexpectedly uses '+kind)
                    checks['delta_equal_brief']=apply_delta(retained_brief,change)==brief
                    full_reason=None
            retained=last.get('context_handle');retained_brief=brief
            frozen=clone_storage(profile,stage/'storage',store)
            binding={'runtime_source':str(source),'runtime_hash':initial_hash,'profile':str(frozen),
                'profile_sha256':digest(frozen.read_bytes()),'pack_id':pack['pack_id'],'pack_digest':digest(pack),
                'source_snapshot_hash':source_snapshot(store)}
            planner_hash=digest(pack['request'].encode());consumer_hash=digest(step['request'].encode())
            value={'name':step['name'],'prompt':step['request'],'request_hashes':{'planner':planner_hash,'consumer':consumer_hash},
                'expected':copy.deepcopy(step['expected']), 'pages':paths,'page_hashes':{p:digest(Path(p).read_bytes()) for p in paths},
                'source_binding':binding,'checks':checks,'delivery':kind,
                'full_delivery_reason':full_reason,'full_transition':transition,
                'task_id':pack['task_frame']['task_id'],'session_id':pack['session_id'],'preparation_metrics':pack['metrics'],
                'preparation_seconds':time.perf_counter()-began,'required_bytes':sum(Path(p).stat().st_size for p in paths),
                'source_snapshot_hash':before,'request_token':token,'warm_reuse':bool(step.get('reuse_request_from'))}
            result['steps'].append(value);write(output/'result.json',result)
            print(json.dumps({'stage':step['name'],'checks':checks,'transport_calls':pack['metrics']['transport_calls']},ensure_ascii=False),flush=True)
            if 'PROVIDER_HTTP_402' in str(pack):
                break
        result['runtime_unchanged']=initial_hash==runtime_hash(source)
        result['pass']=len(result['steps'])==len(fixture['steps']) and result['runtime_unchanged'] and all(all(s['checks'].values()) for s in result['steps'])
    except BaseException as error:
        result['error']={'type':type(error).__name__,'message':str(error),'last_completed_step':result['steps'][-1]['name'] if result['steps'] else None}
        raise
    finally:
        store.close();write(output/'result.json',result)
    return result


def prepare_comparison(args):
    from jcm import config
    fixture=json.loads(args.fixture.read_text());validate_fixture(fixture)
    output=args.output_dir.resolve();output.mkdir(parents=True,exist_ok=False)
    root=output/'workspace';root.mkdir();transcripts=output/'transcripts';transcripts.mkdir()
    cfg=config.enable(output/'seed',root,transcript_roots=[transcripts])
    if args.pack_byte_ceiling:
        cfg=config.save_policy(cfg,pack_byte_ceiling=args.pack_byte_ceiling)
    seed=Path(cfg['home'])/'profiles'/(cfg['repo_id']+'.json')
    files={}
    for index,items in [(0,fixture['initial_items'])]+[(i+1,s['add_items']) for i,s in enumerate(fixture['steps']) if s.get('add_items')]:
        session=fixture['id']+'-history-'+str(index)
        rows=[{'type':'session_meta','payload':{'id':session,'cwd':str(root)}}]
        rows.extend({'type':'event_msg','payload':{'type':'item_completed','turn_id':'native-'+str(index),'item':item}} for item in items)
        path=transcripts/(str(index)+'.jsonl');path.write_bytes(b'\n'.join(encode(r) for r in rows)+b'\n');files[index]=str(path)
    write(output/'transcript-files.json',files)
    profiles={lane:clone_storage(seed,output/(lane+'-storage')) for lane in ('baseline','candidate')}
    runs={}
    for lane,source in [('baseline',args.baseline_source.resolve()),('candidate',args.candidate_source.resolve())]:
        if runtime_hash(source)!=getattr(args,lane+'_hash'):
            raise ValueError('Frozen runtime hash mismatch: '+lane)
        command=[sys.executable,str(Path(__file__).resolve()),'--fixture',str(args.fixture.resolve()),
            '--output-dir',str(output/lane),'--lane-profile',str(profiles[lane]),'--transcript-files',str(output/'transcript-files.json')]
        if args.inventory_only:command.append('--inventory-only')
        with (output/(lane+'.log')).open('w') as log:
            process=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,env={**os.environ,'PYTHONPATH':str(source)})
        path=output/lane/'result.json'
        if path.exists():runs[lane]=json.loads(path.read_text())
        if process.returncode or not runs.get(lane,{}).get('pass'):
            write(output/'preparation-result.json',{'pass':False,'lanes':runs,'failed_lane':lane});return 1
    if args.inventory_only:
        write(output/'inventory-result.json',{'pass':True,'paid_calls':0,'lanes':runs,
            'fixture_sha256':digest(args.fixture.read_bytes()),'harness_sha256':digest(Path(__file__).read_bytes())})
        return 0
    steps=[]
    for a,b in zip(runs['baseline']['steps'],runs['candidate']['steps']):
        if a['source_snapshot_hash']!=b['source_snapshot_hash'] or a['request_hashes']!=b['request_hashes']:
            raise ValueError('Matched corpus or canonical request diverged')
        steps.append({k:a[k] for k in ('name','prompt','expected','request_hashes')} |
            {lane+'_pages':runs[lane]['steps'][len(steps)]['pages'] for lane in runs} |
            {'source_bindings':{lane:runs[lane]['steps'][len(steps)]['source_binding'] for lane in runs},
             'page_hashes':{lane:runs[lane]['steps'][len(steps)]['page_hashes'] for lane in runs}})
    write(output/'consumer-case.json',{'comparison_contract_version':1,'fixture_sha256':digest(args.fixture.read_bytes()),'steps':steps})
    write(output/'preparation-result.json',{'pass':True,'lanes':runs,'fixture_sha256':digest(args.fixture.read_bytes()),
        'harness_sha256':digest(Path(__file__).read_bytes()),'requires_disable_after_consumption':True})
    return 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture',type=Path,required=True);parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--baseline-source',type=Path);parser.add_argument('--candidate-source',type=Path)
    parser.add_argument('--baseline-hash');parser.add_argument('--candidate-hash')
    parser.add_argument('--pack-byte-ceiling',type=int)
    parser.add_argument('--inventory-only',action='store_true',help='Capture fictional inputs and report local sizing/cache probes without API calls')
    parser.add_argument('--lane-profile',type=Path,help=argparse.SUPPRESS);parser.add_argument('--transcript-files',type=Path,help=argparse.SUPPRESS)
    args=parser.parse_args();fixture=json.loads(args.fixture.read_text());validate_fixture(fixture)
    if not args.inventory_only and not os.environ.get('TYPESAFE_API_KEY'):raise SystemExit('TYPESAFE_API_KEY unavailable; no calls made')
    if args.lane_profile:
        files={int(k):v for k,v in json.loads(args.transcript_files.read_text()).items()}
        method=inventory_lane if args.inventory_only else prepare_lane
        return 0 if method(fixture,args.output_dir,args.lane_profile,files)['pass'] else 1
    if not all((args.baseline_source,args.candidate_source,args.baseline_hash,args.candidate_hash)):
        parser.error('Both frozen runtime paths and hashes are required')
    return prepare_comparison(args)


if __name__=='__main__':
    raise SystemExit(main())
