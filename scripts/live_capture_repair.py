#!/usr/bin/env python3
"""Opt-in recovery of a real store clone; never changes the live profile or cursor."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

from release import backup_store
from jcm import config
from jcm.bootstrap import existing
from jcm.health import capture_health
from jcm.store import Store
from jcm.sync import sync
from jcm.worklist import catalogue


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--session', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--with-jev', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    root = args.project.resolve()
    pointer = root / '.codex/jcm.json'
    pointer_before = pointer.read_bytes()
    reference = json.loads(pointer_before)
    profile_path = Path(reference['profile_ref'])
    profile_before = profile_path.read_bytes()
    profile = json.loads(profile_before)
    if profile['root'] != str(root) or not profile['enabled']:
        raise RuntimeError('Expected an enabled profile for exactly this project')
    source = Path(profile['home']) / 'stores' / profile['repo_id']
    output = args.output_dir.resolve(); output.mkdir(parents=True, exist_ok=False)
    home = output / 'storage'; (home / 'profiles').mkdir(parents=True)
    backup_store(source, home / 'stores' / profile['repo_id'])
    with sqlite3.connect((Path(profile['home']) / 'registry.sqlite').as_uri() + '?mode=ro', uri=True) as db:
        row = db.execute('SELECT root,repo_id,git_dir,created FROM projects WHERE root=?', (str(root),)).fetchone()
    with config.connect_registry(home) as db:
        db.execute('INSERT INTO projects VALUES (?,?,?,?)', row)
    profile.update(home=str(home), cli_argv=[sys.executable, '-m', 'jcm', '--home', str(home), '--repo', str(root)])
    profile.pop('plugin', None)
    (home / 'profiles' / (profile['repo_id'] + '.json')).write_text(json.dumps(profile))
    result = {'pass': False, 'lane': 'real_store_clone', 'source_project': str(root), 'checks': {},
              'live_cursor_modified': False, 'desktop_interaction': 'not_performed'}
    store = Store(profile)
    try:
        result['before'] = capture_health(store)
        before = store.db.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        adoption = existing(store, args.session, install=False, follow=False)
        caught = sync(store, capture_only=True, follow=False)
        result['capture'] = caught
        result['request_status'] = adoption['request_status']
        result['checks']['blocked_offset_advanced'] = any(s['backlog_bytes'] for s in result['before']['sources']) and caught['capture']['state'] == 'caught_up'
        result['checks']['current_request_linked'] = adoption['request_status'] == 'linked'
        count = store.db.execute('SELECT COUNT(*) FROM events').fetchone()[0]
        revision = store.db.execute('SELECT SUM(revision) FROM events').fetchone()[0]
        repeated = sync(store, capture_only=True, follow=False)
        result['checks']['retry_idempotent'] = repeated['new_events'] == 0 and store.db.execute('SELECT SUM(revision) FROM events').fetchone()[0] == revision
        result['new_events'] = count - before
        result['chunk_count'] = len(list((store.path / 'chunks').glob('*')))
        result['checks']['large_blobs_persisted'] = result['chunk_count'] > 0
        # Verify reconstruction of every persisted event, not only manifest existence.
        for event in store.events():
            store.blob(event['blob'])
        result['checks']['all_event_blobs_verified'] = True
        if args.with_jev:
            prior = store.db.execute("SELECT COUNT(*) FROM calls WHERE status='success'").fetchone()[0]
            menu = catalogue(store)
            result['menu'] = {k:menu[k] for k in ('quality', 'gaps', 'decisions')}
            result['menu']['task_count'] = len(menu['tasks'])
            result['real_jev_successes'] = store.db.execute("SELECT COUNT(*) FROM calls WHERE status='success'").fetchone()[0] - prior
            result['checks']['real_jev_menu'] = menu['quality'] == 'normal' and result['real_jev_successes'] > 0
        result['completed'] = True
    finally:
        store.change_policy(enabled=False)
        store.close()
        result['clone_disabled_after_validation'] = True
        result['checks']['live_profile_unchanged'] = profile_path.read_bytes() == profile_before
        result['checks']['live_pointer_unchanged'] = pointer.read_bytes() == pointer_before
        result['pass'] = result.get('completed', False) and all(result['checks'].values())
        (output / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({'pass': result['pass'], 'checks': result['checks'], 'new_events': result.get('new_events'),
                      'real_jev_successes': result.get('real_jev_successes'), 'result': str(output / 'result.json')}))
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
