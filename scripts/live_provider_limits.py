"""Real Jev synthetic verification: no local quota and server-driven context splitting."""
import json, os, random, string, tempfile
from pathlib import Path
from jcm import config
from jcm.provider import JevProvider, noul
from jcm.store import Store
from jcm.snapshot import snapshot
from jcm.worker import drain
from jcm.util import JCMError, encode

if not os.environ.get('TYPESAFE_API_KEY'):
    raise SystemExit('TYPESAFE_API_KEY unavailable')
base=Path(tempfile.mkdtemp(prefix='jcm-context-live-')).resolve()
root=base/'workspace'; root.mkdir(exist_ok=True)
p=config.enable(base/'private',root)
# Prove legacy budgets cannot reintroduce the removed gate.
p.update(max_daily_calls=1,max_request_bytes=1)
config.atomic_write(base/'private/profiles'/f"{p['repo_id']}.json",encode(p))
s=Store(p)
result={'lane':'real_jev_synthetic_inputs','new_codex_session':False,'runtime_home':str(base/'private'),'checks':{}}
try:
    provider=JevProvider(s)
    state={'source':'This synthetic record describes a calm and safe situation. '*1800}
    answer=provider.evaluate(state,{'safe':noul('Does the source describe a calm situation?')})
    result['checks']['over_80kb_request_succeeded']=s.db.execute('SELECT MAX(bytes) FROM calls').fetchone()[0]>80000
    result['large_request_usage']=answer['response']['usage']
    rng=random.Random(17)
    text='Synthetic identifier archive, not a user requirement:\n'+''.join(rng.choices(string.ascii_letters+string.digits,k=105000))
    eid=s.capture(session='synthetic',turn='1',kind='tool_result',role='tool',payload={'text':text},snapshot=snapshot(root),source_key='synthetic-1',identity='synthetic-1')
    work=drain(s,provider,limit=1)
    result['worker']=work
    result['checks']['adaptive_worker_completed']=work['processed']==1
    rows=list(s.db.execute('SELECT * FROM decisions'))
    fragments=[]
    for row in rows:
        req=s.blob(row['request_blob'])
        if row['status']=='success' and 'source' in req.get('payload',{}).get('state',{}) and isinstance(req['payload']['state']['source'],dict):
            fragments.append(req['payload']['state']['source'])
    fragments.sort(key=lambda part:part['span']['start'])
    result['checks']['source_reconstructed']=''.join(part['text'] for part in fragments)==text
    result['checks']['server_context_error_observed']=any(row['error']=='PROVIDER_CONTEXT_LENGTH_EXCEEDED' for row in rows)
    result['calls']=[dict(row) for row in s.db.execute('SELECT status,count(*) AS count,min(bytes) AS min_bytes,max(bytes) AS max_bytes FROM calls GROUP BY status')]
    result['diagnostics']=[dict(row) for row in s.db.execute('SELECT status,request_id,detail FROM provider_errors')]
    result['checks']['legacy_one_call_limit_ignored']=s.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0]>1
    result['pass']=all(result['checks'].values())
except Exception as e:
    result.update({'error':str(e) if isinstance(e,JCMError) else type(e).__name__,'pass':False})
finally:
    s.close()
    out=Path(__file__).resolve().parents[1]/'evidence/provider-limits-live.json'
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'pass':result.get('pass',False),'checks':result['checks'],'evidence':str(out)},ensure_ascii=False))
raise SystemExit(0 if result.get('pass') else 1)
