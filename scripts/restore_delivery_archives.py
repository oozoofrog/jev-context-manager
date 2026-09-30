#!/usr/bin/env python3
"""Inspect and clone exact prepared archives without dispatch or policy re-enable.

Historical relative references use the single explicit --base. All new paths
are absolute. The case's independent expected values are carried unchanged; this
tool does not choose them. Restore/case/close require a completed frozen writer
gate. Run one lane per interpreter with that lane's runtime on PYTHONPATH.
"""
import argparse
from contextlib import closing
import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def absolute(path):
    require(isinstance(path, (str, Path)) and Path(path).is_absolute(), 'Explicit absolute path required')
    return Path(path).resolve()


def reference(path, base):
    return (Path(path) if Path(path).is_absolute() else absolute(base) / path).resolve()


def save(path, value):
    path = absolute(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def connect(path):
    db = sqlite3.connect(absolute(path).as_uri() + '?mode=ro', uri=True)
    db.execute('PRAGMA query_only=ON')
    return db


def table_hashes(db, names=None):
    names = names or [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    def binary(value):
        if isinstance(value, bytes):
            return {'sqlite_blob_hex': value.hex()}
        raise TypeError(type(value).__name__)
    values = {}
    for name in names:
        require(bool(re.fullmatch(r'[A-Za-z0-9_]+', name)), 'Unsafe table name')
        rows = [tuple(r) for r in db.execute('SELECT * FROM ' + name + ' ORDER BY rowid')]
        data = json.dumps(rows, ensure_ascii=False, separators=(',', ':'), default=binary).encode()
        values[name] = {'rows': len(rows), 'sha256': hashlib.sha256(data).hexdigest()}
    return values


def blob_hashes(store):
    values = {}
    for kind in ('blobs', 'chunks'):
        source = store / kind
        require(not source.is_symlink(), 'Symlinked source storage')
        for path in source.rglob('*'):
            require(not path.is_symlink(), 'Symlinked source storage')
            if path.is_file():
                values[str(path.relative_to(store))] = sha(path)
    return values


def gate_check(path):
    gate = load(absolute(path))
    require(gate.get('pass') is True and gate.get('writer_turn_completed') is True, 'Writer completion gate required')
    manifest_path = absolute(gate['manifest_path'])
    require(sha(manifest_path) == gate['manifest_sha256'], 'Ready manifest changed')
    manifest = load(manifest_path)
    require(manifest.get('writing_stopped') is True, 'Writer is not frozen')
    files = manifest['artifact_files']
    require(files and str(Path(__file__).resolve()) in files, 'Current executable is not bound by ready manifest')
    for file, expected in files.items():
        require(sha(absolute(file)) == expected, 'Frozen executable/test changed: ' + file)
    return manifest


def inventory(case_path, closure_path, results, base):
    base = absolute(base)
    inputs = [absolute(case_path), absolute(closure_path), *[absolute(p) for p in results.values()]]
    case = load(inputs[0]); closure = load(inputs[1]); lanes = {lane: load(path) for lane, path in results.items()}
    rows = []; names = [s['name'] for s in case['steps']]
    require(names and len(set(names)) == len(names) and all(re.fullmatch(r'[A-Za-z0-9_-]+', n) for n in names), 'Invalid stage names')
    for lane in ('baseline', 'candidate'):
        require([s['name'] for s in lanes[lane]['steps']] == names, 'Prepared stage sequence mismatch')
        for step, saved in zip(case['steps'], lanes[lane]['steps']):
            require(saved['prompt'] == step['prompt'] and saved['expected'] == step['expected'], 'Saved canonical request/answer changed')
            binding = step['source_bindings'][lane]
            profile = reference(binding['profile'], base)
            archives = [r for r in closure['profiles'] if reference(r['profile'], base) == profile]
            require(len(archives) == 1, 'Missing/ambiguous at-run archive')
            archived = archives[0]; cfg_path = reference(archived['pre_disable_profile'], base)
            journal = reference(archived['pre_disable_journal'], base); cfg = load(cfg_path)
            require(sha(cfg_path) == binding['profile_sha256'] == archived['profile_at_run_sha256'], 'At-run profile changed')
            require(sha(journal) == archived['pre_disable_journal_sha256'], 'At-run journal changed')
            require(cfg['enabled'] is True and type(cfg['epoch']) is int, 'Original enabled policy required')
            home = absolute(cfg['home']); absolute(cfg['root'])
            require(load(profile)['enabled'] is False, 'Historical live profile must remain disabled')
            store = home / 'stores' / cfg['repo_id']; registry = home / 'registry.sqlite'
            with closing(connect(registry)) as db:
                found = db.execute('SELECT repo_id FROM projects WHERE root=?', (cfg['root'],)).fetchone()
                require(found and found[0] == cfg['repo_id'], 'Registry identity mismatch')
                registry_tables = table_hashes(db)
            with closing(connect(journal)) as db:
                tables = table_hashes(db)
                pack = db.execute('SELECT token,epoch,invalid FROM packs WHERE id=?', (binding['pack_id'],)).fetchone()
                require(pack and pack[1] == cfg['epoch'] and pack[2] == 0, 'At-run pack/policy invalid')
                request = db.execute('SELECT event_id,epoch FROM requests WHERE token=?', (pack[0],)).fetchone()
                require(request and request[1] == cfg['epoch'], 'At-run request invalid')
            pages = {str(reference(p, base)): step['page_hashes'][lane][p] for p in step[lane + '_pages']}
            require(set(pages) == {str(reference(p, base)) for p in step[lane + '_pages']}, 'Incomplete page binding')
            require(all(sha(p) == h for p, h in pages.items()), 'Required page changed')
            require(all(step['request_hashes'][k] == hashlib.sha256(step['prompt'].encode()).hexdigest() for k in ('planner', 'consumer')), 'Canonical request hash changed')
            rows.append({'lane': lane, 'stage': step['name'], 'archive_profile': str(cfg_path),
                'archive_profile_sha256': sha(cfg_path), 'archive_journal': str(journal), 'archive_journal_sha256': sha(journal),
                'original_live_profile': str(profile), 'original_live_profile_sha256': sha(profile),
                'registry': str(registry), 'registry_tables': registry_tables, 'tables': tables, 'blob_hashes': blob_hashes(store),
                'source_binding': {**binding, 'runtime_source': str(reference(binding['runtime_source'], base))},
                'pages': pages, 'request_token': pack[0], 'request_event_id': request[0]})
    return {'version': 1, 'pass': True, 'base': str(base), 'case': str(inputs[0]),
        'lane_results': {lane: str(absolute(p)) for lane, p in results.items()},
        'input_hashes': {str(p): sha(p) for p in inputs}, 'rows': rows, 'provider_calls_new': 0}


def runtime_check(expected, path):
    import jcm
    from compare_host_delivery import runtime_hash
    path = absolute(path)
    require(Path(jcm.__file__).resolve().parent == path / 'jcm', 'Wrong interpreter runtime')
    require(runtime_hash(path) == expected, 'Runtime changed')


def restore_lane(preflight_path, gate_path, lane, output, owned_root):
    gate_check(gate_path)
    preflight_path = absolute(preflight_path); preflight = load(preflight_path)
    require(preflight.get('pass') is True, 'Archive inventory did not pass')
    for p, h in preflight['input_hashes'].items():
        require(sha(absolute(p)) == h, 'Archive input changed')
    output = absolute(output); owned = absolute(owned_root)
    require(output != owned and output.is_relative_to(owned), 'Clone output must be within explicit owned root')
    for p in preflight['input_hashes']:
        require(not absolute(p).is_relative_to(output), 'Clone output contains source input')
    raw = load(preflight['case']); saved = load(preflight['lane_results'][lane])
    rows = [r for r in preflight['rows'] if r['lane'] == lane]
    require([r['stage'] for r in rows] == [s['name'] for s in raw['steps']], 'Archive sequence changed')
    runtime = rows[0]['source_binding']['runtime_source']; expected_runtime = rows[0]['source_binding']['runtime_hash']
    runtime_check(expected_runtime, runtime)
    from jcm.delivery import apply_delta, reconstruct_pages
    from jcm.representations import validate_contract
    from jcm.source_read import bound_pack
    from jcm.store import Store
    from jcm.util import digest
    output.mkdir(parents=True, exist_ok=False); retained = None; steps = []; proofs = []
    try:
        for row, source_step, saved_step in zip(rows, raw['steps'], saved['steps']):
            for key in ('archive_profile', 'archive_journal', 'original_live_profile'):
                require(sha(row[key]) == row[key + '_sha256'], 'Archive/live input changed: ' + key)
            cfg = load(row['archive_profile']); original_home = absolute(cfg['home'])
            original_store = original_home / 'stores' / cfg['repo_id']
            require(blob_hashes(original_store) == row['blob_hashes'], 'Source blobs changed')
            require(all(sha(p) == h for p, h in row['pages'].items()), 'Source pages changed')
            stage = output / row['stage']; home = stage / 'storage'; store_path = home / 'stores' / cfg['repo_id']
            profile = home / 'profiles' / (cfg['repo_id'] + '.json')
            profile.parent.mkdir(parents=True); store_path.mkdir(parents=True)
            for rel, expected in row['blob_hashes'].items():
                dest = store_path / rel; dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original_store / rel, dest)
                require(sha(dest) == expected, 'Copied blob changed')
            shutil.copy2(row['archive_journal'], store_path / 'journal.sqlite')
            with closing(connect(row['registry'])) as src, closing(sqlite3.connect(home / 'registry.sqlite')) as dst:
                require(table_hashes(src) == row['registry_tables'], 'Source registry changed')
                src.backup(dst)
            relocated = copy.deepcopy(cfg); relocated['home'] = str(home)
            relocated['cli_argv'] = [str(home) if word == str(original_home) else word for word in cfg['cli_argv']]
            save(profile, relocated); store = Store(relocated)
            try:
                require(table_hashes(store.db) == row['tables'], 'Restored journal changed')
                policy = store.policy(cfg['epoch'])
                require(policy['enabled'] is True and policy['epoch'] == cfg['epoch'] and policy['model'] == cfg['model'], 'Restored policy changed')
                binding = row['source_binding']; pack = bound_pack(store, binding['pack_id'])
                require(digest(pack) == binding['pack_digest'], 'Restored pack changed')
                events = store.events(); snapshot = digest([{'event_id': e['id'], 'revision': e['revision'], 'text_hash': digest(store.material(e)['text'])} for e in events])
                require(snapshot == binding['source_snapshot_hash'], 'Restored source frontier changed')
                require(pack['request'] == pack['query_context']['request'] == source_step['prompt'] == saved_step['prompt'], 'Restored request changed')
                request = store.db.execute('SELECT event_id,epoch FROM requests WHERE token=?', (row['request_token'],)).fetchone()
                require(request and request['event_id'] == row['request_event_id'] and request['epoch'] == cfg['epoch'], 'Restored request identity changed')
                pages = []
                for number, (old, expected) in enumerate(row['pages'].items(), 1):
                    dest = stage / ('required-' + str(number) + '.json'); shutil.copy2(old, dest)
                    require(sha(dest) == expected, 'Copied page changed'); pages.append(str(dest))
                payload = reconstruct_pages([load(p) for p in pages])
                full = apply_delta(retained, payload) if payload.get('delivery_mode') == 'delta' else payload
                require(not validate_contract(pack['query_context'], full), 'Restored delivery contract invalid')
                require(full == {k: v for k, v in pack['context_views']['brief'].items() if k != 'page_manifest'}, 'Restored full/delta changed')
                require(table_hashes(store.db) == row['tables'], 'Restore read mutated journal')
                retained = full
                new_binding = {**binding, 'profile': str(profile), 'profile_sha256': sha(profile), 'runtime_source': runtime}
                step = copy.deepcopy(saved_step)
                step.update(source_binding=new_binding, pages=pages, page_hashes={p: sha(p) for p in pages}, incremental_provider_calls=0)
                steps.append(step)
                proofs.append({'stage': row['stage'], 'source_events': len(events), 'tables': row['tables'],
                    'profile': str(profile), 'relocated_profile_fields': ['home', 'cli_argv'], 'source_snapshot_hash': snapshot,
                    'pack_digest': digest(pack), 'blob_hashes': row['blob_hashes'], 'full_delta_equal_original': True})
            finally:
                store.close()
        for p, h in preflight['input_hashes'].items():
            require(sha(p) == h, 'Archive input changed during restoration')
        for row in rows:
            for key in ('archive_profile', 'archive_journal', 'original_live_profile'):
                require(sha(row[key]) == row[key + '_sha256'], 'Archive input changed during restoration')
            cfg = load(row['archive_profile'])
            require(blob_hashes(absolute(cfg['home']) / 'stores' / cfg['repo_id']) == row['blob_hashes'], 'Source blobs changed during restoration')
            with closing(connect(row['registry'])) as db:
                require(table_hashes(db) == row['registry_tables'], 'Registry changed during restoration')
        result = {'pass': True, 'lane': lane, 'runtime_hash': expected_runtime, 'steps': steps,
            'prepared_stages_reused': len(steps), 'preparation_calls_new': 0}
        save(output / 'result.json', result)
        save(output / 'restore-proof.json', {'pass': True, 'lane': lane, 'proofs': proofs,
            'inventory_sha256': sha(preflight_path), 'source_archive_not_mutated': True, 'provider_calls_new': 0})
        return result
    except Exception as error:
        save(output / 'restore-failure.json', {'pass': False, 'completed_stages': len(steps), 'error': str(error), 'provider_calls_new': 0})
        raise


def derive_case(original, baseline, candidate, gate_path, base):
    manifest = gate_check(gate_path); case = load(absolute(original)); result = copy.deepcopy(case)
    lanes = {'baseline': load(absolute(baseline)), 'candidate': load(absolute(candidate))}
    require(all(r['pass'] is True for r in lanes.values()), 'Restored lanes must pass')
    for lane, value in lanes.items():
        require([s['name'] for s in value['steps']] == [s['name'] for s in case['steps']], 'Restored stages mismatch')
        for target, raw, saved in zip(result['steps'], case['steps'], value['steps']):
            require(saved['prompt'] == raw['prompt'] and saved['expected'] == raw['expected'] and saved['request_hashes'] == raw['request_hashes'], 'Semantic case changed')
            target[lane + '_pages'] = saved['pages']; target['page_hashes'][lane] = saved['page_hashes']
            target['source_bindings'][lane] = saved['source_binding']
    for step in result['steps']:
        if 'source_pages' in step:
            step['source_pages'] = {key: [str(reference(p, base)) for p in paths] for key, paths in step['source_pages'].items()}
    result['model_settings'] = manifest['model_settings']
    from compare_host_delivery import validate_case
    validate_case(result)
    return result


def close_clones(result_path, gate_path, owned_root, archive):
    gate_check(gate_path); result = load(absolute(result_path)); owned = absolute(owned_root); archive = absolute(archive)
    require(archive.is_relative_to(owned), 'Closure archive outside owned root')
    from jcm.store import Store
    profiles = [absolute(s['source_binding']['profile']) for s in result['steps']]
    require(len(set(profiles)) == len(profiles), 'Duplicate clone profile')
    for profile in profiles:
        cfg = load(profile); runtime_check(result['runtime_hash'], result['steps'][0]['source_binding']['runtime_source'])
        require(profile.is_relative_to(owned) and absolute(cfg['home']).is_relative_to(owned), 'Refusing to close outside owned clones')
        require(not profile.is_relative_to(archive), 'Closure archive contains clone')
    archive.mkdir(parents=True, exist_ok=False); reports = []
    names = ['events', 'calls', 'call_metrics', 'decisions', 'semantic_items']
    for index, profile in enumerate(profiles):
        cfg = load(profile); require(cfg['enabled'] is True, 'Clone already disabled')
        store_path = absolute(cfg['home']) / 'stores' / cfg['repo_id']; journal = store_path / 'journal.sqlite'
        copied_profile = archive / (str(index) + '.json'); copied_journal = archive / (str(index) + '.sqlite')
        shutil.copy2(profile, copied_profile); blobs = blob_hashes(store_path)
        with closing(connect(journal)) as src, closing(sqlite3.connect(copied_journal)) as dst:
            before = table_hashes(src, names); src.backup(dst)
        store = Store(cfg)
        try:
            store.change_policy(enabled=False)
            require(table_hashes(store.db, names) == before and blob_hashes(store_path) == blobs, 'Source/cache mutated during close')
            require(store.db.execute('SELECT count(*) FROM packs WHERE invalid=0').fetchone()[0] == 0, 'Closed clone has valid packs')
        finally:
            store.close()
        reports.append({'profile': str(profile), 'profile_at_run_sha256': sha(copied_profile), 'pre_disable_profile': str(copied_profile),
            'pre_disable_journal': str(copied_journal), 'pre_disable_journal_sha256': sha(copied_journal), 'source_cache_preserved': True})
    value = {'pass': True, 'profiles': reports, 'disabled_profiles': len(reports), 'provider_calls_new': 0}
    save(archive / 'fixture-closure.json', value)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('inventory')
    for name in ('case', 'closure', 'baseline-result', 'candidate-result', 'base', 'output'):
        p.add_argument('--' + name, required=True)
    p = sub.add_parser('restore')
    for name in ('inventory', 'gate', 'output', 'owned-root'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--lane', choices=('baseline', 'candidate'), required=True)
    p = sub.add_parser('case')
    for name in ('original', 'baseline-result', 'candidate-result', 'base', 'gate', 'output'):
        p.add_argument('--' + name, required=True)
    p = sub.add_parser('close')
    for name in ('result', 'gate', 'owned-root', 'archive'):
        p.add_argument('--' + name, required=True)
    args = parser.parse_args()
    if args.command == 'inventory':
        value = inventory(args.case, args.closure, {'baseline': args.baseline_result, 'candidate': args.candidate_result}, args.base)
        save(args.output, value)
    elif args.command == 'restore':
        value = restore_lane(args.inventory, args.gate, args.lane, args.output, args.owned_root)
    elif args.command == 'case':
        value = derive_case(args.original, args.baseline_result, args.candidate_result, args.gate, args.base)
        save(args.output, value)
    else:
        value = close_clones(args.result, args.gate, args.owned_root, args.archive)
    print(json.dumps({'pass': value.get('pass', True), 'command': args.command, 'provider_calls_new': 0}))


if __name__ == '__main__':
    main()
