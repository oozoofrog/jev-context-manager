"""Real old-session adoption followed by real independent new-session recovery."""
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import time
import tomllib
import uuid
from pathlib import Path

from jcm import config
from jcm.follower import follower_status
from jcm.store import Store
from config_guard import remove_fixture_trust

REPOSITORY = Path(__file__).resolve().parents[1]


def main():
    os.umask(0o077)
    if not os.environ.get('TYPESAFE_API_KEY'):
        raise SystemExit('TYPESAFE_API_KEY unavailable; live test not run')
    case = uuid.uuid4().hex[:8]
    base = Path(tempfile.mkdtemp(prefix='jcm-bootstrap-' + case + '-')).resolve()
    root, home = base / 'workspace', base / 'private'
    root.mkdir()
    code = root / 'connection.py'
    code.write_text('def reconnect(paused):\n    return {"connected": True, "paused": False}\n')
    policy = config.enable(home, root)
    store = Store(policy)
    evidence = REPOSITORY / 'evidence' / ('live-bootstrap-' + case)
    evidence.mkdir(mode=0o700)
    cfgpath = Path(os.environ.get('CODEX_HOME', '~/.codex')).expanduser() / 'config.toml'
    original = cfgpath.read_bytes()
    (base / 'config-before.toml').write_bytes(original)
    summary = {'lane': 'real_existing_and_fresh_Astra_CLI_real_Jev', 'workspace': str(root), 'home': str(home),
               'model': 'gpt-6-astra', 'reasoning': 'xhigh', 'sessions': [], 'pass': False,
               'trust': 'vetted_fixture_one_invocation_bypass_not_production_trust',
               'explicit_writable_config_directory': str(root / '.codex'),
               'hooks_absent_before_existing_session': not (root / '.codex/hooks.json').exists()}

    def run(name, prompt):
        args = [shutil.which('codex'), 'exec', '--dangerously-bypass-hook-trust', '--skip-git-repo-check',
                '-m', 'gpt-6-astra', '-c', 'model_reasoning_effort="xhigh"',
                '-c', 'features.hooks=true', '-c', 'features.multi_agent=false',
                '-c', 'sandbox_workspace_write.network_access=true',
                '-c', 'projects.' + json.dumps(str(root)) + '.trust_level="trusted"',
                '-s', 'workspace-write', '--add-dir', str(root / '.codex'),
                '-C', str(root), '--json', '-']
        print(json.dumps({'session': name, 'state': 'running'}), flush=True)
        began = time.monotonic()
        with (evidence / (name + '.jsonl')).open('w') as out, (evidence / (name + '.stderr.log')).open('w') as err:
            p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=out, stderr=err, text=True, start_new_session=True)
            try:
                p.communicate(prompt, timeout=300)
            except subprocess.TimeoutExpired:
                p.terminate()
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
                raise RuntimeError(name + ' timed out')
        lines = [json.loads(l) for l in (evidence / (name + '.jsonl')).read_text().splitlines() if l.startswith('{')]
        thread = next((r['thread_id'] for r in lines if r.get('type') == 'thread.started'), None)
        entry = {'name': name, 'thread_id': thread, 'exit_code': p.returncode,
                 'elapsed_seconds': round(time.monotonic() - began, 2)}
        summary['sessions'].append(entry)
        print(json.dumps(entry), flush=True)
        if p.returncode or not thread:
            raise RuntimeError(name + ' failed')
        return thread, lines

    try:
        command = shlex.join(policy['cli_argv'] + ['bootstrap', 'existing'])
        # Marker is created AFTER adoption, emitted only as ordinary tool output.
        # It is absent from the later fresh prompt and from initial project files.
        prompt = ('connection.py 연결 복구 작업입니다. 최종 결과 객체에는 protocol_version: 1을 유지하고, '
                  '재연결 전 paused 값을 그대로 유지해야 합니다. 지금은 구현하지 마세요. '
                  '이 세션은 JCM hook 설치 전에 시작했습니다. 아래 명령을 먼저 실행해 현재 세션을 편입하고 '
                  '자동 로컬 기록을 켜세요. 현재 세션 ID는 환경에서 자동으로 확인하게 두세요:\n' + command + '\n'
                  '명령 성공 후 별도 도구 호출에서 Python uuid.uuid4().hex로 임의 표식을 생성하여 '
                  'CONTINUITY_MARKER=<표식> 한 줄로 출력하세요. 이는 이후 구현에서 결과 객체의 '
                  'continuity_marker 값으로 반드시 사용할 요구사항입니다. 표식을 파일에 저장하지 마세요. '
                  '다음 요청까지 코드를 바꾸지 말고, 마지막 답변은 준비 완료 한 줄만 쓰세요. '
                  'bootstrap이 반환한 read_command는 이번 준비 요청에서는 실행하지 마세요.')
        old, lines = run('existing', prompt)
        reportrow = store.db.execute('SELECT value FROM meta WHERE key=?', ('bootstrap_existing:' + old,)).fetchone()
        if not reportrow:
            raise RuntimeError('Actual existing session did not adopt itself')
        report = json.loads(reportrow[0])
        summary['existing_bootstrap'] = report
        # Wait for the automatic follower alone, never invoke recovery manually here.
        marker, marker_event = None, None
        for _ in range(100):
            for event in store.events():
                if event['session'] != old or event['role'] != 'tool':
                    continue
                payload = store.blob(event['blob'])
                output = payload.get('public_item', {}).get('aggregated_output', '') or ''
                import re
                found = re.search(r'^CONTINUITY_MARKER=([a-f0-9]{32})$', output, re.M)
                if found:
                    marker, marker_event = found.group(1), event
            if marker:
                break
            time.sleep(.1)
        if not marker:
            raise RuntimeError('Follower did not capture post-adoption tool output')
        summary['post_adoption_marker_event'] = marker_event['id']
        summary['marker_job_before_fresh'] = store.db.execute('SELECT state FROM jobs WHERE event_id=?', (marker_event['id'],)).fetchone()[0]
        summary['old_hook_event_count'] = store.db.execute("SELECT COUNT(*) FROM events WHERE session=? AND role='lifecycle'", (old,)).fetchone()[0]
        summary['events_before_fresh'] = len(store.events())
        if 'protocol_version' in code.read_text() or (root / 'result.json').exists():
            raise RuntimeError('Old session unexpectedly implemented the task')
        fresh, freshlines = run('fresh', '이어서 connection.py의 연결 복구를 구현하고 일시정지 상태에서 재연결한 결과를 result.json에 작성하세요. 현재 파일을 확인하고 동작을 검증하세요.')
        spec = importlib.util.spec_from_file_location('bootstrap_connection', code)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        actual, running = module.reconnect(True), module.reconnect(False)
        result = json.loads((root / 'result.json').read_text())
        rows = store.db.execute('SELECT p.* FROM packs p JOIN requests r ON r.token=p.token WHERE r.session=?', (fresh,)).fetchall()
        packs = [store.blob(r['blob']) for r in rows if r['delivery'] == 'read_served']
        checks = {'existing_started_without_hooks': summary['hooks_absent_before_existing_session'],
                  'existing_self_identity_resolved': report['session_id'] == old,
                  'existing_history_backfilled': report['new_events'] >= 1,
                  'existing_local_follower_running_at_adoption': report['follower']['running'],
                  'post_adoption_tail_automatically_captured': bool(marker_event),
                  'post_adoption_semantics_pending': summary['marker_job_before_fresh'] == 'queued',
                  'fresh_independent_thread': fresh != old,
                  'new_bootstrap_used': 'bootstrap new --request-token' in json.dumps(freshlines),
                  'new_read_served': bool(packs),
                  'old_requirement_in_pack': any(any(x['role'] == 'user' and x['session'] == old for x in p['selected_records']) for p in packs),
                  'post_adoption_tail_in_pack': any(any(x['event_id'] == marker_event['id'] for x in p['selected_records']) for p in packs),
                  'real_Jev_normal_path': any(p['quality'] == 'normal' and p['decisions'] and all(d['lane'] == 'real_http' for d in p['decisions']) for p in packs),
                  'true_paused_preserved': actual.get('paused') is True and result.get('paused') is True,
                  'false_paused_preserved': running.get('paused') is False,
                  'older_protocol_preserved': actual.get('protocol_version') == result.get('protocol_version') == 1,
                  'later_marker_exact': result.get('continuity_marker') == marker}
        summary.update(checks=checks, packs=packs, actual=actual, result=result)
    except Exception as error:
        summary['error'] = str(error)
    finally:
        store.change_policy(enabled=False)
        for _ in range(40):
            if not any(follower_status(store, r[0])['running'] for r in store.db.execute('SELECT key FROM sources')):
                break
            time.sleep(.1)
        summary['fixture_disabled'] = True
        if str(root) not in tomllib.loads(original.decode()).get('projects', {}) and str(root) in tomllib.loads(cfgpath.read_text()).get('projects', {}):
            summary['config_cleanup'] = remove_fixture_trust(cfgpath, root, base / 'config-pre-cleanup.toml')
        summary.setdefault('checks', {})['other_global_config_preserved'] = tomllib.loads(cfgpath.read_text()) == tomllib.loads(original.decode())
        summary['pass'] = not summary.get('error') and all(summary['checks'].values())
        summary['decisions'] = [dict(r) for r in store.db.execute('SELECT id,status,model,usage,error FROM decisions')]
        summary['provider_calls'] = [dict(r) for r in store.db.execute('SELECT * FROM calls')]
        summary['followers_at_end'] = [follower_status(store, r[0]) for r in store.db.execute('SELECT key FROM sources')]
        (evidence / 'result.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        (REPOSITORY / 'evidence/latest-live-bootstrap.json').write_text(json.dumps({'evidence': str(evidence), 'pass': summary['pass'],
                   'checks': summary['checks'], 'error': summary.get('error')}, ensure_ascii=False, indent=2))
        store.close()
    print(json.dumps({'evidence': str(evidence), 'pass': summary['pass'], 'checks': summary['checks'], 'error': summary.get('error')}, ensure_ascii=False), flush=True)
    return 0 if summary['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
