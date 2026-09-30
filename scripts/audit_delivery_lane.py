#!/usr/bin/env python3
"""Audit a restored lane against explicitly supplied independent data, offline.

Adopts the earlier exact source-span/goal-carrier audit. No fixture paths, answer
values, witness membership or stage counts are selected by this executable.
"""
import argparse
from pathlib import Path
from restore_delivery_archives import absolute, load, require, save, sha, runtime_check


def audit(lane, result_path, plan_path, witness_path, fixture_path):
    from jcm.delivery import reconstruct_pages, apply_delta
    from jcm.representations import validate_contract
    from jcm.source_read import bound_pack
    from jcm.store import Store
    from jcm.util import digest
    paths = [absolute(p) for p in (result_path, plan_path, witness_path, fixture_path)]
    result, plan, witnesses, fixture = [load(p) for p in paths]
    names = [s['name'] for s in result['steps']]
    require(names == [s['name'] for s in fixture['steps']] == [s['name'] for s in plan['steps']], 'Audit stage sequence mismatch')
    rows = []; retained = None
    for index, step in enumerate(result['steps']):
        spec = step['source_binding']; runtime_check(spec['runtime_hash'], spec['runtime_source'])
        profile = absolute(spec['profile']); require(sha(profile) == spec['profile_sha256'], 'Audit profile changed')
        require(all(sha(absolute(p)) == step['page_hashes'][p] for p in step['pages']), 'Audit page changed')
        store = Store(load(profile))
        try:
            pack = bound_pack(store, spec['pack_id']); events = store.events()
            materials = [store.material(e) for e in events]
            snapshot = digest([{'event_id': e['id'], 'revision': e['revision'], 'text_hash': digest(store.material(e)['text'])} for e in events])
            material_hashes = {digest(m['text'].encode()): m for m in materials}
            payload = reconstruct_pages([load(p) for p in step['pages']])
            full = apply_delta(retained, payload) if payload.get('delivery_mode') == 'delta' else payload
            records = {r['event_id']: r for r in full['selected_records']}; expected = plan['steps'][index]
            checks = {'source_count': len(materials) == plan['expected_actual_source_event_counts'][step['name']],
                'snapshot': snapshot == spec['source_snapshot_hash'], 'pack_digest': digest(pack) == spec['pack_digest'],
                'canonical_request': pack['request'] == pack['query_context']['request'] == fixture['steps'][index]['request'] == step['prompt'],
                'request_hashes': all(step['request_hashes'][key] == expected['canonical_request_sha256'] for key in ('planner', 'consumer')),
                'contract': not validate_contract(pack['query_context'], full),
                'expected_unchanged': step['expected'] == fixture['steps'][index]['expected'],
                'reported_mode': step['delivery'] == payload.get('delivery_mode', 'brief'), 'normal_quality': pack['quality'] == 'normal'}
            found = []
            for alias in expected['witness_ids']:
                w = witnesses[alias]; material = material_hashes.get(w['source_text_sha256']); record = records.get(material['event_id']) if material else None
                exact_source = bool(material and material['text'][w['raw_start']:w['raw_end']] == w['raw_text'])
                selected = bool(exact_source and record and any(s['start'] <= w['raw_start'] and s['end'] >= w['raw_end'] for s in record['spans']))
                goal = full.get('task_frame', {}).get('goal', {})
                exact_goal = bool(exact_source and goal.get('event_id') == material['event_id'] and goal.get('revision') == material['revision'] and goal.get('text') == material['text'])
                present = bool(exact_goal or (exact_source and record and w['raw_text'] in record['text']))
                found.append({'witness': alias, 'source_id': material['event_id'] if material else None,
                    'source_found': exact_source, 'required_span_covered': selected or exact_goal, 'exact_raw_text_present': present,
                    'required_view_carrier': 'selected_record' if selected else 'task_frame.goal' if exact_goal else None})
            checks['required_witnesses'] = all(x['source_found'] and x['required_span_covered'] and x['exact_raw_text_present'] for x in found)
            rows.append({'name': step['name'], 'checks': checks, 'witnesses': found, 'source_events': len(materials),
                'required_bytes': sum(Path(p).stat().st_size for p in step['pages']), 'observed_mode': payload.get('delivery_mode', 'brief'),
                'qualification_limits': full.get('task_frame', {}).get('qualification_limits', [])})
            retained = full
        finally:
            store.close()
    return {'lane': lane, 'pass': bool(rows) and all(all(r['checks'].values()) for r in rows), 'rows': rows,
        'witness_checks': sum(len(r['witnesses']) for r in rows), 'input_hashes': {str(p): sha(p) for p in paths},
        'model_visibility': 'NOT_EVALUATED_BY_SOURCE_AUDIT', 'provider_calls_new': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('lane', 'result', 'plan', 'witnesses', 'fixture', 'output'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args(); value = audit(args.lane, args.result, args.plan, args.witnesses, args.fixture)
    save(args.output, value)
    print({'pass': value['pass'], 'witness_checks': value['witness_checks']})
    return 0 if value['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
