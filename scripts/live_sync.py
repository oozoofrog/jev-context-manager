#!/usr/bin/env python3
"""Real CLI skill/status/sync and independent continuation acceptance."""
import argparse
import json
import os
from pathlib import Path
import uuid

from live_entry import Acceptance, sha


class SyncAcceptance(Acceptance):
    def run(self):
        root = self.project('sync-project')
        marker = 'SYNC_' + uuid.uuid4().hex
        protocol = 47
        before_hash = sha(root / 'connection.py')
        first, _ = self.session('bare', root, self.mention)
        self.verify_entry(root, first, 'awaiting_scope')
        self.session('scope-and-requirement', root,
            f'현재 세션 전체를 포함해 관리해주세요. connection.py 연결 복구의 요구사항도 기록해주세요. '
            f'paused 입력값을 그대로 유지하고 protocol_version={protocol}, continuity_marker={marker!r}를 반환해야 합니다. '
            '지금은 요구사항만 기록하고 제품 코드나 별도 메모 파일을 수정하지 마세요.', first)
        self.verify_entry(root, first, 'ready')
        self.check('requirements_did_not_edit_product', sha(root / 'connection.py') == before_hash)
        store = self.store(root)
        try:
            before_status = store.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0]
        finally:
            store.close()
        self.session('natural-status-only', root, 'JCM 상태만 확인해주세요.', first)
        store = self.store(root)
        try:
            self.check('status_only_did_not_call_jev', store.db.execute('SELECT COUNT(*) FROM calls').fetchone()[0] == before_status)
        finally:
            store.close()
        self.session('natural-status-and-sync', root,
            'JCM 상태를 확인하고 빠진 기록을 반영해주세요. 기록 저장과 Jev 판단 상태를 구분해 알려주세요. 제품 파일을 수정하지 마세요.', first)
        store = self.store(root)
        try:
            row = store.db.execute("SELECT value FROM meta WHERE key='sync:last'").fetchone()
            observed = json.loads(row[0]) if row else {}
            self.result['sync'] = observed
            self.check('natural_request_used_sync', bool(row))
            self.check('sync_completed_observed_frontier', observed.get('stage') == 'complete' and observed.get('remaining_at_frontier') == 0)
            self.check('sync_judgment_requested', observed.get('judgment_requested') is True)
            self.check('real_jev_called', store.db.execute("SELECT COUNT(*) FROM calls WHERE status='success'").fetchone()[0] > 0)
        finally:
            store.close()
        self.check('sync_did_not_edit_product', sha(root / 'connection.py') == before_hash)
        second, _ = self.session('independent-continuation', root,
            '이어서 connection.py 연결 복구를 구현하고 paused=True로 재연결한 결과를 result.json에 저장해주세요. 이전에 정한 요구사항을 반영하고 동작을 검증해주세요.')
        self.check('independent_sessions', first != second)
        self.verify_recovery(root, second, protocol, marker)
        self.check('global_config_unchanged', sha(self.user_home / 'config.toml') == self.global_before)
        store = self.store(root)
        try:
            self.result['real_jev_successes'] = store.db.execute("SELECT COUNT(*) FROM calls WHERE status='success'").fetchone()[0]
        finally:
            store.close()
        self.result['pass'] = True; self.save()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    case = SyncAcceptance(args.output_dir)
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
