import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from . import config
from .adapter import hook
from .coordinator import dispatch, read_pack, status
from .store import Store
from .util import JCMError, encode, identifier
from .worker import drain


def parser():
    result = argparse.ArgumentParser(prog='jcm')
    result.add_argument('--home', default=str(config.default_home()))
    result.add_argument('--repo', default=os.getcwd())
    sub = result.add_subparsers(dest='command', required=True)
    enable = sub.add_parser('enable')
    enable.add_argument('--install-hooks', action='store_true')
    sub.add_parser('install-hooks')
    sub.add_parser('plugin-bind')
    sub.add_parser('disable')
    policy = sub.add_parser('policy')
    policy.add_argument('--enabled', choices=['true', 'false'])
    sub.add_parser('doctor')
    sub.add_parser('status').add_argument('--detail', action='store_true')
    sync = sub.add_parser('sync')
    sync.add_argument('--capture-only', action='store_true')
    sync.add_argument('--no-follow', action='store_true')
    entry = sub.add_parser('entry').add_subparsers(dest='entry_mode', required=True)
    preview = entry.add_parser('preview')
    preview.add_argument('--session-id')
    preview.add_argument('--page', type=int, default=1)
    choose = entry.add_parser('choose')
    choose.add_argument('--session-id')
    choose.add_argument('--entry', required=True)
    choose.add_argument('--choice', required=True, choices=['from_invocation', 'whole_session', 'resume', 'keep_disabled', 'new_task', 'continue_request'])
    tasks = entry.add_parser('tasks')
    tasks.add_argument('--session-id')
    tasks.add_argument('--entry', required=True)
    tasks.add_argument('--page', type=int, default=1)
    tasks.add_argument('--search')
    select = entry.add_parser('select')
    select.add_argument('--session-id')
    select.add_argument('--entry', required=True)
    select.add_argument('--task', required=True)
    capture = sub.add_parser('hook')
    capture.add_argument('--stdin', action='store_true', required=True)
    route = sub.add_parser('dispatch')
    route.add_argument('--request-token', required=True)
    read = sub.add_parser('read')
    read.add_argument('--pack', required=True)
    read.add_argument('--page', type=int, default=1)
    inspect = sub.add_parser('inspect')
    inspect.add_argument('--record', required=True)
    inspect.add_argument('--raw', action='store_true')
    worker = sub.add_parser('worker')
    worker.add_argument('action', choices=['drain'])
    worker.add_argument('--limit', type=int, default=4)
    bootstrap = sub.add_parser('bootstrap').add_subparsers(dest='bootstrap_mode', required=True)
    existing = bootstrap.add_parser('existing')
    existing.add_argument('--session-id')
    existing.add_argument('--transcript')
    existing.add_argument('--no-install-hooks', action='store_true')
    existing.add_argument('--no-follow', action='store_true')
    fresh = bootstrap.add_parser('new')
    fresh.add_argument('--request-token', required=True)
    follow = sub.add_parser('follow')
    follow.add_argument('--source', required=True)
    forget = sub.add_parser('forget')
    forget.add_argument('--session', required=True)
    return result


