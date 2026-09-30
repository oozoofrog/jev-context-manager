import json
import time
import uuid

from .provider import JevProvider, noul
from .batching import adaptive_batches, context_fits, planned_spans, source_span, split_source
from .util import JCMError, now

from .classification import LABELS, classify


def drain(store, provider=None, limit=4, through_seq=None):
    provider = provider or JevProvider(store)
    done, owner, errors, decision_refs = 0, uuid.uuid4().hex, [], []
    for _ in range(limit):
        store.policy()
        event = store.lease(owner, through_seq=through_seq)
        if not event:
            break
        epoch = store.policy()['epoch']
        try:
            source = store.material(event)
            policy = store.policy(epoch)
            def renew():
                store.policy(epoch)
                changed = store.db.execute("UPDATE jobs SET lease_until=? WHERE event_id=? AND owner=? AND lease_until>?",
                    (time.time() + 40, event['id'], owner, time.time())).rowcount
                if not changed or store.event(event['id'])['revision'] != event['revision']:
                    raise JCMError('WORKER_REVISION_OR_LEASE_CHANGED')
            classified = classify(store, provider, [source], epoch, heartbeat=renew)
            if classified['errors']:
                raise JCMError(classified['errors'][0])
            records = classified['records']
            from .local_index import refresh
            refresh(store, [source], epoch)
            refs = list(dict.fromkeys(ref['id'] for r in records for ref in r['decisions']))
            decision_refs.extend(refs)
            labels = {name: max(r['answers'][name]['noul'] for r in records) for name in LABELS}
            decision = refs[0] if len(refs) == 1 else uuid.uuid4().hex
            store.db.execute('BEGIN IMMEDIATE')
            try:
                store.policy(epoch)
                current = store.event(event['id'])
                job = store.db.execute('SELECT * FROM jobs WHERE event_id=?', (event['id'],)).fetchone()
                if current['revision'] != event['revision'] or job['owner'] != owner or job['lease_until'] < time.time():
                    raise JCMError('WORKER_REVISION_OR_LEASE_CHANGED')
                if len(refs) > 1:
                    # Explicit composition provenance; never present the max as a new Jev probability.
                    inputs = store.put_blob({'event_id': event['id'], 'revision': event['revision'],
                                             'fragment_decisions': refs})
                    output = store.put_blob({'labels': labels, 'aggregation': 'max_fragment_evidence'})
                    store.db.execute('INSERT INTO decisions (id,epoch,status,request_blob,response_blob,model,created) VALUES (?,?,?,?,?,?,?)',
                                     (decision, epoch, 'composed', inputs, output, policy['model'], now()))
                store.db.execute('INSERT OR REPLACE INTO projections VALUES (?,?,?,?,?,?,?)',
                    (event['id'], event['revision'], json.dumps(labels), source['basis'], 'unresolved',
                     'not_established', decision))
                store.db.execute("UPDATE jobs SET state='succeeded',decision=?,owner=NULL WHERE event_id=?",
                                 (decision, event['id']))
                store.db.execute('COMMIT')
            except BaseException:
                store.db.execute('ROLLBACK')
                raise
            done += 1
        except KeyboardInterrupt:
            store.db.execute("UPDATE jobs SET state='retryable',error='RECOVERY_INTERRUPTED',owner=NULL WHERE event_id=? AND owner=?",
                             (event['id'], owner))
            raise
        except JCMError as error:
            store.db.execute("UPDATE jobs SET state='retryable',error=?,owner=NULL WHERE event_id=? AND owner=?",
                             (str(error), event['id'], owner))
            errors.append(str(error))
            break
    return {'processed': done, 'errors': errors, 'lane': provider.lane, 'decision_refs': decision_refs}
