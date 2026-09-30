"""Inspect and resume an exact frozen journal; never rebuild it from page exports.

Inspect/clone are offline. Only measure dispatches a new provider operation.
Every output directory must be new; source profiles and journals are read-only.
"""
import argparse
from collections import Counter
from contextlib import closing, contextmanager
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import time

from jcm.blob_storage import read as read_blob
from jcm.config import atomic_write
from jcm.coordinator import dispatch, read_pack
from jcm.delivery import reconstruct_pages
from jcm.metrics import provider_metrics
from jcm.provider import RUBRIC_VERSION, validate
from jcm.representations import validate_contract
from jcm.store import Store
from jcm.util import JCMError, digest, encode


def read_json(path):
    return json.loads(Path(path).read_text())


def fingerprint(db):
    return digest([tuple(r) for r in db.execute('SELECT id,revision,blob FROM events ORDER BY seq')])


def runtime_hash():
    return digest({p.name: digest(p.read_bytes()) for p in Path(__import__('jcm').__file__).parent.glob('*.py')})


@contextmanager
def readonly(path):
    # sqlite's default connect creates an empty journal when storage is gone.
    if Path(path).is_symlink():
        raise JCMError('SYMLINK_DATABASE_REFUSED')
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        yield db
    finally:
        db.close()


