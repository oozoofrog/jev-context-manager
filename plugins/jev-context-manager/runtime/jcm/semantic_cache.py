"""Reusable independent judgments with explicit source/context dependencies.

Question builders may consult their own item and the shared context only. Pair
judgments must be one item containing BOTH sources; query-dependent judgments
must include the query in context. Batch neighbours are not semantic inputs.
"""
import json
import time

from . import batching

from .batching import STOP_ERRORS, batches, context_fits, planned_spans, source_span, split_source
from .provider import CONTEXT_ERROR, RUBRIC_VERSION
from .util import JCMError, digest, encode, now

VERSION = 'independent-items-v1'
PARTITION_VERSION = 'source-partition-v1'


def dependencies(item):
    return item.get('dependencies', [{'event_id': item['event_id'], 'revision': item['revision']}]
                    if 'event_id' in item else [])


def validate_dependencies(store, items):
    for item in items:
        for dep in dependencies(item):
            if store.event(dep['event_id'])['revision'] != dep['revision']:
                raise JCMError('SEMANTIC_DEPENDENCY_CHANGED')


def request(context, items, builder):
    questions = {}
    for index, item in enumerate(items):
        questions.update({f'i{index}_{name}': q for name, q in builder(f'`state.items[{index}]`', item).items()})
    return {'context': context, 'items': items}, questions


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
    start = time.perf_counter()

    def key(item):
        return digest([VERSION, RUBRIC_VERSION, kind, policy['repo_id'], epoch, policy['model'],
                       provider.lane, context, item, builder('`state.items[0]`', item)])

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
            cached = store.db.execute('SELECT * FROM semantic_items WHERE key=?', (item_key,)).fetchone()
            if cached:
                refs = [{**r, 'cached': True} for r in json.loads(cached['decisions'])]
                result['records'].append({'item': item, 'answers': json.loads(cached['answer']),
                                          'decisions': refs, 'cached': True})
                result['decisions'].extend(refs)
                result['cache_hits'] += 1
            else:
                partition = store.db.execute('SELECT answer FROM semantic_items WHERE key=?',
                    (digest([PARTITION_VERSION, item_key]),)).fetchone() if splittable and 'span' in item else None
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
            result['errors'].append(code)
            if code in STOP_ERRORS or code == CONTEXT_ERROR:
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
    return result