def run(args):
    from .plugin import environment_binding, bind
    administrative = args.command in ('status', 'doctor', 'disable', 'policy', 'forget')
    binding = environment_binding() if os.environ.get('JCM_PLUGIN_ROOT') and not administrative else None
    if args.command == 'entry':
        from . import entry
        if args.entry_mode == 'preview':
            if binding:
                try:
                    previous = config.load(args.home, args.repo)
                except JCMError as exc:
                    if str(exc) != 'PROJECT_NOT_ENABLED':
                        raise
                else:
                    bind(previous, binding, migrate=not previous.get('plugin'))
            return entry.preview(args.home, args.repo, args.session_id, page=args.page)
        if args.entry_mode == 'choose':
            return entry.choose(args.home, args.repo, args.entry, args.choice, args.session_id, binding)
    if args.command == 'enable':
        policy = config.enable(args.home, args.repo)
        if binding:
            policy = bind(policy, binding, migrate=True)
        store = Store(policy)
        if not policy['enabled']:
            policy = store.change_policy(enabled=True)
        store.close()
        installed = config.install_hooks(policy) if args.install_hooks else None
        return {'repo_id': policy['repo_id'], 'root': policy['root'], 'home': policy['home'],
                'allow_egress': policy['allow_egress'], 'mode': 'limited', 'hooks': installed}
    policy = config.load(args.home, args.repo)
    if args.command == 'plugin-bind':
        if not binding:
            raise JCMError('RUN_FROM_INSTALLED_PLUGIN')
        policy = bind(policy, binding, migrate=True)
        return {'plugin': policy['plugin'], 'records_preserved': True, 'enabled': policy['enabled']}
    if binding and policy.get('plugin'):
        policy = bind(policy, binding)
    if args.command == 'install-hooks':
        return config.install_hooks(policy)
    store = Store(policy)
    try:
        if args.command == 'entry':
            from . import entry
            if args.entry_mode == 'tasks':
                return entry.tasks(store, args.entry, args.session_id, args.page, args.search)
            return entry.select_task(store, args.entry, args.task, args.session_id)
        if args.command == 'disable':
            store.change_policy(enabled=False)
            return {'enabled': False, 'records_preserved': True}
        if args.command == 'policy':
            changes = {}
            if args.enabled is not None:
                changes['enabled'] = args.enabled == 'true'
            if not changes:
                raise JCMError('POLICY_CHANGE_REQUIRED')
            updated = store.change_policy(**changes)
            return {'epoch': updated['epoch'], 'allow_egress': updated['allow_egress'],
                    'enabled': updated['enabled'], 'old_packs_invalidated': True}
        if args.command == 'status':
            return status(store, args.detail)
        if args.command == 'doctor':
            from .health import doctor
            return doctor(store)
        if args.command == 'sync':
            from .sync import sync
            return sync(store, capture_only=args.capture_only, follow=not args.no_follow)
        if args.command == 'hook':
            # Hooks contain one JSON record. Large results use the same blob
            # persistence as transcript recovery rather than a separate quota.
            return hook(store, json.load(sys.stdin))
        if args.command == 'bootstrap':
            from .bootstrap import existing, new
            if args.bootstrap_mode == 'existing':
                return existing(store, args.session_id, args.transcript,
                                install=not args.no_install_hooks, follow=not args.no_follow)
            return new(store, args.request_token)
        if args.command == 'follow':
            from .follower import follow
            return follow(store, args.source)
        if args.command == 'dispatch':
            return dispatch(store, args.request_token)
        if args.command == 'read':
            return read_pack(store, args.pack, args.page)
        if args.command == 'inspect':
            store.policy()
            return {'origin': 'jcm', 'source': store.material(store.event(identifier(args.record)), raw=args.raw)}
        if args.command == 'worker':
            if not 0 <= args.limit <= 64:
                raise JCMError('INVALID_WORKER_LIMIT')
            return drain(store, limit=args.limit)
        if args.command == 'forget':
            return store.forget_session(args.session)
        raise JCMError('UNKNOWN_COMMAND')
    finally:
        store.close()


def main():
    os.umask(0o077)
    args = parser().parse_args()
    try:
        result = run(args)
        sys.stdout.buffer.write(encode(result) + b'\n')
    except (JCMError, ValueError, OSError, sqlite3.Error) as error:
        # Never echo exception bodies from external providers or input payloads.
        code = str(error) if isinstance(error, JCMError) else type(error).__name__
        if args.command == 'hook':
            sys.stdout.buffer.write(encode({'systemMessage': 'JCM capture failed: ' + code + '. No durable capture ACK.'}) + b'\n')
        else:
            sys.stdout.buffer.write(encode({'error': code, 'origin': 'jcm'}) + b'\n')
        print('JCM: ' + code, file=sys.stderr)
        return 1
    return 0
