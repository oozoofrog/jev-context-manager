"""Lossless, immutable pages for large recovery packs; receipts track served bytes."""
import shlex

from .batching import text_spans
from .util import JCMError, digest, encode


def envelope(store, pack, entries, page, count, served, next_page, reconciliation='consistent'):
    complete = served == count
    command = (shlex.join(store.config['cli_argv'] + ['read', '--pack', pack['pack_id'], '--page', str(next_page)] +
                         (['--view', pack['view']] if pack.get('view') else []) +
                         (['--retained-context', pack['retained_context']] if pack.get('retained_context') else []))
               if next_page is not None else None)
    return {'origin': 'jcm', 'pack_id': pack['pack_id'], 'dispatch': pack['dispatch'], 'quality': pack['quality'],
            'session_id': pack['session_id'], 'view': pack.get('view', 'audit'), 'stage': 'read_served' if complete else 'reading',
            'delivery': 'read_served' if complete else 'page_served', 'delivery_coverage': 'unknown',
            'recovery_success': 'not_attested', 'current_reconciliation': reconciliation,
            'source_use_policy': pack['source_use_policy'], 'content_hash': pack['page_manifest']['content_hash'],
            'entries': entries, 'page_hash': digest(entries),
            'pagination': {'page': page, 'page_count': count, 'served_pages': served,
                           'remaining_pages': count - served, 'all_pages_served': complete},
            'next_read_command': command}


def paginate(store, pack):
    """Split every field, including large snapshots, without changing the source data."""
    ceiling = store.config['pack_byte_ceiling']
    content_hash = digest(pack)
    sizing_pack = {**pack, 'page_manifest': {'content_hash': content_hash}}
    # Size with long counters/next-command and room for bootstrap's wrapper fields.
    sample = envelope(store, sizing_pack, [], 999999999, 999999999, 0, 999999999)
    # Only entries vary. Page/content digests have fixed encoded length, so
    # sizing need not hash each trial batch or reconstruct the whole envelope.
    envelope_bytes = len(encode(sample)) - len(encode([]))
    def fits(entries):
        return envelope_bytes + len(encode(entries)) + 256 <= ceiling
    if not fits([]):
        raise JCMError('PACK_BUDGET_EXCEEDED_REQUIRES_SCOPED_READ')

    def entries(value, path, source=None):
        entry = {'path': path, 'value': value}
        if source:
            entry['event_id'] = source
        if fits([entry]):
            yield entry
        elif isinstance(value, (dict, list)):
            empty = {**entry, 'value': {} if isinstance(value, dict) else []}
            if not fits([empty]):
                raise JCMError('PACK_BUDGET_EXCEEDED_REQUIRES_SCOPED_READ')
            yield empty
            items = value.items() if isinstance(value, dict) else enumerate(value)
            for key, child in items:
                yield from entries(child, path + [key], source)
        elif isinstance(value, str):
            def part(start, end):
                return {**{k:v for k,v in entry.items() if k != 'value'},
                        'text': value[start:end], 'start': start, 'end': end, 'total_chars': len(value)}
            try:
                for start, end in text_spans(value, lambda a,b: fits([part(a,b)])):
                    yield part(start, end)
            except JCMError:
                raise JCMError('PACK_BUDGET_EXCEEDED_REQUIRES_SCOPED_READ') from None
        else:
            raise JCMError('PACK_BUDGET_EXCEEDED_REQUIRES_SCOPED_READ')

    pages, current = [], []
    def add(entry):
        nonlocal current
        if current and not fits(current + [entry]):
            pages.append(current)
            current = []
        current.append(entry)
    priority = ['origin', 'pack_id', 'session_id', 'request', 'dispatch', 'quality',
                'semantic_error', 'coverage', 'source_use_policy', 'current_goal',
                'evidence_delivery', 'record_defaults', 'source_expansion_argv', 'selected_records']
    priority = [key for key in priority if key in pack]
    for key in priority + [k for k in pack if k not in priority]:
        value = pack[key]
        if key == 'selected_records':
            add({'path': [key], 'value': []})
            for index, record in enumerate(value):
                for entry in entries(record, [key, index], record['event_id']):
                    add(entry)
        else:
            for entry in entries(value, [key]):
                add(entry)
    if current:
        pages.append(current)
    return {'version': 1, 'content_hash': content_hash, 'byte_ceiling': ceiling,
            'pages': pages}


