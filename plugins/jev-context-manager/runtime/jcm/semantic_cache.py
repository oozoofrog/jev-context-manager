"""Reusable independent judgments with explicit source/context dependencies.

Question builders may consult their own item and the shared context only. Pair
judgments must be one item containing BOTH sources; query-dependent judgments
must include the query in context. Batch neighbours are not semantic inputs.
"""
import json
import sqlite3
import time

from . import batching

from .batching import STOP_ERRORS, batches, context_fits, planned_spans, source_span, split_source
from .provider import CONTEXT_ERROR, RUBRIC_VERSION
from .util import JCMError, digest, encode, now
from .progress import update as progress

VERSION = 'independent-items-v2'
LEGACY_VERSION = 'independent-items-v1'
PARTITION_VERSION = 'source-partition-v1'


def dependencies(item):
    return item.get('dependencies', [{'event_id': item['event_id'], 'revision': item['revision']}]
                    if 'event_id' in item else [])


def validate_dependencies(store, items, bindings=None):
    deps = [dep for item in items for dep in dependencies(item)]
    bindings = bindings or {}
    identifiers = list(dict.fromkeys([d['event_id'] for d in deps] + list(bindings)))
    rows = {}
    # This is the SQLite parameter limit, not an evidence/source limit. Every
    # dependency is checked, including across chunk boundaries.
    limit = store.db.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER)
    for offset in range(0,len(identifiers),limit):
        batch=identifiers[offset:offset+limit]
        rows.update((r['id'],r) for r in store.db.execute(
            'SELECT id,revision,blob FROM events WHERE id IN ('+','.join('?' for _ in batch)+')',batch))
    for dep in deps:
        if dep['event_id'] not in rows:
            raise JCMError('EVENT_NOT_FOUND')
        if rows[dep['event_id']]['revision'] != dep['revision']:
            raise JCMError('SEMANTIC_DEPENDENCY_CHANGED')
    for event_id,blob in bindings.items():
        if event_id not in rows:
            raise JCMError('EVENT_NOT_FOUND')
        if rows[event_id]['blob'] != blob:
            raise JCMError('PACK_SOURCE_CHANGED')


def request(context, items, builder):
    questions = {}
    for index, item in enumerate(items):
        questions.update({f'i{index}_{name}': q for name, q in builder(f'`state.items[{index}]`', item).items()})
    # Dependency IDs protect cache reuse and publication in code. They are not
    # semantic evidence and can repeat an entire source group for every item.
    # Keep them in cache keys/local validation, outside the model's input.
    visible=[]
    for item in items:
        value={k:v for k,v in item.items() if k!='dependencies' and not k.startswith('_')}
        if 'reading_text' in value:
            # The decoded view and raw offsets remain bound in the local cache
            # key. Send the readable evidence once, not an escaped duplicate.
            value['text']=value.pop('reading_text')
        if '_parents' in item:
            value['parent_conditions']=[{'path':p['path'],'text':p.get('reading_text',p['text'])}
                                        for p in item['_parents']]
        visible.append(value)
    return {'context': context, 'items': visible}, questions


def source_items(material, context, builder):
    # The whole-source hash is invariant across probes and resulting spans.
    # Large tool outputs otherwise re-encode megabytes on every binary probe.
    source_hash = digest(material['text'])
    spans = planned_spans(material['text'], lambda a, b:
        batching.context_fits(*request(context, [source_span(material, a, b, source_hash)], builder)))
    return [source_span(material, a, b, source_hash) for a, b in spans]


