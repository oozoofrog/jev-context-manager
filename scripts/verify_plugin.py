#!/usr/bin/env python3
"""Verify marketplace install/lifecycle in a separate CODEX_HOME; optional real sessions."""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from jcm import config
from jcm.follower import follower_status
from jcm.store import Store

REPO = Path(__file__).resolve().parents[1]
SELECTOR = 'jev-context-manager@jcm'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', default=str(REPO))
    parser.add_argument('--ref')
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--output-dir', type=Path, help='Keep all results/logs here without changing tracked evidence pointers')
    args = parser.parse_args()
    case = uuid.uuid4().hex[:8]
    base = Path(tempfile.mkdtemp(prefix='jcm-plugin-' + case + '-')).resolve()
    home, ch, root = base/'private', base/'codex', base/'workspace'
    ch.mkdir(); root.mkdir()
    (ch/'config.toml').write_text('')
    user_home = Path(os.environ.get('CODEX_HOME', '~/.codex')).expanduser().resolve()
    global_before = (user_home/'config.toml').read_bytes()
    env = {**os.environ, 'CODEX_HOME': str(ch), 'JCM_HOME': str(home)}
    # No copying/token output; CLI uses existing credentials only for the live lane.
    if args.live:
        if not os.environ.get('TYPESAFE_API_KEY'):
            raise SystemExit('Live Jev credential unavailable')
        (ch/'auth.json').symlink_to(user_home/'auth.json')
    else:
        env.pop('TYPESAFE_API_KEY', None)
    evidence = args.output_dir.resolve() if args.output_dir else REPO/'evidence'/('live-plugin-' + case)
    evidence.mkdir(parents=True, exist_ok=False)
    summary = {'source':args.source, 'ref':args.ref, 'fixture':str(base), 'live':args.live,
               'host_version':subprocess.check_output(['codex','--version'],text=True).strip(),
               'checks':{}, 'sessions':[], 'pass':False}
    store = None

    def call(argv, name=None, stdin=None):
        result = subprocess.run(argv, env=env, cwd=root, input=stdin, text=True, capture_output=True, timeout=90)
        if name:
            (evidence/(name+'.stdout.log')).write_text(result.stdout)
            (evidence/(name+'.stderr.log')).write_text(result.stderr)
        if result.returncode:
            raise RuntimeError((name or argv[0]) + ' failed; see saved logs')
        return json.loads(result.stdout) if result.stdout.strip() else None

    def run_session(name, prompt):
        argv=['codex','exec','--dangerously-bypass-hook-trust','--skip-git-repo-check',
              '-m','gpt-6-astra','-c','model_reasoning_effort="xhigh"','-c','features.hooks=true',
              '-c','features.multi_agent=false','-c','sandbox_workspace_write.network_access=true',
              '-c','projects.'+json.dumps(str(root))+'.trust_level="trusted"',
              '-s','workspace-write','-C',str(root),'--json','-']
        print(json.dumps({'session':name,'state':'running'}),flush=True)
        began=time.monotonic()
        with (evidence/(name+'.jsonl')).open('w') as out, (evidence/(name+'.stderr.log')).open('w') as err:
            proc=subprocess.Popen(argv,env=env,stdin=subprocess.PIPE,stdout=out,stderr=err,text=True)
            try: proc.communicate(prompt,timeout=300)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try: proc.wait(timeout=10)
                except subprocess.TimeoutExpired: proc.kill();proc.wait()
                raise RuntimeError(name+' timed out')
        lines=[json.loads(s) for s in (evidence/(name+'.jsonl')).read_text().splitlines() if s.startswith('{')]
        tid=next((v['thread_id'] for v in lines if v.get('type')=='thread.started'),None)
        summary['sessions'].append({'name':name,'id':tid,'exit_code':proc.returncode,
                                    'elapsed_seconds':round(time.monotonic()-began,2)})
        if proc.returncode or not tid: raise RuntimeError(name+' failed')
        return tid,lines

    try:
        argv=['codex','plugin','marketplace','add',args.source,'--json']
        if args.ref: argv+=['--ref',args.ref]
        summary['marketplace']=call(argv,'marketplace-add')
        installed=call(['codex','plugin','add',SELECTOR,'--json'],'plugin-add')
        summary['installation']=installed
        plugin=Path(installed['installedPath'])
        launcher=str(plugin/'scripts/jcm')
        before=list(base.rglob('registry.sqlite'))
        summary['checks']['install_does_not_activate_projects']=not before
        inventory=call(['codex','plugin','list','--json'],'inventory')
        summary['checks']['installed_enabled']=any(v['pluginId']==SELECTOR and v['enabled'] for v in inventory['installed'])
        manifest=json.loads((plugin/'runtime-manifest.json').read_text())
        summary['runtime_manifest_sha256']=hashlib.sha256((plugin/'runtime-manifest.json').read_bytes()).hexdigest()
        revision=subprocess.run(['git','-C',summary['marketplace']['installedRoot'],'rev-parse','HEAD'],
                                text=True,capture_output=True)
        summary['marketplace_revision']=revision.stdout.strip() if revision.returncode==0 else None
        summary['checks']['installed_payload_integrity']=all(hashlib.sha256((plugin/p).read_bytes()).hexdigest()==h for p,h in manifest.items())
        enable=[launcher,'--repo',str(root),'enable']
        call(enable,'enable')
        policy=config.load(home,root);store=Store(policy)
        summary['checks']['no_permanent_project_hooks']=not(root/'.codex/hooks.json').exists()
        summary['checks']['external_storage']=str(home) == policy['home'] and not(root/'.jcm').exists()
        summary['checks']['automatic_jev']=policy['allow_egress'] is True
        if args.live:
            (root/'connection.py').write_text('def reconnect(paused):\n    return {"connected": True, "paused": False}\n')
            old,_=run_session('old','connection.py 연결 복구 작업입니다. 반환값에 protocol_version: 1을 유지하고 전달받은 paused 값을 그대로 유지해야 합니다. 지금은 구현하지 마세요. 별도 도구 호출에서 Python uuid.uuid4().hex로 임의 표식을 생성하여 CONTINUITY_MARKER=<표식> 한 줄로 출력하세요. 이후 구현의 continuity_marker 값에 이 표식을 정확히 사용해야 합니다. 표식을 파일에 저장하지 말고 마지막 답변은 준비 완료 한 줄만 쓰세요.')
            marker=None; marker_id=None
            for event in store.events():
                if event['session']==old and event['role']=='tool':
                    payload=store.blob(event['blob'])
                    output=payload.get('public_item',{}).get('aggregated_output','') or ''
                    found=re.search(r'^CONTINUITY_MARKER=([a-f0-9]{32})$',output,re.M)
                    if found: marker=found.group(1);marker_id=event['id']
            if not marker: raise RuntimeError('Native plugin hooks did not capture the real tool marker')
            fresh,lines=run_session('fresh','이어서 connection.py의 연결 복구를 구현하고 일시정지 상태에서 재연결한 결과를 result.json에 작성하세요. 현재 파일을 확인하고 동작을 검증하세요.')
            spec=importlib.util.spec_from_file_location('fixture_connection',root/'connection.py')
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            result=json.loads((root/'result.json').read_text())
            packs=[store.blob(r['blob']) for r in store.db.execute('SELECT p.* FROM packs p JOIN requests r ON p.token=r.token WHERE r.session=? AND p.delivery=?',(fresh,'read_served'))]
            summary['checks'].update({
                'fresh_independent_session':fresh!=old,
                'native_hook_received':bool(store.db.execute("SELECT 1 FROM meta WHERE key='hook_received:UserPromptSubmit'").fetchone()),
                'new_bootstrap_used':'bootstrap new --request-token' in json.dumps(lines),
                'served_real_pack':bool(packs),
                'old_marker_in_pack':any(any(r['event_id']==marker_id for r in p['selected_records']) for p in packs),
                'real_Jev_normal':any(p['quality']=='normal' and p['decisions'] and all(d['lane']=='real_http' for d in p['decisions']) for p in packs),
                'requirements_restored':module.reconnect(True).get('paused') is True and module.reconnect(False).get('paused') is False and result.get('protocol_version')==1,
                'marker_restored':result.get('continuity_marker')==marker,
                'no_project_hooks_after_sessions':not(root/'.codex/hooks.json').exists(),
            })
            summary['trust']='Vetted synthetic fixture with per-invocation hook trust bypass; production approval UI not tested'
            summary['provider_calls']=[dict(r) for r in store.db.execute('SELECT * FROM calls')]
        # Real subprocess follower for both install and live lanes.
        sid='fixture-'+case
        transcript=ch/'sessions'/('rollout-fixture-'+sid+'.jsonl');transcript.parent.mkdir(exist_ok=True)
        transcript.write_text(json.dumps({'type':'session_meta','payload':{'id':sid,'cwd':str(root),'cli_version':'0.158.0-alpha.2.1'}})+'\n')
        adopted=call([launcher,'--repo',str(root),'bootstrap','existing','--session-id',sid,'--transcript',str(transcript)],'adopt')
        summary['checks']['follower_started']=adopted['follower']['running']
        cfg=ch/'config.toml';enabled=cfg.read_text()
        cfg.write_text(enabled.replace('enabled = true','enabled = false'))
        deadline=time.monotonic()+5
        while follower_status(store,adopted['source']['key'])['running'] and time.monotonic()<deadline:time.sleep(.1)
        state=follower_status(store,adopted['source']['key'])
        summary['checks']['disable_stops_follower']=not state['running'] and state.get('error')=='PLUGIN_INACTIVE_OR_UNVERIFIED'
        count=len(store.events())
        call([launcher,'plugin-hook'],'disabled-hook',json.dumps({'hook_event_name':'UserPromptSubmit','session_id':sid,'cwd':str(root),'turn_id':'denied','prompt':'must not record'}))
        summary['checks']['disable_stops_capture']=len(store.events())==count
        cfg.write_text(enabled)
        call(['codex','plugin','remove',SELECTOR,'--json'],'remove')
        summary['checks']['uninstall_removes_cache']=not plugin.exists()
        summary['checks']['uninstall_preserves_records']=len(store.events())==count and (home/'registry.sqlite').exists()
        installed_again=call(['codex','plugin','add',SELECTOR,'--json'],'reinstall')
        summary['checks']['reinstall_preserves_records']=len(store.events())==count
        call([str(Path(installed_again['installedPath'])/'scripts/jcm'),'--repo',str(root),'status'],'reinstall-status')
    except Exception as exc:
        summary['error']=str(exc)
    finally:
        if store:
            store.change_policy(enabled=False)
            summary['fixture_disabled']=True
            store.close()
        if (ch/'auth.json').is_symlink(): (ch/'auth.json').unlink()
        summary['checks']['global_configuration_unchanged']=(user_home/'config.toml').read_bytes()==global_before
        summary['pass']=not summary.get('error') and all(summary['checks'].values())
        (evidence/'result.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n')
        if not args.output_dir:
            (REPO/'evidence/latest-plugin-validation.json').write_text(json.dumps({'evidence':str(evidence),'pass':summary['pass'],'live':args.live,'error':summary.get('error')},indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    return 0 if summary['pass'] else 1


if __name__=='__main__':
    raise SystemExit(main())
