#!/usr/bin/env python3
"""Cold / paraphrase / correction on a fixed synthetic corpus with real Jev.

Each stage opens a new Store connection. This exercises session identity and saved
state, not an independent Codex process or Desktop consumption; those are separate.
"""
import argparse
import json
import os
from pathlib import Path

from jcm import config
from jcm.coordinator import dispatch, read_pack
from jcm.metrics import delivery_metrics
from jcm.snapshot import snapshot
from jcm.store import Store
from jcm.util import encode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    if not os.environ.get('TYPESAFE_API_KEY'):
        raise SystemExit('TYPESAFE_API_KEY unavailable; no real-provider claim.')
    output = args.output_dir.resolve(); output.mkdir(parents=True, exist_ok=False)
    root = output / 'workspace'; root.mkdir()
    cfg = config.enable(output / 'storage', root)
    store = Store(cfg)
    def capture(store, session, turn, text, role='user'):
        return store.capture(session=session, turn=turn, kind=role + '_message', role=role,
            payload={'text': text}, snapshot=snapshot(root), source_key=session + ':' + turn,
            identity=[session, turn, text])
    sources = {}
    sources['goal'] = capture(store, 'history', 'goal', '네트워크 연결 복구 작업: 연결이 끊겼다가 복구되어도 사용자가 일시정지했다면 paused=true를 유지한다. 자동 재개는 금지한다.')
    sources['delay'] = capture(store, 'history', 'delay', '네트워크 연결 복구의 재시도 간격은 5초로 한다. protocol_version=1은 유지한다.')
    sources['shared'] = capture(store, 'history', 'shared', '다른 기능에서 정한 프로젝트 공통 제약: 모든 네트워크 재시도는 오프라인이면 중단해야 한다. Wi-Fi에 다시 연결되더라도 명시적인 사용자 재개 전까지 paused=true를 유지한다.')
    sources['other'] = capture(store, 'history', 'theme', '별개 설정 화면 테마 작업: 배경을 보라색으로 바꾸고 글자색은 흰색으로 한다.')
    sources['evidence'] = capture(store, 'history', 'report', '네트워크 복구 핸들러 수정 보고. 지난 세션의 단위 테스트는 통과했다고 보고했으나 실기기 재접속 시험은 아직 하지 않았다.\n\n' +
        'LOG: ' + 'retry loop diagnostic tick; ' * 250 + '\n\n예외: 사용자가 직접 일시정지한 경우 연결이 복구되어도 자동으로 재개하면 안 된다.', 'assistant')
    store.close()
    result = {'pass': False, 'lane': 'real_jev_synthetic_three_session_ids',
              'independent_codex_session': False, 'desktop': 'not_attested', 'stages': {}, 'checks': {}}
    try:
        for stage, session, request in (
            ('cold', 'session-a', '네트워크 연결 복구 작업을 이어서 구현해줘.'),
            ('repeat', 'session-b', '이전에 하던 네트워크 재접속 처리 작업을 계속하자.'),
            ('correction', 'session-c', '정정: 네트워크 재접속 재시도 간격은 5초 대신 8초로 바꿔줘. protocol_version=1 및 사용자 일시정지 유지 조건은 그대로야.')):
            store = Store(config.load(cfg['home'], root))
            current = capture(store, session, 'request', request)
            token = store.request(session, current)
            route = dispatch(store, token)
            pack = store.blob(store.db.execute('SELECT blob FROM packs WHERE id=?', (route['pack_id'],)).fetchone()[0])
            (output / (stage + '-pack.json')).write_bytes(encode(pack))
            page = read_pack(store, pack['pack_id'])
            reads = [page]
            while page.get('next_read_command'):
                page = read_pack(store, pack['pack_id'], page['pagination']['page'] + 1)
                reads.append(page)
            (output / (stage + '-reads.json')).write_bytes(encode(reads))
            ids = {r['event_id'] for r in pack['selected_records']}
            assertions = pack['task_frame']['assertions']
            result['stages'][stage] = {'pack_id': pack['pack_id'], 'quality': pack['quality'],
                'task_id': pack['task_frame']['task_id'], 'metrics': pack['metrics'],
                'delivery': delivery_metrics(store, pack['pack_id']),
                'required_context_complete': page['required_context_complete'],
                'required_sources_present': {sources[k] for k in ('goal', 'delay', 'shared', 'evidence')} <= ids,
                'other_task_absent': sources['other'] not in ids,
                'verification_not_promoted': all(a['implementation_status'] == 'not_established' for a in assertions),
                'late_exception_preserved': '자동으로 재개하면 안 된다' in str(pack['context_views']['brief']),
                'brief_reduces_log': len(next((s['text'] for s in pack['context_views']['brief']['selected_records'] if s['event_id'] == sources['evidence']), '')) < 1000,
                'delay_value_states': [a['state'] for a in assertions if a['event_id'] == sources['delay'] and '5초' in a['text']],
                'unaffected_protocol_states': [a['state'] for a in assertions if a['event_id'] == sources['delay'] and 'protocol_version=1' in a['text']],
                'delay_states': [a['state'] for a in assertions if a['event_id'] == sources['delay']],
                'current_update_present': any(a['event_id'] == current for a in assertions)}
            print(json.dumps({'stage': stage, **result['stages'][stage]}, ensure_ascii=False), flush=True)
            store.close()
        a,b,c = (result['stages'][k] for k in ('cold', 'repeat', 'correction'))
        result['checks'] = {
            'normal_all_stages': all(s['quality'] == 'normal' for s in (a,b,c)),
            'recall_all_stages': all(s['required_sources_present'] and s['other_task_absent'] and s['late_exception_preserved'] for s in (a,b,c)),
            'verification_not_promoted': all(s['verification_not_promoted'] for s in (a,b,c)),
            'same_task_identity': a['task_id'] == b['task_id'] == c['task_id'],
            'repeat_no_source_reassessment': b['metrics']['source_units_evaluated'] == 0,
            'repeat_less_transport_bytes': b['metrics']['request_bytes'] < a['metrics']['request_bytes'],
            'current_correction_applied': c['current_update_present'] and bool(c['delay_value_states']) and set(c['delay_value_states']) <= {'disputed','superseded'},
            'brief_reduces_log': all(s['brief_reduces_log'] for s in (a,b,c)),
            'unaffected_protocol_preserved': bool(c['unaffected_protocol_states']) and set(c['unaffected_protocol_states']) == {'active_evidence'},
            'required_delivery_complete': all(s['required_context_complete'] for s in (a,b,c)),
            'audit_not_required': all(s['delivery']['optional_served_bytes'] == 0 for s in (a,b,c)),
            'real_calls_made': a['metrics']['successful_transport_calls'] > 0}
        result['pass'] = all(result['checks'].values())
    finally:
        try:
            store.close()
        except Exception:
            pass
        store = Store(config.load(cfg['home'], root)); store.change_policy(enabled=False); store.close()
        result['fixture_disabled'] = True
        (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'pass': result['pass'], 'checks': result['checks']}, ensure_ascii=False))
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
