"""Observable recovery progress; stdout remains the completed JSON contract."""
import json
import os
import sys
import time
import uuid

from .util import JCMError, encode, now


def update(store, **fields):
    recovery = getattr(store, 'recovery', None)
    if recovery:
        recovery.update(**fields)


class Recovery:
    def __init__(self, store, token):
        self.store, self.token = store, token
        request = store.resolve_request(token)
        self.key = 'bootstrap_new:' + request['session']
        self.started = time.perf_counter()
        self.last_write = 0
        self.last_phase = None
        self.value = {'origin': 'jcm', 'bootstrap': 'new', 'session_id': request['session'],
                      'request_token': token, 'operation_id': uuid.uuid4().hex, 'pid': os.getpid(),
                      'stage': 'recovering', 'phase': 'starting', 'created_at': now(),
                      'provider_called': False, 'provider_calls': 0, 'delivery': 'not_served'}

    def __enter__(self):
        self.store.policy()
        self.store.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (self.key, encode(self.value).decode()))
        self.store.recovery = self
        self.update(force=True)
        return self

    def update(self, force=False, **fields):
        self.value.update(fields)
        clock = time.perf_counter()
        if not force and self.value['phase'] == self.last_phase and clock - self.last_write < 1:
            return
        self.value.update(elapsed_seconds=round(clock - self.started, 3), updated_at=now())
        # Forget or another recovery may have removed/replaced this operation.
        # A late network callback must not resurrect it or overwrite its successor.
        changed = self.store.db.execute('UPDATE meta SET value=? WHERE key=? '
            "AND json_extract(value, '$.operation_id')=? AND EXISTS(SELECT 1 FROM requests WHERE token=?)",
            (encode(self.value).decode(), self.key, self.value['operation_id'], self.token)).rowcount
        if changed and getattr(self.store, 'progress_output', False):
            public = {k: v for k, v in self.value.items() if k in
                      ('origin', 'stage', 'phase', 'elapsed_seconds', 'provider_calls', 'units', 'completed_units', 'cached_units', 'error')}
            print('JCM progress: ' + json.dumps(public, ensure_ascii=False), file=sys.stderr, flush=True)
        self.last_write, self.last_phase = clock, self.value['phase']

    def __exit__(self, typ, error, traceback):
        if error is not None:
            self.update(force=True, stage='interrupted' if isinstance(error, KeyboardInterrupt) else 'failed',
                        error='RECOVERY_INTERRUPTED' if isinstance(error, KeyboardInterrupt) else
                              str(error) if isinstance(error, JCMError) else type(error).__name__)
        self.store.recovery = None
        return False
