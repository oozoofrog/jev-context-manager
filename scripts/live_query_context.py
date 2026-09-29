#!/usr/bin/env python3
"""Real Jev focus-change validation in an isolated synthetic store.

This validates source selection and delivery, not a downstream answer, Desktop
consumption or an independent Codex session. No live project history is read.
"""
import argparse
import json
import os
from pathlib import Path

from jcm import config
from jcm.coordinator import dispatch, read_pack
from jcm.snapshot import snapshot
from jcm.store import Store
from jcm.util import encode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    if not os.environ.get('TYPESAFE_API_KEY'):
        raise SystemExit('TYPESAFE_API_KEY unavailable; no real-provider claim.')
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    root = output / 'workspace'
    root.mkdir()
    cfg = config.enable(output / 'storage', root)
    store = Store(cfg)
    def capture(session, turn, text, role='user'):
        return store.capture(session=session, turn=turn, kind=role + '_message', role=role,
            payload={'text': text}, snapshot=snapshot(root), source_key=session + ':' + turn,
            identity=[session, turn, text])
    rule = capture('history', 'rule', '네트워크 재접속 처리 작업의 제약: 사용자가 직접 일시정지했으면 paused=true를 유지한다. '
                   '자동 재개는 금지한다. 사용자가 재개 버튼을 직접 누른 경우에만 재개할 수 있다. 재시도 간격은 5초이며 protocol_version=1을 유지한다.')
    trace = capture('history', 'trace', '네트워크 재접속 중 발생한 E42 오류의 진단 기록. 원인은 아직 검증하지 않았다.\n\n'
        'LOG: t=100.010 callback_enter; t=100.012 paused=true; t=100.013 queue_depth=2; '
        't=100.014 reconnect_callback; t=100.015 generation=7; t=100.016 generation=8; '
        't=100.017 old_callback_dequeued; t=100.018 attempt_resume; t=100.019 paused_guard; t=100.020 E42. '
        + 'diagnostic tick; queue inspected; ' * 80 + '\n\n'
        '예외 조건: 사용자가 직접 일시정지했다면 재접속하더라도 자동으로 재개하면 안 된다. 실기기에서 재현 여부는 확인하지 않았다.', 'tool')
    background = capture('history', 'background', '재접속 코드의 설명 자료를 만들 때 참고했던 비유와 도식이다.\n\n'
        '배경 비유: 연결 이벤트를 편지를 나르는 우편배달부에 빗대어 설명할 수 있다. 도식에는 편지 봉투와 우체통 그림을 넣는 예시가 있다. '
        '구현 요구사항이나 채택한 결정은 아니며 설명용 참고 내용이다.', 'assistant')
    result = {'pass': False, 'lane': 'real_jev_synthetic_focus_changes', 'stages': {}, 'checks': {}}
    packs = {}
    try:
        for stage, request in (
            ('resume', '네트워크 재접속 처리 작업을 전체적으로 복원해서 이어서 구현해줘.'),
            ('exception', '같은 네트워크 재접속 작업에서 자동 재개 금지의 예외 조건만 짧게 설명해줘.'),
            ('paraphrase', '네트워크 재접속 처리의 자동 재개 금지 규칙에서 예외로 허용되는 경우만 간결하게 알려줘.'),
            ('failure', '같은 네트워크 재접속 작업의 E42 오류를 상세 분석해줘. callback 순서와 시각, generation 값을 포함한 진단 로그 원문 전체가 필요해.'),
            ('correction', '정정: 네트워크 재접속 재시도 간격은 5초 대신 8초로 한다. protocol_version=1과 사용자 일시정지 유지 조건은 그대로다. 자동 재개 예외만 다시 설명해줘.')):
            current = capture('session-' + stage, 'request', request)
            route = dispatch(store, store.request('session-' + stage, current))
            pack = store.blob(store.db.execute('SELECT blob FROM packs WHERE id=?', (route['pack_id'],)).fetchone()[0])
            packs[stage] = pack
            (output / (stage + '-pack.json')).write_bytes(encode(pack))
            page = read_pack(store, pack['pack_id'])
            reads = [page]
            while page.get('next_read_command'):
                page = read_pack(store, pack['pack_id'], page['pagination']['page'] + 1)
                reads.append(page)
            (output / (stage + '-reads.json')).write_bytes(encode(reads))
            required = {s['event_id']: s for s in pack['context_views']['brief']['selected_records']}
            result['stages'][stage] = {'quality': pack['quality'], 'task_id': pack['task_frame']['task_id'],
                'query_id': pack['query_context']['id'], 'query_route': pack['query_context']['route'],
                'trace': required.get(trace), 'background_in_required': background in required,
                'rule_in_required': rule in required, 'metrics': pack['metrics'],
                'required_context_complete': page['required_context_complete']}
            print(json.dumps({'stage': stage, 'quality': pack['quality'], 'query_route': pack['query_context']['route'],
                'trace_level': required.get(trace, {}).get('representation'),
                'trace_chars': len(required.get(trace, {}).get('text', '')),
                'query_evaluated': pack['metrics']['query_units_evaluated'],
                'http_calls': pack['metrics']['transport_calls']}, ensure_ascii=False), flush=True)
        resume, exception, paraphrase, failure, correction = (packs[k] for k in packs)
        stages = result['stages']
        corrected = correction['context_views']['brief']
        result['checks'] = {
            'all_normal': all(p['quality'] == 'normal' for p in packs.values()),
            'same_task': len({p['task_frame']['task_id'] for p in packs.values()}) == 1,
            'required_rule_retained': all(s['rule_in_required'] for s in stages.values()),
            'exception_omits_background': not stages['exception']['background_in_required'],
            'failure_expands_trace': len(stages['failure']['trace']['text']) > len(stages['exception']['trace']['text']),
            'failure_has_exact_trace': 't=100.017 old_callback_dequeued' in stages['failure']['trace']['text'],
            'equivalent_question_reused': exception['query_context']['id'] == paraphrase['query_context']['id'],
            'paraphrase_no_source_query_reassessment': paraphrase['metrics']['query_units_evaluated'] == 0,
            'focus_change_keeps_membership_cache': exception['metrics']['source_units_evaluated'] == 0,
            'correction_and_protocol_retained': '8초' in str(corrected) and 'protocol_version=1' in str(corrected),
            'old_delay_disputed': any(a['event_id'] == rule and '5초' in a['text'] and a['state'] in ('disputed', 'superseded') for a in correction['task_frame']['assertions']),
            'verification_not_promoted': all(a['implementation_status'] == 'not_established' for p in packs.values() for a in p['task_frame']['assertions']),
            'required_delivery_complete': all(s['required_context_complete'] for s in stages.values()),
        }
        result['pass'] = all(result['checks'].values())
    finally:
        store.change_policy(enabled=False)
        store.close()
        result['fixture_disabled'] = True
        (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'pass': result['pass'], 'checks': result['checks']}, ensure_ascii=False))
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
