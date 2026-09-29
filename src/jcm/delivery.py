"""Lossless, immutable pages for large recovery packs; receipts track served bytes."""
import shlex

from .batching import text_spans
from .util import JCMError, digest, encode


def envelope(store, pack, entries, page, count, served, next_page, reconciliation='consistent'):
    complete = served == count
    command = (shlex.join(store.config['cli_argv'] + ['read', '--pack', pack['pack_id'], '--page', str(next_page)] +
                         (['--view', pack['view']] if pack.get('view') else []))
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
