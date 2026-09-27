"""Real fresh Astra threads. Synthetic project; no fork, resume or manual checkpoint."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid
from pathlib import Path

from jcm import config
from jcm.store import Store
from jcm.util import digest
from config_guard import remove_fixture_trust


REPOSITORY = Path(__file__).resolve().parents[1]


def main():
    os.umask(0o077)
    if not os.environ.get('TYPESAFE_API_KEY'):
        raise SystemExit('Missing TYPESAFE_API_KEY; live Jev/fresh-session path not run.')
    case = uuid.uuid4().hex[:8]
    base = Path(tempfile.mkdtemp(prefix='jcm-e2e-' + case + '-')).resolve()
    root, home = base / 'workspace', base / 'private-store'
    root.mkdir()
    code = root / 'connection.py'
    code.write_text('def reconnect(paused):\n    return {"connected": True, "paused": False}\n')
    policy = config.enable(home, root)
    installed = config.install_hooks(policy)
    store = Store(policy)
    evidence = REPOSITORY / 'evidence' / ('live-e2e-' + case)
    evidence.mkdir(mode=0o700)
    marker = 'correction-' + uuid.uuid4().hex
    sessions = []
    config_path = Path(os.environ.get('CODEX_HOME', '~/.codex')).expanduser() / 'config.toml'
    config_hash = digest(config_path.read_bytes())
    config_before = config_path.read_bytes()
    (base / 'config-before.toml').write_bytes(config_before)
    original_code_hash = digest(code.read_bytes())
    summary = {'lane': 'real_fresh_codex_cli_threads_real_jev', 'case': case,
               'workspace': str(root), 'home': str(home), 'model': 'gpt-6-astra',
               'installation': installed, 'trust': 'one_off_reviewed_hooks_bypass_not_persisted',
               'sessions': sessions, 'pass': False}

    def run_session(name, prompt, interrupt_after_capture=False):
        before = store.db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
        args = [shutil.which('codex'), 'exec', '--dangerously-bypass-hook-trust',
                '--skip-git-repo-check', '-m', 'gpt-6-astra', '-c', 'model_reasoning_effort="xhigh"',
                '-c', 'features.hooks=true', '-c', 'features.multi_agent=false',
                '-c', 'sandbox_workspace_write.network_access=true',
                '-c', 'projects.' + json.dumps(str(root)) + '.trust_level="trusted"',
                '-s', 'workspace-write', '-C', str(root), '--json', '-']
        # The only bypass applies to previously inspected hook definitions in this
        # invocation. No global config/trust or unrelated project is modified.
        output = (evidence / (name + '.jsonl')).open('w')
        errors = (evidence / (name + '.stderr.log')).open('w')
        process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=output, stderr=errors,
                                   text=True, start_new_session=True)
        process.stdin.write(prompt)
        process.stdin.close()
        started, captured = time.monotonic(), None
        print(json.dumps({'session': name, 'state': 'running'}), flush=True)
        while process.poll() is None:
            if interrupt_after_capture:
                row = store.db.execute("SELECT id,session FROM events WHERE seq>? AND role='user' ORDER BY seq DESC LIMIT 1", (before,)).fetchone()
                if row:
                    captured = dict(row)
                    process.terminate()
                    break
            if time.monotonic() - started > 240:
                process.terminate()
                raise RuntimeError('fresh session timed out: ' + name)
            time.sleep(.1)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        output.close()
        errors.close()
        lines = [json.loads(l) for l in (evidence / (name + '.jsonl')).read_text().splitlines() if l.startswith('{')]
        thread = next((r['thread_id'] for r in lines if r.get('type') == 'thread.started'), None)
        entry = {'name': name, 'thread_id': thread, 'exit_code': process.returncode,
                 'interrupted_after_durable_capture': bool(captured),
                 'captured_event': captured, 'elapsed_seconds': round(time.monotonic() - started, 3)}
        sessions.append(entry)
        print(json.dumps(entry), flush=True)
        if not thread:
            raise RuntimeError('No actual fresh thread created: ' + name)
        if interrupt_after_capture and not captured:
            raise RuntimeError('Latest correction was not captured before interrupt')
        if not interrupt_after_capture and process.returncode != 0:
            raise RuntimeError('Fresh session failed: ' + name)
        return thread

    try:
        a = run_session('A', 'connection.py의 연결 복구 작업입니다. 결과 객체에는 protocol_version: 1을 유지해야 합니다. '
            '지금은 요구사항만 확인하고 코드는 수정하지 마세요. 실제 구현과 result.json 작성은 다음 요청에서 진행합니다.')
        if digest(code.read_bytes()) != original_code_hash or (root / 'result.json').exists():
            raise RuntimeError('A changed the fixture before continuation')
        b = run_session('B', '같은 연결 복구 작업의 교정입니다. 재접속해도 기존 paused 값을 그대로 유지해야 하며 '
            '일시정지를 자동으로 해제하면 안 됩니다. 결과 객체에 continuity_marker: "' + marker +
            '"를 넣어야 합니다. 아직 구현은 시작하지 마세요.', interrupt_after_capture=True)
        correction = sessions[-1]['captured_event']['id']
        job = store.db.execute('SELECT state FROM jobs WHERE event_id=?', (correction,)).fetchone()
        summary['correction_job_before_fresh_session'] = job[0] if job else None
        summary['B_had_no_Stop_at_transition'] = not store.db.execute(
            "SELECT 1 FROM events WHERE session=? AND kind='assistant_final'", (b,)).fetchone()
        c = run_session('C', '이어서 connection.py의 연결 복구를 구현하고, 일시정지 상태에서 다시 연결한 결과를 result.json에 작성해 주세요. '
            '재개 전 상태가 유지되는지도 확인해 주세요.')
        spec = importlib.util.spec_from_file_location('synthetic_connection', code)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        result = json.loads((root / 'result.json').read_text())
        expected = {'connected': True, 'paused': True, 'protocol_version': 1, 'continuity_marker': marker}
        actual = module.reconnect(True)
        resume = module.reconnect(False)
        checks = {key: actual.get(key) == value and result.get(key) == value for key, value in expected.items()}
        checks['running_state_preserved'] = resume.get('paused') is False
        checks['three_distinct_fresh_threads'] = len(set((a, b, c))) == 3
        packs = store.db.execute('SELECT p.* FROM packs p JOIN requests r ON p.token=r.token WHERE r.session=?', (c,)).fetchall()
        read_packs = [p for p in packs if p['delivery'] == 'read_served']
        checks['C_read_served_pack'] = bool(read_packs)
        pack_data = [store.blob(p['blob']) for p in read_packs]
        checks['latest_correction_in_C_pack'] = any(correction in [x['event_id'] for x in p['selected_records']] for p in pack_data)
        checks['older_A_requirement_in_C_pack'] = any(any(x['session'] == a and x['role'] == 'user' for x in p['selected_records']) for p in pack_data)
        checks['real_Jev_normal_C_dispatch'] = any(p['quality'] == 'normal' and p['decisions'] and
                                                  all(d['lane'] == 'real_http' for d in p['decisions']) for p in pack_data)
        current_config = tomllib.loads(config_path.read_text())
        original_config = tomllib.loads(config_before.decode())
        # Some installed hosts persist a newly trusted exec cwd. Track and undo
        # only this generated fixture registration, never overwrite other changes.
        summary['host_added_fixture_trust'] = str(root) in current_config.get('projects', {}) and str(root) not in original_config.get('projects', {})
        if summary['host_added_fixture_trust']:
            summary['config_cleanup'] = remove_fixture_trust(config_path, root, base / 'config-pre-cleanup.toml')
        checks['global_config_preserved_after_fixture_cleanup'] = tomllib.loads(config_path.read_text()) == original_config
        checks['correction_was_pending'] = summary['correction_job_before_fresh_session'] == 'queued'
        checks['no_stop_required_for_B'] = summary['B_had_no_Stop_at_transition']
        summary.update(pass_=all(checks.values()), checks=checks, actual=actual, result=result,
                       receipt_count=store.db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0],
                       provider_calls=store.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0],
                       packs=pack_data)
        summary['pass'] = summary.pop('pass_')
    except Exception as error:
        summary['error'] = str(error)
    finally:
        # The enabled fixture does not keep collecting after this bounded test.
        store.change_policy(enabled=False)
        summary['fixture_disabled_after_test'] = True
        if str(root) not in tomllib.loads(config_before.decode()).get('projects', {}) and str(root) in tomllib.loads(config_path.read_text()).get('projects', {}):
            summary['config_cleanup'] = remove_fixture_trust(config_path, root, base / 'config-pre-cleanup-finally.toml')
        summary['events_by_kind'] = {r[0]: r[1] for r in store.db.execute('SELECT kind,COUNT(*) FROM events GROUP BY kind')}
        summary['queue'] = {r[0]: r[1] for r in store.db.execute('SELECT state,COUNT(*) FROM jobs GROUP BY state')}
        summary['decisions'] = [dict(r) for r in store.db.execute('SELECT id,status,model,usage,error FROM decisions')]
        (evidence / 'result.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        (REPOSITORY / 'evidence/latest-live-e2e.json').write_text(json.dumps({'evidence': str(evidence),
            'pass': summary['pass'], 'checks': summary.get('checks'), 'error': summary.get('error')}, ensure_ascii=False, indent=2))
        store.close()
    print(json.dumps({'pass': summary['pass'], 'checks': summary.get('checks'),
                      'error': summary.get('error'), 'evidence': str(evidence)}, ensure_ascii=False), flush=True)
    return 0 if summary['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