def evaluate_items(store, provider, kind, context, items, builder, epoch, splittable=True, heartbeat=None):
    policy = store.policy(epoch)
    result = {'records': [], 'errors': [], 'decisions': [], 'batches': [],
              'cache_hits': 0, 'evaluated_units': 0, 'partitions_reused': 0}
    stopped = None
    unsplittable_sources = set()
    start = time.perf_counter()
    progress(store, phase=kind, units=len(items), completed_units=0, cached_units=0)

    def key(item, legacy_epoch=None):
        # Epoch protects in-flight publication/egress, not the meaning of a
        # completed pure judgment. Source, question, model, scope and lane still
        # participate in its identity. Forget deletes these derivatives.
        prefix = ([VERSION, RUBRIC_VERSION, kind, policy['repo_id']] if legacy_epoch is None else
                  [LEGACY_VERSION, RUBRIC_VERSION, kind, policy['repo_id'], legacy_epoch])
        return digest(prefix + [policy['model'], provider.lane, context, item,
                                builder('`state.items[0]`', item)])

    legacy_epochs = [r[0] for r in store.db.execute(
        'SELECT DISTINCT epoch FROM semantic_items WHERE kind=? AND model=?', (kind, policy['model']))]

    def lookup(item, item_key):
        cached = store.db.execute('SELECT * FROM semantic_items WHERE key=?', (item_key,)).fetchone()
        if cached:
            return cached, item_key
        for old in legacy_epochs:
            candidate = key(item, old)
            cached = store.db.execute('SELECT * FROM semantic_items WHERE key=?', (candidate,)).fetchone()
            if cached:
                return cached, candidate
        return None, None

    def uncached(batch):
        # Cache lookup is local work, not an egress boundary. Validate policy
        # before and after the pass; provider reservation/publication check it
        # again. Do not spawn a git process for every cached source unit.
        store.policy(epoch)
        remaining = []
        pending = list(reversed(batch))
        while pending:
            item = pending.pop()
            validate_dependencies(store, [item])
            item_key = key(item)
            cached, _ = lookup(item, item_key)
            if cached:
                refs = [{**r, 'cached': True} for r in json.loads(cached['decisions'])]
                result['records'].append({'item': item, 'answers': json.loads(cached['answer']),
                                          'decisions': refs, 'cached': True})
                result['decisions'].extend(refs)
                result['cache_hits'] += 1
            else:
                partition = None
                if splittable and 'span' in item:
                    for parent in [item_key] + [key(item, old) for old in legacy_epochs]:
                        partition = store.db.execute('SELECT answer FROM semantic_items WHERE key=?',
                            (digest([PARTITION_VERSION, parent]),)).fetchone()
                        if partition:
                            break
                if partition:
                    parts = split_source(item)
                    if parts and [p['span'] for p in parts] == json.loads(partition[0])['children']:
                        # A server-confirmed single-source rejection is bound to
                        # the exact parent question/context. Restore that tree
                        # before batching misses with new neighbouring sources.
                        pending.extend(reversed(parts))
                        result['partitions_reused'] += 1
                        continue
                remaining.append(item)
        store.policy(epoch)
        return remaining

    def process(remaining):
        nonlocal stopped
        remaining=[item for item in remaining if item.get('event_id') not in unsplittable_sources]
        if not remaining:
            return
        if stopped:
            result['errors'].append(stopped)
            return
        state, questions = request(context, remaining, builder)
        report = {'kind': kind, 'request_hash': digest([state, questions]),
                  'sources': [dependencies(i) for i in remaining]}
        result['batches'].append(report)
        try:
            evaluated = provider.evaluate(state, questions, **({'heartbeat': heartbeat} if heartbeat else {}))
            ref = {'id': evaluated['decision_id'], 'model': evaluated['response']['model'],
                   'lane': evaluated['lane'], 'cached': evaluated['cached']}
            report.update(status='success', decision=ref)
            result['decisions'].append(ref)
            store.db.execute('BEGIN IMMEDIATE')
            try:
                store.policy(epoch)
                validate_dependencies(store, remaining)
                for index, item in enumerate(remaining):
                    names = builder(f'`state.items[{index}]`', item)
                    answers = {name: evaluated['response']['answers'][f'i{index}_{name}'] for name in names}
                    store.db.execute('INSERT OR REPLACE INTO semantic_items VALUES (?,?,?,?,?,?,?,?)',
                        (key(item), kind, epoch, policy['model'], encode(dependencies(item)).decode(),
                         encode(answers).decode(), encode([ref]).decode(), now()))
                    result['records'].append({'item': item, 'answers': answers, 'decisions': [ref],
                                              'cached': evaluated['cached']})
                store.db.execute('COMMIT')
            except BaseException:
                store.db.execute('ROLLBACK')
                raise
            result['cache_hits' if evaluated['cached'] else 'evaluated_units'] += len(remaining)
            progress(store, completed_units=result['evaluated_units'], cached_units=result['cache_hits'])
        except JCMError as error:
            code = str(error)
            report.update(status='failed', error=code)
            if code == CONTEXT_ERROR:
                if len(remaining) > 1:
                    middle = len(remaining) // 2
                    process(remaining[:middle]); process(remaining[middle:])
                    return
                parts = split_source(remaining[0]) if splittable and 'span' in remaining[0] else []
                if parts:
                    store.db.execute('BEGIN IMMEDIATE')
                    try:
                        store.policy(epoch)
                        validate_dependencies(store, remaining)
                        store.db.execute('INSERT OR REPLACE INTO semantic_items VALUES (?,?,?,?,?,?,?,?)',
                            (digest([PARTITION_VERSION, key(remaining[0])]), PARTITION_VERSION, epoch, policy['model'],
                             encode(dependencies(remaining[0])).decode(),
                             encode({'children': [p['span'] for p in parts], 'reason': code,
                                     'request_hash': report['request_hash']}).decode(), '[]', now()))
                        store.db.execute('COMMIT')
                    except BaseException:
                        store.db.execute('ROLLBACK')
                        raise
                    for part in parts:
                        process(uncached([part]))
                    return
                if splittable and 'span' in remaining[0] and 'event_id' in remaining[0]:
                    # Even the smallest fragment of this source was rejected.
                    # Preserve the whole record as unresolved instead of trying
                    # its larger sibling fragments; other sources may still fit.
                    unsplittable_sources.add(remaining[0]['event_id'])
            result['errors'].append(code)
            # A single unsplittable item may exceed the provider context while
            # later independent items still fit. Keep that item unresolved;
            # only operation-wide failures stop the rest of the stage.
            if code in STOP_ERRORS:
                stopped = code

    # Only misses need provider-context packing. Previously even a fully cached
    # task rebuilt and sized every candidate batch before consulting the cache.
    for batch in batches(uncached(items), lambda batch: batching.context_fits(*request(context, batch, builder))):
        process(batch)
    store.policy(epoch)
    validate_dependencies(store, items)
    # Cached units and adaptive children can complete in a different order.
    result['records'].sort(key=lambda r: (r['item'].get('event_id', ''), r['item'].get('span', {}).get('start', 0)))
    result['errors'] = sorted(set(result['errors']))
    result['elapsed_seconds'] = time.perf_counter() - start
    progress(store, completed_units=result['evaluated_units'], cached_units=result['cache_hits'])
    return result