def delta(store, stored, retained_context):
    """Caller attests the full base is still in context. A receipt alone is insufficient."""
    from .source_read import bound_pack
    base_id, separator, content_hash = retained_context.partition(':')
    if not separator:
        raise JCMError('INVALID_RETAINED_CONTEXT')
    base = bound_pack(store, base_id)
    old = base.get('context_views', {}).get('brief', {})
    current = stored['context_views']['brief']
    if (not old or old.get('page_manifest', {}).get('content_hash') != content_hash or
            base['session_id'] != stored['session_id'] or
            base['task_frame']['task_id'] != stored['task_frame']['task_id'] or
            not store.db.execute('SELECT 1 FROM meta WHERE key=?', ('required_read:' + base_id,)).fetchone()):
        raise JCMError('RETAINED_CONTEXT_NOT_APPLICABLE')
    if (base.get('query_context', {}).get('contract_version') != 1 or
            stored.get('query_context', {}).get('contract_version') != 1):
        raise JCMError('RETAINED_CONTEXT_CONTRACT_UNVERIFIED')
    from .representations import validate_contract
    if validate_contract(base['query_context'], old) or validate_contract(stored['query_context'], current):
        raise JCMError('DELIVERY_CONTRACT_VIOLATION')
    previous = {r['event_id']: r for r in old['selected_records']}
    present = {r['event_id']: r for r in current['selected_records']}
    # Default fields affect interpretation of unchanged records, so send all
    # records if their defaults changed. Every changed field replaces its base.
    changed = current.get('record_defaults') != old.get('record_defaults')
    result = {k: v for k, v in current.items() if k not in ('selected_records', 'page_manifest') and
              (k in ('origin', 'pack_id', 'session_id', 'dispatch', 'quality', 'source_use_policy', 'snapshot', 'view') or old.get(k) != v)}
    result.update(retained_context=retained_context, delivery_mode='delta',
        merge_policy='Remove removed_fields, then replace supplied fields; replace or insert each selected_record by event_id '
                     '(do not merge record fields); remove removed_record_ids; reorder by record_order. '
                     'Only valid while the complete base and all intervening deltas remain in the current model context.',
        record_order=list(present),
        removed_record_ids=sorted(set(previous) - set(present)),
        removed_fields=sorted(set(old) - set(current) - {'page_manifest'}),
        selected_records=[r for eid, r in present.items() if changed or previous.get(eid) != r])
    if apply_delta(old, result) != {k:v for k,v in current.items() if k != 'page_manifest'}:
        raise JCMError('DELTA_RECONSTRUCTION_MISMATCH')
    return result


def apply_delta(base, change):
    """Reference decoder: whole records and explicit ordering, never shallow merge."""
    import copy
    value = copy.deepcopy({k:v for k,v in base.items() if k != 'page_manifest'})
    for key in change['removed_fields']:
        value.pop(key, None)
    metadata = {'retained_context','delivery_mode','merge_policy','removed_record_ids','removed_fields','record_order','selected_records','page_manifest'}
    value.update({k:copy.deepcopy(v) for k,v in change.items() if k not in metadata})
    records = {r['event_id']:r for r in value.get('selected_records', [])}
    for eid in change['removed_record_ids']:
        records.pop(eid, None)
    records.update({r['event_id']:copy.deepcopy(r) for r in change['selected_records']})
    order = change['record_order']
    if len(order) != len(set(order)) or set(order) != set(records):
        raise JCMError('DELTA_RECORD_ORDER_INVALID')
    value['selected_records'] = [records[eid] for eid in order]
    return value


def reconstruct_pages(responses):
    """Reassemble the actual public API's page entries without interpretation."""
    import copy
    if len(responses) == 1 and 'pack' in responses[0]:
        return copy.deepcopy({k:v for k,v in responses[0]['pack'].items() if k != 'page_manifest'})
    result = {}
    for response in responses:
        for entry in response['entries']:
            path = entry['path']; node = result
            for i, key in enumerate(path[:-1]):
                if isinstance(node, list):
                    while len(node) <= key:
                        node.append(None)
                    if node[key] is None:
                        node[key] = [] if isinstance(path[i+1], int) else {}
                elif key not in node:
                    node[key] = [] if isinstance(path[i+1], int) else {}
                node = node[key]
            key = path[-1]
            if isinstance(node, list):
                while len(node) <= key:
                    node.append(None)
            if 'value' in entry:
                node[key] = copy.deepcopy(entry['value'])
            else:
                previous = node[key] if (isinstance(node,list) or key in node) else ''
                previous = previous or ''
                if len(previous) != entry['start']:
                    raise JCMError('DELIVERY_PAGE_SPAN_GAP')
                node[key] = previous + entry['text']
    return result
