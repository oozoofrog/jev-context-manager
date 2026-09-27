"""Bounded, local-only transcript follower for sessions started before hook installation."""
import fcntl
import json
import os
import subprocess
import time
import threading
from pathlib import Path

from .adapter import recover_source
from .util import JCMError, encode, identifier


def follower_status(store, key):
    row = store.db.execute('SELECT value FROM meta WHERE key=?', ('follower:' + key,)).fetchone()
    value = json.loads(row[0]) if row else {'source_key': key, 'state': 'not_started'}
    alive = False
    try:
        if value.get('pid'):
            os.kill(value['pid'], 0)
            alive = True
    except ProcessLookupError:
        pass
    value['running'] = alive and value['state'] == 'running' and time.time() - value.get('heartbeat', 0) < 10
    return value


def start(store, key):
    value = follower_status(store, key)
    if value['running']:
        return value
    command = store.config['cli_argv'] + ['follow', '--source', identifier(key)]
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    threading.Thread(target=process.wait, daemon=True).start()
    for _ in range(40):
        value = follower_status(store, key)
        if value['running']:
            return value
        if process.poll() is not None:
            break
        time.sleep(.05)
    store.gap('EXISTING_SESSION_FOLLOWER_NOT_RUNNING', key)
    return value


def follow(store, key, idle_seconds=1800, max_seconds=86400, interval=.5):
    key = identifier(key)
    lockpath = store.path / ('follower-' + key + '.lock')
    if lockpath.is_symlink():
        raise JCMError('SYMLINK_LOCK_REFUSED')
    with lockpath.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'state': 'already_running'}
        started = changed = time.monotonic()
        previous, state, error = None, 'running', None
        def record():
            value = {'source_key': key, 'pid': os.getpid(), 'state': state, 'error': error,
                     'heartbeat': time.time(), 'idle_seconds': idle_seconds, 'max_seconds': max_seconds,
                     'egress': 'none', 'recovery_on_next_request': True}
            store.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('follower:' + key, encode(value).decode()))
            return value
        try:
            while True:
                store.policy()
                row = store.db.execute('SELECT * FROM sources WHERE key=?', (key,)).fetchone()
                if not row:
                    state = 'source_removed'
                    break
                stat = Path(row['path']).stat()
                current = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
                if current != previous:
                    recover_source(store, row)
                    previous, changed = current, time.monotonic()
                record()
                if time.monotonic() - changed >= idle_seconds:
                    state = 'idle_expired'
                    break
                if time.monotonic() - started >= max_seconds:
                    state = 'lifetime_expired'
                    break
                time.sleep(interval)
        except (JCMError, OSError) as exc:
            state = 'stopped'
            error = str(exc) if isinstance(exc, JCMError) else 'SOURCE_UNAVAILABLE'
        return record()
