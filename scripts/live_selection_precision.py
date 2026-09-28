#!/usr/bin/env python3
"""Compare a prior selected-task pack against current retrieval on a frozen clone.

No live profile/cursor/product changes. Raw sources remain in the private clone;
its transcript registration is removed to freeze the recorded corpus for comparison.
This is real provider selection evidence, not Desktop/fresh-session validation.
"""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys

from release import backup_store
from jcm import config
from jcm.coordinator import dispatch, read_pack
from jcm.store import Store
from jcm.util import digest, encode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--baseline-pack', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--required-source', action='append', default=[])
    parser.add_argument('--excluded-source', action='append', default=[])
    args = parser.parse_args()
    os.umask(0o077)
    root = args.project.resolve(); pointer = root / '.codex/jcm.json'
    pointer_before = pointer.read_bytes(); profile_path = Path(json.loads(pointer_before)['profile_ref'])
    profile_before = profile_path.read_bytes(); profile = json.loads(profile_before)
    output = args.output_dir.resolve(); output.mkdir(parents=True, exist_ok=False)
    home = output / 'storage'; (home / 'profiles').mkdir(parents=True)
    backup_store(Path(profile['home']) / 'stores' / profile['repo_id'], home / 'stores' / profile['repo_id'])
    with sqlite3.connect((Path(profile['home']) / 'registry.sqlite').as_uri() + '?mode=ro', uri=True) as db:
        row = db.execute('SELECT root,repo_id,git_dir,created FROM projects WHERE root=?', (str(root),)).fetchone()
    with config.connect_registry(home) as db:
        db.execute('INSERT INTO projects VALUES (?,?,?,?)', row)
    profile.update(home=str(home), cli_argv=[sys.executable, '-m', 'jcm', '--home', str(home), '--repo', str(root)])
    profile.pop('plugin', None)
    (home / 'profiles' / (profile['repo_id'] + '.json')).write_bytes(encode(profile))
    store = Store(profile)
    result = {'pass': False, 'lane': 'frozen_real_store_clone_real_jev', 'checks': {},
              'capture': 'not_exercised_in_frozen_comparison', 'desktop': 'not_attested'}
    try:
        store.db.execute('DELETE FROM sources')
        baseline = store.blob(store.db.execute('SELECT blob FROM packs WHERE id=?', (args.baseline_pack,)).fetchone()[0])
        if not baseline.get('selected_task'):
            raise ValueError('Baseline must be a selected-task pack')
        raw = [(e['id'], e['revision'], e['blob']) for e in store.events()]
        prior_calls = store.db.execute("SELECT COUNT(*) FROM calls WHERE status='success'").fetchone()[0]
        route = dispatch(store, baseline['request_token'])
        pack = store.blob(store.db.execute('SELECT blob FROM packs WHERE id=?', (route['pack_id'],)).fetchone()[0])
        (output / 'pack.json').write_bytes(encode(pack))
        ids = {r['event_id'] for r in pack['selected_records']}
        task = store.event(baseline['selected_task']['task_id'])
        anchors = {e['id'] for e in store.events() if e['session'] == task['session'] and e['turn'] == task['turn'] and e['role'] in ('user', 'assistant')}
        result['checks'].update(normal=pack['quality'] == 'normal', selected_task_anchors=anchors <= ids,
            required_sources=set(args.required_source) <= ids,
            unrelated_sources_absent=set(args.excluded_source).isdisjoint(ids),
            raw_sources_unchanged=raw == [(e['id'], e['revision'], e['blob']) for e in store.events()])
        for event in store.events():
            store.blob(event['blob'])
        result['checks']['raw_blobs_verified'] = True
        count = len(pack.get('page_manifest', {}).get('pages', [])) or 1
        first = read_pack(store, route['pack_id'])
        for page in range(2, count + 1):
            last = read_pack(store, route['pack_id'], page)
        served = first if count == 1 else last
        result['checks']['all_pages_served'] = served['delivery'] == 'read_served'
        for name, value in [('baseline', baseline), ('candidate', pack)]:
            result[name] = {'selected': len(value['selected_records']), 'excluded': len(value['excluded_records']),
                'source_text_bytes': sum(len(r['text'].encode()) for r in value['selected_records']),
                'pages': len(value.get('page_manifest', {}).get('pages', [])) or 1,
                'content_hash': value.get('page_manifest', {}).get('content_hash', digest(value))}
        result['real_jev_successes'] = store.db.execute("SELECT COUNT(*) FROM calls WHERE status='success'").fetchone()[0] - prior_calls
        result['checks']['real_provider_called'] = result['real_jev_successes'] > 0
        result['checks']['precision_improved'] = result['candidate']['source_text_bytes'] < result['baseline']['source_text_bytes']
        result['completed'] = True
    finally:
        store.change_policy(enabled=False); store.close()
        result['clone_disabled'] = True
        result['checks']['live_profile_unchanged'] = profile_path.read_bytes() == profile_before
        result['checks']['live_pointer_unchanged'] = pointer.read_bytes() == pointer_before
        result['pass'] = result.get('completed', False) and all(result['checks'].values())
        (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