def inspect(profile, expected, interrupted):
    profile = Path(profile).resolve()
    report = {'profile': str(profile), 'ready': False, 'errors': [], 'provider_called': False,
              'expected_source_fingerprint': expected['source_fingerprint'],
              'expected_event_count': expected['event_count'],
              'decision_inventory': None, 'semantic_inventory': None,
              'inventory_boundary': 'Unavailable storage is unknown, not an empty successful cache.'}
    if not profile.is_file():
        report['errors'].append('PROFILE_MISSING')
        return report
    cfg = read_json(profile)
    home = Path(cfg['home']).resolve()
    journal = home / 'stores' / cfg['repo_id'] / 'journal.sqlite'
    report.update(home=str(home), journal=str(journal), epoch=cfg['epoch'], enabled=cfg['enabled'],
                  profile_hash=digest(profile.read_bytes()))
    if profile != home / 'profiles' / (cfg['repo_id'] + '.json'):
        report['errors'].append('PROFILE_HOME_MISMATCH')
    if interrupted['repo_id'] != cfg['repo_id']:
        report['errors'].append('PROJECT_IDENTITY_MISMATCH')
    if cfg['epoch'] < interrupted['policy_epoch']:
        report['errors'].append('POLICY_HISTORY_INCOMPLETE')
    if not journal.is_file():
        report['errors'].append('JOURNAL_MISSING')
        return report
    with readonly(journal) as db:
        db.execute('BEGIN')
        report['event_count'] = db.execute('SELECT count(*) FROM events').fetchone()[0]
        report['source_fingerprint'] = fingerprint(db)
        if report['event_count'] != expected['event_count'] or report['source_fingerprint'] != expected['source_fingerprint']:
            report['errors'].append('FROZEN_SOURCE_MISMATCH')
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            report['errors'].append('JOURNAL_INTEGRITY_FAILED')
        report['registered_sources'] = db.execute('SELECT count(*) FROM sources').fetchone()[0]
        if report['registered_sources']:
            report['errors'].append('LIVE_SOURCE_REGISTRATION_IN_FROZEN_STORE')
        events = {r['id']: dict(r) for r in db.execute('SELECT id,revision,blob,session FROM events')}
        tombstones = {r[0] for r in db.execute('SELECT session FROM tombstones')}
        if tombstones & {r['session'] for r in events.values()}:
            report['errors'].append('FORGOTTEN_SOURCE_PRESENT')
        for dep in interrupted.get('source_dependencies', []):
            event = events.get(dep['event_id'])
            if not event or event['revision'] != dep['revision']:
                report['errors'].append('INTERRUPTED_SOURCE_DEPENDENCY_MISMATCH')
        for eid, blob in interrupted.get('source_bindings', {}).items():
            if eid not in events or events[eid]['blob'] != blob:
                report['errors'].append('INTERRUPTED_SOURCE_BLOB_MISMATCH')
        request = db.execute('SELECT * FROM requests WHERE token=?', (interrupted['request_token'],)).fetchone()
        if not request or request['event_id'] not in events or request['session'] != interrupted['session_id']:
            report['errors'].append('INTERRUPTED_REQUEST_MISSING')
        else:
            report['request'] = dict(request)
        decisions = {r['id']: dict(r) for r in db.execute('SELECT * FROM decisions')}
        report['decision_ledger_fingerprint'] = digest(sorted((tuple(d.items()) for d in decisions.values()), key=lambda d: d[0][1]))
        required = set(expected.get('decision_ids', [])) | {r['id'] for r in interrupted.get('decisions', [])}
        missing = sorted(required - decisions.keys())
        required_success = {r['id'] for r in interrupted.get('decisions', [])}
        invalid = sorted(r for r in required_success & decisions.keys() if decisions[r]['status'] not in ('success', 'composed'))
        report['decision_inventory'] = dict(Counter(d['status'] for d in decisions.values()))
        report['decision_errors'] = dict(Counter(d['error'] for d in decisions.values() if d['error']))
        report['missing_prior_decisions'] = missing
        report['invalid_success_references'] = invalid
        if missing or invalid:
            report['errors'].append('PRIOR_JUDGMENTS_NOT_RECOVERED')
        blobs = home / 'stores' / cfg['repo_id'] / 'blobs'
        decoded = {}
        def blob(key):
            if key not in decoded:
                decoded[key] = read_blob(blobs, key)
            return decoded[key]
        blob_errors = []
        for key in {e['blob'] for e in events.values()} | {r[0] for r in db.execute('SELECT blob FROM event_sources')}:
            try:
                blob(key)
            except (JCMError, ValueError) as error:
                blob_errors.append({'blob': key, 'error': str(error)})
        valid_decisions = set()
        for decision in decisions.values():
            try:
                request_body = blob(decision['request_blob'])
                if decision['status'] == 'success':
                    payload = request_body['payload']
                    validate(blob(decision['response_blob']), payload['questions'], payload['model'])
                    if decision['model'] != payload['model'] or request_body['rubric_version'] != RUBRIC_VERSION or request_body['lane'] not in ('real_http', 'mock'):
                        raise JCMError('JUDGMENT_CONTRACT_MISMATCH')
                    valid_decisions.add(decision['id'])
                elif decision['response_blob']:
                    blob(decision['response_blob'])
            except (JCMError, ValueError, KeyError, TypeError) as error:
                blob_errors.append({'decision': decision['id'], 'error': str(error)})
        report['blob_errors'] = blob_errors
        if blob_errors:
            report['errors'].append('SOURCE_OR_JUDGMENT_BLOB_UNVERIFIED')
        semantic = Counter()
        semantic_errors = []
        kinds = Counter()
        semantic_rows = db.execute('SELECT * FROM semantic_items ORDER BY key').fetchall()
        report['semantic_cache_fingerprint'] = digest([tuple(row) for row in semantic_rows])
        for row in semantic_rows:
            kinds[row['kind']] += 1
            try:
                deps = json.loads(row['dependencies'])
                if any(d['event_id'] not in events or events[d['event_id']]['revision'] != d['revision'] for d in deps):
                    semantic['stale_dependencies'] += 1
                    continue
                refs = json.loads(row['decisions'])
                if row['kind'] == 'source-partition-v1':
                    semantic['context_partitions'] += 1
                elif refs and all(r['id'] in valid_decisions for r in refs):
                    answers = json.loads(row['answer'])
                    for ref in refs:
                        decision = decisions[ref['id']]
                        stored_request = blob(decision['request_blob'])
                        if (row['model'] != decision['model'] or ref['model'] != decision['model'] or
                                ref['lane'] != stored_request['lane']):
                            raise JCMError('SEMANTIC_JUDGMENT_IDENTITY_MISMATCH')
                        response = blob(decision['response_blob'])['answers']
                        # Item answers are exact typed subsets of the original
                        # successful response, never reconstructed from pack IDs.
                        prefixes = {name.split('_', 1)[0] for name in response}
                        if not any(all(response.get(prefix + '_' + name) == value for name, value in answers.items())
                                   for prefix in prefixes):
                            raise JCMError('SEMANTIC_ANSWER_MISMATCH')
                    semantic['successful_units'] += 1
                else:
                    semantic['unverified_units'] += 1
                    semantic_errors.append(row['key'])
            except (JCMError, ValueError, KeyError, TypeError):
                semantic_errors.append(row['key'])
        report['semantic_inventory'] = dict(semantic)
        report['semantic_kinds'] = dict(kinds)
        report['unverified_semantic_keys'] = semantic_errors
        report['missing_semantic_units'] = None
        report['missing_semantic_units_reason'] = 'Only a matching planner can enumerate unevaluated query units; absent entries are not successful judgments.'
        if semantic_errors or not semantic['successful_units']:
            report['errors'].append('SEMANTIC_CACHE_NOT_RECOVERED')
        report['provider_error_history'] = dict(Counter(str(r[0]) for r in db.execute('SELECT status FROM provider_errors')))
    registry = home / 'registry.sqlite'
    if registry.is_file():
        with readonly(registry) as db:
            row = db.execute('SELECT repo_id FROM projects WHERE root=?', (cfg['root'],)).fetchone()
            if not row or row[0] != cfg['repo_id']:
                report['errors'].append('REGISTRY_IDENTITY_MISMATCH')
    else:
        report['errors'].append('REGISTRY_MISSING')
    report['errors'] = sorted(set(report['errors']))
    report['ready'] = not report['errors']
    return report


