#!/usr/bin/env python3
"""Real independent Codex sessions: saved requirements, implementation, correction."""
import argparse
import json
import os
from pathlib import Path
import uuid
from live_entry import Acceptance, sha
from jcm.metrics import delivery_metrics


class ContextReuseAcceptance(Acceptance):
    def session(self, name, root, prompt, resume=None):
        thread, rows = super().session(name, root, prompt, resume)
        self.result['sessions'][-1]['host_reported_usage'] = [r['usage'] for r in rows
            if r.get('type') == 'turn.completed' and isinstance(r.get('usage'), dict)]
        self.save()
        return thread, rows

    def inspect_session(self, root, session, name):
        store = self.store(root)
        try:
            rows = store.db.execute('SELECT p.* FROM packs p JOIN requests r ON p.token=r.token WHERE r.session=? ORDER BY p.rowid', (session,)).fetchall()
            packs = [store.blob(r['blob']) for r in rows]
            complete = [p for p in packs if store.db.execute('SELECT 1 FROM meta WHERE key=?', ('required_read:' + p['pack_id'],)).fetchone()]
            self.check(name + '_required_context_read', bool(complete))
            pack = complete[-1]
            self.check(name + '_normal', pack['quality'] == 'normal')
            self.check(name + '_task_frame', bool(pack.get('task_frame', {}).get('assertions')))
            self.result[name] = {'metrics': pack['metrics'], 'delivery': delivery_metrics(store, pack['pack_id']),
                'task_id': pack['task_frame']['task_id'], 'pack_id': pack['pack_id']}
            self.save()
        finally:
            store.close()

    def run(self):
        root = self.project('reuse-project')
        marker = 'REUSE_' + uuid.uuid4().hex
        before = sha(root / 'connection.py')
        first, _ = self.session('bare', root, self.mention)
        self.verify_entry(root, first, 'awaiting_scope')
        self.session('requirements', root,
            '현재 세션 전체를 포함해 관리해주세요. connection.py 연결 복구 작업의 요구사항입니다. '
            f'입력 paused를 그대로 유지하고 protocol_version=47, continuity_marker={marker!r}, retry_delay_seconds=5를 반환해야 합니다. '
            '지금은 요구사항만 기록하고 코드나 별도 메모 파일을 수정하지 마세요.', first)
        self.verify_entry(root, first, 'ready')
        self.check('recording_kept_product', sha(root / 'connection.py') == before)
        second, _ = self.session('independent-implementation', root,
            '이어서 connection.py 연결 복구를 구현하고 paused=True의 결과를 result.json에 저장해주세요. 이전에 정한 요구사항을 복원해 반영하고 동작을 검증해주세요.')
        self.verify_recovery(root, second, 47, marker)
        value = json.loads((root / 'result.json').read_text())
        self.check('recovered_initial_delay', value.get('retry_delay_seconds') == 5)
        self.inspect_session(root, second, 'implementation')
        third, _ = self.session('independent-correction', root,
            '정정: connection.py 연결 복구의 재시도 간격은 5초 대신 8초로 바꿔주세요. 다른 요구사항은 그대로 유지합니다. '
            '이전 작업 문맥을 복원하고 현재 요청의 정정을 반영해 구현한 뒤 paused=True의 결과를 result.json에 저장하고 동작을 검증해주세요.')
        self.verify_recovery(root, third, 47, marker)
        self.check('current_correction_used', json.loads((root / 'result.json').read_text()).get('retry_delay_seconds') == 8)
        self.inspect_session(root, third, 'correction')
        self.check('three_independent_sessions', len({first, second, third}) == 3)
        self.check('same_task_identity', self.result['implementation']['task_id'] == self.result['correction']['task_id'])
        store = self.store(root)
        try:
            self.result['cumulative_transport'] = [dict(r) for r in store.db.execute(
                'SELECT status,COUNT(*) AS calls,SUM(bytes) AS request_bytes FROM calls GROUP BY status')]
        finally:
            store.close()
        self.check('global_config_unchanged', sha(self.user_home / 'config.toml') == self.global_before)
        self.result['pass'] = True
        self.save()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    case = ContextReuseAcceptance(parser.parse_args().output_dir)
    try:
        case.run()
    except Exception as exc:
        case.result['error'] = str(exc); case.save(); raise
    finally:
        case.cleanup()
    print(json.dumps({'pass': True, 'result': str(case.base / 'result.json')}))


if __name__ == '__main__':
    main()
