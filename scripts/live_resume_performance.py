#!/usr/bin/env python3
"""Compare warm resume and A/B/A+delta on a frozen copy of an enabled store.

The optional baseline tree is a source snapshot with src/jcm (for example a
released Git archive). Each dispatch runs in its own Python process. This is
real-provider recovery evidence, not an independent Codex or Desktop UI test.
Raw packs stay in the private output directory; result.json contains aggregates.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time


def run_dispatch(profile_file, token, output):
    from jcm.coordinator import dispatch, read_pack
    from jcm.metrics import delivery_metrics
    from jcm.store import Store
    from jcm.util import encode, digest
    store = Store(json.loads(Path(profile_file).read_text()))
    try:
        decision_frontier = store.db.execute('SELECT COALESCE(MAX(rowid),0) FROM decisions').fetchone()[0]
        started = time.perf_counter()
        route = dispatch(store, token)
        elapsed = time.perf_counter() - started
        pack = store.blob(store.db.execute('SELECT blob FROM packs WHERE id=?', (route['pack_id'],)).fetchone()[0])
        Path(str(output) + '.pack.json').write_bytes(encode(pack))
        last = read_pack(store, pack['pack_id'])
        while last.get('next_read_command'):
            last = read_pack(store, pack['pack_id'], last['pagination']['page'] + 1)
        transmitted = set()
        for row in store.db.execute('SELECT request_blob FROM decisions WHERE rowid>?', (decision_frontier,)):
            payload = store.blob(row[0]).get('payload', {})
            if any(q.endswith('_relevance') for q in payload.get('questions', {})):
                transmitted.update((i['event_id'], i['revision']) for i in payload['state']['items'])
        result = {'transmitted_membership_sources': sorted(transmitted), 'elapsed_seconds': elapsed, 'quality': pack['quality'], 'dispatch': pack['dispatch'],
                  'task_id': pack['task_frame']['task_id'], 'metrics': pack['metrics'],
                  'delivery': delivery_metrics(store, pack['pack_id']),
                  'required_complete': last['required_context_complete'],
                  'selected_ids': [s['event_id'] for s in pack['selected_records']],
                  'task_frame_hash': digest(pack['task_frame']),
                  'brief_evidence_hash': digest(pack['context_views']['brief']['selected_records']),
                  'correction_marker_in_brief': any('READY-REVIEW-83' in s['text'] for s in pack['context_views']['brief']['selected_records'])}
        Path(output).write_bytes(encode(result))
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--baseline-pack', required=True)
    parser.add_argument('--baseline-tree', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--required-source', action='append', default=[])
    args = parser.parse_args()
    from release import backup_store
    from jcm import config
    from jcm.store import Store
    from jcm.util import encode, digest
    from jcm.snapshot import snapshot
    os.umask(0o077)
    root = args.project.resolve()
    pointer = root / '.codex/jcm.json'; pointer_before = pointer.read_bytes()
    profile_file = Path(json.loads(pointer_before)['profile_ref']); profile_before = profile_file.read_bytes()
    profile = json.loads(profile_before)
    if not profile['enabled']:
        raise ValueError('Benchmark requires an enabled snapshot; do not restore an obsolete policy epoch.')
    output = args.output_dir.resolve(); output.mkdir(parents=True, exist_ok=False)
    home = output / 'storage'; (home / 'profiles').mkdir(parents=True)
    backup_store(Path(profile['home']) / 'stores' / profile['repo_id'], home / 'stores' / profile['repo_id'])
    with sqlite3.connect((Path(profile['home']) / 'registry.sqlite').as_uri() + '?mode=ro', uri=True) as db:
        registry = db.execute('SELECT * FROM projects WHERE root=?', (str(root),)).fetchone()
    with config.connect_registry(home) as db:
        db.execute('INSERT INTO projects VALUES (?,?,?,?)', registry)
    profile.update(home=str(home), cli_argv=[sys.executable, '-m', 'jcm', '--home', str(home), '--repo', str(root)],
                   transcript_roots=[])
    profile.pop('plugin', None)
    frozen_profile = home / 'profiles' / (profile['repo_id'] + '.json')
    frozen_profile.write_bytes(encode(profile))
    store = Store(profile)
    result = {'pass': False, 'lane': 'frozen_history_real_jev_independent_dispatch_processes',
              'phases': {}, 'checks': {}}
    original_status = subprocess.check_output(['git', '-C', str(root), 'status', '--short'], text=True)
    try:
        store.db.execute('DELETE FROM sources')
        baseline = store.blob(store.db.execute('SELECT blob FROM packs WHERE id=?', (args.baseline_pack,)).fetchone()[0])
        if not baseline.get('selected_task'):
            raise ValueError('Baseline must have an explicit task selection')
        originals = [(e['id'], e['revision'], e['blob']) for e in store.events()]
        original_ids = {e[0] for e in originals}
        required = set(args.required_source)
        tree = Path(__file__).resolve().parents[1]

        def measure(name, token, source_tree=tree):
            env = dict(os.environ, PYTHONPATH=str(source_tree / 'src'))
            dest = output / (name + '.json')
            with (output / (name + '.log')).open('w') as log:
                subprocess.run([sys.executable, str(Path(__file__).resolve()), '_dispatch',
                    str(frozen_profile), token, str(dest)], check=True, env=env, stdout=log, stderr=subprocess.STDOUT)
            r = json.loads(dest.read_text()); result['phases'][name] = r
            print(json.dumps({'phase': name, 'seconds': r['elapsed_seconds'], 'calls': r['metrics']['transport_calls'],
                              'source_evaluated': r['metrics']['source_units_evaluated'], 'quality': r['quality']}), flush=True)
            return r

        # Keep original request and corpus identical for baseline/candidate timings.
        if args.baseline_tree:
            measure('baseline_warm', baseline['request_token'], args.baseline_tree.resolve())
        a1 = measure('candidate_warm', baseline['request_token'])
        a2 = measure('candidate_repeat', baseline['request_token'])
        result['checks']['same_warm_sources'] = set(a1['selected_ids']) == set(a2['selected_ids']) == {s['event_id'] for s in baseline['selected_records']}
        result['checks']['warm_no_http'] = a1['metrics']['transport_calls'] == a2['metrics']['transport_calls'] == 0
        if args.baseline_tree:
            before = result['phases']['baseline_warm']
            result['checks']['faster_same_input'] = max(a1['elapsed_seconds'], a2['elapsed_seconds']) < before['elapsed_seconds']
            result['checks']['baseline_same_sources'] = set(before['selected_ids']) == set(a1['selected_ids'])
            result['checks']['same_task_frame_and_brief'] = all(before[k] == a1[k] == a2[k] for k in ('task_frame_hash', 'brief_evidence_hash'))

        def capture(turn, text):
            return store.capture(session='resume-performance-fixture', turn=turn, kind='user_message', role='user',
                payload={'text': text}, snapshot=snapshot(root), source_key='performance:' + turn,
                identity=['performance', turn, text])

        def selection(anchor, turn):
            event = capture(turn, '이전에 제시된 작업을 선택해 기록을 복원해 주세요.')
            token = store.request('resume-performance-fixture', event)
            # Test dispatch selection, not menu admission or fabricated host turns.
            store.db.execute('INSERT INTO meta VALUES (?,?)', ('entry_control:' + event, 'true'))
            store.db.execute('INSERT INTO meta VALUES (?,?)', ('entry_selection:' + token,
                encode({'task_id': anchor, 'entry_id': 'frozen-performance-fixture'}).decode()))
            return token

        b = capture('task-b', '독립 작업 B: CSV 내보내기. 결과 파일의 구분자는 세미콜론이고 파일명은 archive.csv다. 준비 동작이나 Blender 애니메이션과 무관한 별도 작업이다.')
        b1 = measure('switch_b_cold', selection(b, 'select-b'))
        a3 = measure('return_a', selection(baseline['selected_task']['task_id'], 'return-a'))
        correction = capture('correction-a', '준비 동작 작업의 문서 요구를 정정합니다. 클립 수와 기존 동작 시간은 유지하고, 문서에 검토 식별자 READY-REVIEW-83을 반드시 표시해야 합니다. 이 요구의 구현이나 검증이 완료됐다는 뜻은 아닙니다.')
        a4 = measure('a_after_correction', selection(baseline['selected_task']['task_id'], 'select-a-correction'))
        result['checks']['distinct_task_b'] = b1['task_id'] != a1['task_id'] and b in b1['selected_ids']
        result['checks']['returned_to_same_a'] = a3['task_id'] == a4['task_id'] == a1['task_id']
        result['checks']['required_sources_retained'] = all(required <= set(p['selected_ids']) for p in (a1, a2, a3, a4))
        result['checks']['unrelated_b_excluded_from_a'] = b not in a3['selected_ids'] and b not in a4['selected_ids']
        result['checks']['new_correction_retained'] = correction in a4['selected_ids'] and a4['correction_marker_in_brief']
        result['checks']['old_sources_not_reassessed'] = all(p['metrics']['source_units_reused'] >= a1['metrics']['source_units_reused'] for p in (a3, a4))
        known = {(d['event_id'], d['revision']) for d in baseline['source_dependencies']}
        result['checks']['no_old_membership_source_retransmission'] = not (known & {tuple(d) for d in a3['transmitted_membership_sources']})
        result['checks']['correction_only_membership_delta'] = {d[0] for d in a4['transmitted_membership_sources']} == {correction} and a4['metrics']['source_units_evaluated'] == 1
        result['measurement_note'] = ('Warm baseline and candidate share the original request/input frontier. A new selection turn also admits previously excluded later output from the original turn; those are legitimate new source units on return_a, not cache invalidation. Source retransmission checks use exact event/revision pairs.')
        result['checks']['all_normal_ready_read'] = all(p['quality'] == 'normal' and p['dispatch'] == 'ready' and p['required_complete'] for p in result['phases'].values())
        result['checks']['no_optional_reads'] = all(p['delivery']['optional_served_bytes'] == p['delivery']['source_expansion_bytes'] == 0 for p in result['phases'].values())
        result['checks']['original_sources_unchanged'] = originals == [(e['id'], e['revision'], e['blob']) for e in store.events() if e['id'] in original_ids]
        # Make selected evidence equality reproducible without publishing texts/IDs.
        for phase in result['phases'].values():
            sources = phase.pop('transmitted_membership_sources'); phase['membership_sources_transmitted'] = len(sources)
            ids = phase.pop('selected_ids'); phase['selected_count'] = len(ids); phase['selected_set_hash'] = digest(sorted(ids))
        result['completed'] = True
    finally:
        store.change_policy(enabled=False); store.close(); result['fixture_disabled'] = True
        result['checks']['live_profile_unchanged'] = profile_file.read_bytes() == profile_before
        result['checks']['live_pointer_unchanged'] = pointer.read_bytes() == pointer_before
        result['checks']['product_status_unchanged'] = original_status == subprocess.check_output(['git', '-C', str(root), 'status', '--short'], text=True)
        result['pass'] = result.get('completed', False) and all(result['checks'].values())
        (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '_dispatch':
        run_dispatch(*sys.argv[2:])
    else:
        raise SystemExit(main())