def clone(profile, expected, interrupted, target):
    inventory = inspect(profile, expected, interrupted)
    if not inventory['ready']:
        raise JCMError('HISTORICAL_RESUME_PREFLIGHT_FAILED:' + ','.join(inventory['errors']))
    source = Path(inventory['home'])
    target = Path(target).resolve()
    # The clone is the only policy writer. Never re-enable an original/global profile.
    if target.exists() or source in target.parents or target in source.parents:
        raise JCMError('NEW_ISOLATED_HOME_REQUIRED')
    target.mkdir(parents=True, mode=0o700)
    for relative in ('registry.sqlite', 'stores/' + interrupted['repo_id'] + '/journal.sqlite'):
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with readonly(source / relative) as src, closing(sqlite3.connect(destination)) as dest:
            src.backup(dest)
    for name in ('blobs', 'chunks'):
        folder = source / 'stores' / interrupted['repo_id'] / name
        if folder.exists():
            if folder.is_symlink() or any(p.is_symlink() for p in folder.rglob('*')):
                raise JCMError('SYMLINK_STORAGE_REFUSED')
            shutil.copytree(folder, target / 'stores' / interrupted['repo_id'] / name)
    cfg = {**read_json(profile), 'home': str(target), 'transcript_roots': []}
    cfg.pop('plugin', None)
    cfg['cli_argv'] = [sys.executable, '-m', 'jcm', '--home', str(target), '--repo', cfg['root']]
    target_profile = target / 'profiles' / (cfg['repo_id'] + '.json')
    atomic_write(target_profile, encode(cfg))
    # Recheck the copied bytes and dependency inventory before any policy change.
    copied = inspect(target_profile, expected, interrupted)
    if (not copied['ready'] or any(copied[k] != inventory[k] for k in
            ('source_fingerprint', 'decision_ledger_fingerprint', 'semantic_cache_fingerprint')) or
            digest(Path(profile).read_bytes()) != inventory['profile_hash']):
        raise JCMError('COPIED_RESUME_STATE_INVALID')
    with_store = Store(cfg)
    try:
        policy = with_store.change_policy(enabled=True)
    finally:
        with_store.close()
    ready = inspect(target_profile, expected, interrupted)
    ready.update(original_profile=str(Path(profile).resolve()), epoch_before=cfg['epoch'],
                 epoch_after=policy['epoch'], prior_inventory=inventory,
                 runtime_hash=runtime_hash(), owned_clone=True,
                 input_bindings={'expected': digest(expected), 'interrupted': digest(interrupted)})
    atomic_write(target / 'resume-state.json', encode(ready))
    return ready


