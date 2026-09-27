"""Explicit, bounded real TypeSafe evaluation of synthetic continuity evidence."""
import json
import os
import tempfile
from pathlib import Path

from jcm import config
from jcm.coordinator import dispatch, read_pack
from jcm.provider import JevProvider
from jcm.snapshot import snapshot
from jcm.store import Store


def main():
    if not os.environ.get('TYPESAFE_API_KEY'):
        raise SystemExit('TYPESAFE_API_KEY is unavailable; no live claim made.')
    base = Path(tempfile.mkdtemp(prefix='jcm-live-jev-')).resolve()
    root = base / 'workspace'
    root.mkdir()
    cfg = config.enable(base / 'store', root, allow_egress=True, max_calls=6)
    store = Store(cfg)
    try:
        def capture(session, text):
            return store.capture(session=session, turn=session, kind='user_message', role='user',
                payload={'text': text}, snapshot=snapshot(root), source_key=session, identity=session)
        old = capture('synthetic-A', '연결 복구 작업: 결과 JSON에 protocol_version 1을 유지한다. 일시정지는 자동으로 해제하지 않는다.')
        latest = capture('synthetic-B', '정정: 재접속해도 사용자가 직접 재개하기 전에는 paused=true를 유지한다.')
        current = capture('synthetic-C', '연결이 끊겼다가 다시 연결되는 처리를 이어서 구현해줘.')
        token = store.request('synthetic-C', current)
        route = dispatch(store, token, JevProvider(store))
        content = read_pack(store, route['pack_id'])
        pack = content.get('pack', {})
        preserved = {r['event_id'] for r in pack.get('selected_records', [])}
        decisions = []
        for row in store.db.execute('SELECT * FROM decisions ORDER BY created'):
            data = dict(row)
            data['request'] = store.blob(row['request_blob'])
            data['response'] = store.blob(row['response_blob']) if row['response_blob'] else None
            decisions.append(data)
        result = {'lane': 'real_jev_synthetic_input', 'runtime_home': str(base / 'store'),
                  'pass': route['quality'] == 'normal' and {old, latest} <= preserved,
                  'route': route, 'read': content, 'decisions': decisions,
                  'actual_fresh_codex_session': False}
        target = Path(__file__).resolve().parents[1] / 'evidence/live-jev.json'
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps({'pass': result['pass'], 'quality': route['quality'],
                          'calls': len(decisions), 'evidence': str(target)}, ensure_ascii=False))
        return 0 if result['pass'] else 1
    finally:
        store.change_policy(enabled=False)
        store.close()


if __name__ == '__main__':
    raise SystemExit(main())
