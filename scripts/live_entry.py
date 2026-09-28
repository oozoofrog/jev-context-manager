#!/usr/bin/env python3
"""Opt-in real Codex/Jev acceptance for bare skill entry and independent recovery.

Uses an isolated CODEX_HOME, marketplace, storage and workspaces. It reuses only
existing authentication, model settings and unchanged JCM hook trust hashes.
No hook-trust/sandbox bypass, global installation or user configuration edits.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import tomllib
import uuid

from jcm import config
from jcm.store import Store

REPO = Path(__file__).resolve().parents[1]
SELECTOR = 'jev-context-manager@jcm'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Acceptance:
    def __init__(self, output):
        self.base = output.resolve(); self.base.mkdir(parents=True, exist_ok=False)
        self.user_home = Path(os.environ.get('CODEX_HOME', '~/.codex')).expanduser().resolve()
        self.global_before = sha(self.user_home / 'config.toml')
        self.ch = self.base / 'codex'; self.ch.mkdir()
        self.home = self.base / 'storage'; self.home.mkdir()
        self.market = self.base / 'marketplace'; self.market.mkdir()
        self.env = {**os.environ, 'CODEX_HOME': str(self.ch), 'JCM_HOME': str(self.home)}
        if not self.env.get('TYPESAFE_API_KEY'):
            raise RuntimeError('Real Jev credential is required')
        settings = tomllib.loads((self.user_home / 'config.toml').read_text())
        lines = [k + ' = ' + json.dumps(settings[k]) for k in ('model', 'model_reasoning_effort') if k in settings]
        lines += ['approval_policy = "never"', 'sandbox_mode = "workspace-write"',
                  '[sandbox_workspace_write]', 'network_access = true',
                  'writable_roots = [' + json.dumps(str(self.home)) + ']',
                  '[features]', 'hooks = true', 'multi_agent = false']
        for key, value in settings.get('hooks', {}).get('state', {}).items():
            if key.startswith(SELECTOR + ':'):
                lines.append('[hooks.state.' + json.dumps(key) + ']')
                lines.extend(k + ' = ' + json.dumps(v) for k, v in value.items())
        (self.ch / 'config.toml').write_text('\n'.join(lines) + '\n')
        (self.ch / 'auth.json').symlink_to(self.user_home / 'auth.json')
        shutil.copytree(REPO / 'plugins', self.market / 'plugins')
        shutil.copytree(REPO / '.agents', self.market / '.agents')
        self.result = {'pass': False, 'checks': {}, 'sessions': [], 'fixture': str(self.base),
                       'lane': 'real_cli_and_real_jev', 'desktop': 'not_attested'}
        self.roots = []
        self.call(['codex', 'plugin', 'marketplace', 'add', str(self.market), '--json'], 'marketplace')
        installed = self.call(['codex', 'plugin', 'add', SELECTOR, '--json'], 'installation')
        self.plugin = Path(installed['installedPath'])
        self.launcher = str(self.plugin / 'scripts/jcm')
        self.mention = '[$jev-context-manager:astra-continuity](' + str(self.plugin / 'skills/astra-continuity/SKILL.md') + ')'
        expected = json.loads((REPO / 'plugins/jev-context-manager/runtime-manifest.json').read_text())
        self.check('candidate_payload_matches', all(sha(self.plugin / p) == h for p, h in expected.items()))
        self.save()

    def save(self):
        write(self.base / 'result.json', self.result)

    def check(self, name, condition):
        self.result['checks'][name] = bool(condition); self.save()
        if not condition:
            raise RuntimeError('Acceptance failed: ' + name)

    def call(self, argv, name, root=None):
        result = subprocess.run(argv, env=self.env, cwd=root or self.base, capture_output=True, text=True, timeout=120)
        (self.base / (name + '.stdout.log')).write_text(result.stdout)
        (self.base / (name + '.stderr.log')).write_text(result.stderr)
        if result.returncode:
            raise RuntimeError(name + ' failed; see saved logs')
        return json.loads(result.stdout) if result.stdout.strip() else None

    def session(self, name, root, prompt, resume=None):
        argv = ['codex', 'exec', '--enable', 'hooks', '--disable', 'multi_agent', '--json',
                '--skip-git-repo-check', '-C', str(root), '--add-dir', str(self.home)]
        argv += ['resume', resume, '-'] if resume else ['-']
        (self.base / (name + '.prompt.txt')).write_text(prompt)
        print(json.dumps({'stage': name, 'status': 'running'}), flush=True)
        start = time.monotonic()
        with (self.base / (name + '.jsonl')).open('w') as out, (self.base / (name + '.stderr.log')).open('w') as err:
            proc = subprocess.run(argv, input=prompt, env=self.env, cwd=root, stdout=out, stderr=err, text=True, timeout=900)
        rows = []
        for line in (self.base / (name + '.jsonl')).read_text().splitlines():
            try: rows.append(json.loads(line))
            except ValueError: pass
        thread = next((r.get('thread_id') for r in rows if r.get('type') == 'thread.started'), resume)
        messages = [r['item']['text'] for r in rows if r.get('type') == 'item.completed' and r.get('item', {}).get('type') == 'agent_message']
        if messages:
            (self.base / (name + '.final.md')).write_text(messages[-1] + '\n')
        self.result['sessions'].append({'name': name, 'thread': thread, 'resume': resume,
            'seconds': round(time.monotonic()-start, 2), 'exit_code': proc.returncode})
        self.save()
        if proc.returncode or not thread:
            raise RuntimeError(name + ' did not complete')
        print(json.dumps({'stage': name, 'status': 'completed', 'thread': thread}), flush=True)
        return thread, rows

    def project(self, name):
        root = self.base / name; root.mkdir(); self.roots.append(root)
        subprocess.run(['git', 'init', '-q', str(root)], check=True, capture_output=True)
        (root / 'connection.py').write_text('def reconnect(paused):\n    return {"connected": True, "paused": False}\n')
        (root / 'theme.py').write_text('def color():\n    return "blue"\n')
        return root

    def store(self, root):
        return Store(config.load(self.home, root))

    def verify_entry(self, root, session, stage):
        from jcm.entry import state_path
        state = json.loads(state_path(self.home, root, session).read_text())
        self.check(root.name + '_' + stage, state['stage'] == stage)
        return state

    def stored_text(self, store):
        return '\n'.join(json.dumps(store.blob(p.name), ensure_ascii=False) for p in store.blobs.iterdir())

    def verify_recovery(self, root, session, protocol, marker=None):
        spec = importlib.util.spec_from_file_location('fixture_connection_' + root.name, root / 'connection.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        value = json.loads((root / 'result.json').read_text())
        self.check(root.name + '_paused_true', module.reconnect(True)['paused'] is True)
        self.check(root.name + '_paused_false', module.reconnect(False)['paused'] is False)
        self.check(root.name + '_protocol', value.get('protocol_version') == protocol)
        if marker:
            self.check(root.name + '_marker', value.get('continuity_marker') == marker)
        store = self.store(root)
        try:
            packs = [store.blob(r['blob']) for r in store.db.execute(
                'SELECT p.* FROM packs p JOIN requests r ON p.token=r.token WHERE r.session=? AND p.delivery=?',
                (session, 'read_served'))]
            self.check(root.name + '_independent_pack_read', bool(packs))
            self.check(root.name + '_real_jev_normal', any(p['quality'] == 'normal' and p['decisions'] and
                all(d['lane'] == 'real_http' for d in p['decisions']) for p in packs))
        finally:
            store.close()

    def run(self):
        root = self.project('from-now')
        outside = 'EXCLUDED_' + uuid.uuid4().hex
        protocol = 37
        marker = 'INCLUDED_' + uuid.uuid4().hex
        self.result['oracle'] = {'excluded': outside, 'included': marker, 'protocol': protocol}; self.save()
        initial = {p.name: sha(p) for p in root.iterdir() if p.is_file()}
        old, _ = self.session('now-seed', root, '이 대화의 옛 메모 표식은 ' + outside + '입니다. 아직 작업하지 말고 확인 한 줄만 답해주세요.')
        self.session('now-bare', root, self.mention, old)
        self.verify_entry(root, old, 'awaiting_scope')
        self.check('now_not_registered_before_choice', not (self.home / 'registry.sqlite').exists())
        self.check('now_preview_did_not_edit_product', initial == {p.name: sha(p) for p in root.iterdir() if p.is_file()})
        self.session('now-choice', root, '지금부터 관리해주세요.', old)
        state = self.verify_entry(root, old, 'ready')
        self.check('now_selected_correct_scope', state['choice'] == 'from_invocation')
        self.session('now-requirements', root,
            f'connection.py 연결 복구 작업을 준비합니다. 전달받은 paused 값을 그대로 유지하고 protocol_version은 {protocol}, '
            f'continuity_marker는 {marker!r}로 반환해야 합니다. 아직 구현하거나 파일에 메모하지 말고 요구사항만 확인해주세요.', old)
        self.session('now-other-task', root,
            '별도 테마 작업도 남겨둡니다. theme.py의 color()는 amber를 반환하도록 나중에 바꿔주세요. 지금은 파일을 수정하지 마세요.', old)
        fresh, _ = self.session('now-fresh', root,
            '이어서 connection.py 연결 복구를 구현하고 paused=True로 재연결한 결과를 result.json에 저장해주세요. 현재 파일을 확인하고 동작을 검증해주세요.')
        self.check('now_independent_sessions', old != fresh)
        self.verify_recovery(root, fresh, protocol, marker)
        store = self.store(root)
        try:
            self.check('now_excluded_from_every_blob', outside not in self.stored_text(store))
            self.check('now_new_records_stored', marker in self.stored_text(store))
        finally:
            store.close()
        connection_hash = sha(root / 'connection.py')
        menu, _ = self.session('managed-bare', root, self.mention)
        self.verify_entry(root, menu, 'awaiting_task')
        self.session('managed-select', root, '테마 작업을 이어서 완료해주세요.', menu)
        self.check('selected_task_did_not_rewrite_network', sha(root / 'connection.py') == connection_hash)
        spec = importlib.util.spec_from_file_location('entry_theme', root / 'theme.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        self.check('selected_theme_completed', module.color() == 'amber')
        theme_hash = sha(root / 'theme.py')
        store = self.store(root)
        try:
            packs = [store.blob(r['blob']) for r in store.db.execute('SELECT blob FROM packs')]
            self.check('selected_task_pack_used', any(p.get('selected_task') and p['session_id'] == menu for p in packs))
            scope = store.policy()['capture_scope']
            store.change_policy(enabled=False)
            calls = store.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0]
        finally:
            store.close()
        disabled, _ = self.session('disabled-bare', root, self.mention)
        self.verify_entry(root, disabled, 'disabled')
        store = self.store(root)
        try:
            self.check('disabled_stayed_disabled', not store.policy(require_enabled=False)['enabled'])
            self.check('disabled_no_jev', calls == store.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0])
        finally:
            store.close()
        self.session('disabled-resume', root, '기존 범위 그대로 기록을 재개해주세요.', disabled)
        store = self.store(root)
        try:
            self.check('resume_preserved_scope', store.policy()['capture_scope'] == scope)
        finally:
            store.close()
        self.session('new-task-choice', root, '새 작업을 시작할게요.', disabled)
        self.check('new_task_no_product_edits', sha(root / 'connection.py') == connection_hash and sha(root / 'theme.py') == theme_hash)

        root = self.project('whole-session')
        marker = 'WHOLE_' + uuid.uuid4().hex
        before, _ = self.session('whole-seed', root,
            f'connection.py 연결 복구의 요구사항입니다. paused를 유지하고 protocol_version=11, continuity_marker={marker!r}를 반환해야 합니다. 아직 파일을 수정하지 마세요.')
        self.session('whole-bare', root, self.mention, before)
        self.verify_entry(root, before, 'awaiting_scope')
        self.session('whole-choice', root, '현재 세션 전체를 포함해 관리해주세요.', before)
        self.session('whole-correction', root, '정정합니다. 연결 복구의 protocol_version은 19입니다. 나머지 요구사항은 유지합니다. 아직 구현하지 마세요.', before)
        after, _ = self.session('whole-fresh', root,
            '이어서 connection.py 연결 복구를 구현하고 paused=True로 재연결한 결과를 result.json에 저장해주세요. 현재 파일을 확인하고 동작을 검증해주세요.')
        self.check('whole_independent_sessions', before != after)
        self.verify_recovery(root, after, 19, marker)
        self.check('global_config_unchanged', sha(self.user_home / 'config.toml') == self.global_before)
        self.result['pass'] = True; self.save()

    def cleanup(self):
        for root in self.roots:
            try:
                store = self.store(root)
                store.change_policy(enabled=False); store.close()
            except Exception:
                pass
        self.result['global_config_unchanged'] = sha(self.user_home / 'config.toml') == self.global_before
        self.save()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    case = Acceptance(args.output_dir)
    try:
        case.run()
    except Exception as exc:
        case.result['error'] = str(exc); case.save()
        raise
    finally:
        case.cleanup()
    print(json.dumps({'pass': True, 'result': str(case.base / 'result.json')}))


if __name__ == '__main__':
    main()