def measure(home, expected, interrupted, output):
    home = Path(home).resolve()
    saved = read_json(home / 'resume-state.json')
    if (not saved.get('owned_clone') or saved['home'] != str(home) or saved['runtime_hash'] != runtime_hash() or
            saved['input_bindings'] != {'expected': digest(expected), 'interrupted': digest(interrupted)}):
        raise JCMError('RESUME_MANIFEST_OR_RUNTIME_CHANGED')
    before = inspect(saved['profile'], expected, interrupted)
    if not before['ready'] or not before['enabled'] or before['epoch'] != saved['epoch_after']:
        raise JCMError('HISTORICAL_RESUME_PREFLIGHT_FAILED')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    atomic_write(output / 'inventory-before.json', encode(before))
    store = Store(read_json(saved['profile']))
    store.progress_output = True
    prior = {r[0] for r in store.db.execute('SELECT id FROM decisions')}
    started = time.perf_counter()
    result = {'pass': False, 'runtime_hash': runtime_hash(), 'prior_provider_error_history': before['provider_error_history'],
              'source_fingerprint': before['source_fingerprint'], 'measurement': 'resumed_existing_cache',
              'model_consumption_or_use': 'not_run'}
    try:
        old = before['request']
        token = store.request(old['session'], old['event_id'])
        if token == interrupted['request_token']:
            raise JCMError('FRESH_REQUEST_REQUIRED')
        route = dispatch(store, token)
        pack = store.blob(store.db.execute('SELECT blob FROM packs WHERE id=?', (route['pack_id'],)).fetchone()[0])
        atomic_write(output / 'pack.json', encode(pack))
        result.update(route=route, metrics=pack['metrics'], gaps=pack['coverage']['gaps'],
                      preparation_seconds=time.perf_counter() - started)
        pages = []
        while True:
            response = read_pack(store, route['pack_id'], len(pages) + 1, 'brief')
            pages.append(response)
            atomic_write(output / ('brief-' + str(len(pages)) + '.json'), encode(response))
            if not response.get('next_read_command'):
                break
        logical = reconstruct_pages(pages)
        checks = {'normal': pack['quality'] == 'normal', 'ready': pack['dispatch'] == 'ready',
                  'delivery_contract': not validate_contract(pack['query_context'], logical),
                  'required_read_complete': pages[-1].get('required_context_complete', False),
                  'source_unchanged': fingerprint(store.db) == before['source_fingerprint'],
                  'runtime_unchanged': runtime_hash() == saved['runtime_hash']}
        result.update(checks=checks, pass_=all(checks.values()), page_count=len(pages),
                      qualification_limits=pack['query_context'].get('qualification_limits', []),
                      complete_recovery_established=False if pack['query_context'].get('qualification_limits') else None,
                      consumer_gate='Requires independent unchanged semantic oracle acceptance before consumer execution.')
        result['pass'] = result.pop('pass_')
    except BaseException as error:
        result['error'] = 'RECOVERY_INTERRUPTED' if isinstance(error, KeyboardInterrupt) else str(error)
        raise
    finally:
        current = {r[0] for r in store.db.execute('SELECT id FROM decisions')}
        result.update(incremental_provider_metrics=provider_metrics(store, current - prior),
                      total_seconds=time.perf_counter() - started,
                      usage_boundary='Returned usage only; HTTP failures without usage remain unmeasured.')
        atomic_write(output / 'result.json', encode(result))
        store.close()
        atomic_write(output / 'inventory-after.json', encode(inspect(saved['profile'], expected, interrupted)))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('inspect', 'clone', 'measure'))
    parser.add_argument('--expected', type=Path, required=True, help='Frozen history-before.json')
    parser.add_argument('--interrupted', type=Path, required=True, help='402 candidate pack.json')
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--home', type=Path, help='New isolated home for clone; owned clone for measure')
    parser.add_argument('--output', type=Path, required=True, help='New evidence directory')
    args = parser.parse_args()
    if args.action in ('clone', 'measure') and args.home is None:
        parser.error('--home is required for clone and measure')
    args.output.mkdir(parents=True, exist_ok=False)
    expected, interrupted = read_json(args.expected), read_json(args.interrupted)
    try:
        if args.action == 'inspect':
            report = inspect(args.profile or expected['profile'], expected, interrupted)
        elif args.action == 'clone':
            report = clone(args.profile or expected['profile'], expected, interrupted, args.home)
        else:
            # main reserves the directory; measure owns a separate new child.
            report = measure(args.home, expected, interrupted, args.output / 'evaluation')
    except (JCMError, OSError, ValueError, sqlite3.Error) as error:
        report = {'ready': False, 'errors': [str(error)], 'provider_called': False if args.action != 'measure' else 'see evaluation/result.json'}
    atomic_write(args.output / 'result.json', encode(report))
    print(json.dumps({'ready': report.get('ready'), 'pass': report.get('pass'), 'errors': report.get('errors'),
                      'output': str(args.output.resolve())}))
    return 0 if report.get('ready', report.get('pass', False)) else 1


if __name__ == '__main__':
    raise SystemExit(main())
