"""Bounded original reads and revision-specific evidence receipts."""
import json
import shlex
import uuid

from .batching import text_spans
from .util import JCMError, digest, encode, identifier, now


def inspect_source(store, event_id, page=1, raw=False, pack_id=None):
    policy = store.policy()
    material = store.material(store.event(event_id), raw=raw)
    text = material['text']
    ceiling = policy['pack_byte_ceiling']
    if pack_id:
        pack = store.db.execute('SELECT * FROM packs WHERE id=?', (identifier(pack_id),)).fetchone()
        if not pack or pack['invalid'] or pack['epoch'] != policy['epoch']:
            raise JCMError('PACK_MISSING_OR_INVALIDATED')
    def response(start, end, next_page):
        args = ['inspect', '--record', event_id, '--page', str(next_page)]
        if raw:
            args.append('--raw')
        if pack_id:
            args += ['--pack', pack_id]
        return {'origin': 'jcm', 'source': {**material, 'text': text[start:end]},
                'span': {'start': start, 'end': end, 'total_chars': len(text), 'source_hash': digest(text)},
                'next_read_command': shlex.join(store.config['cli_argv'] + args) if next_page else None}
    spans = list(text_spans(text, lambda a,b: len(encode(response(a,b,999999999))) + 256 <= ceiling))
    if type(page) is not int or page < 1 or page > len(spans):
        raise JCMError('INVALID_SOURCE_PAGE')
    start, end = spans[page - 1]
    result = response(start, end, page + 1 if page < len(spans) else None)
    receipt_key = 'source_pages:' + digest([event_id, material['revision'], digest(text), raw, ceiling])
    store.db.execute('BEGIN IMMEDIATE')
    try:
        store.policy(policy['epoch'])
        if store.event(event_id)['revision'] != material['revision']:
            raise JCMError('SEMANTIC_DEPENDENCY_CHANGED')
        previous = store.db.execute('SELECT value FROM meta WHERE key=?', (receipt_key,)).fetchone()
        served = set(json.loads(previous[0]) if previous else []) | {page}
        store.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (receipt_key, encode(sorted(served)).decode()))
        complete = len(served) == len(spans)
        if complete and not raw:
            store.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', ('source_read:' + event_id,
                encode({'revision': material['revision'], 'hash': digest(text)}).decode()))
        result['pagination'] = {'page': page, 'page_count': len(spans), 'all_pages_served': complete}
        if pack_id:
            kind = 'source:' + receipt_key + ':' + str(page)
            store.db.execute('INSERT INTO delivery_calls VALUES (?,?,?,?,?)',
                             (uuid.uuid4().hex, pack_id, kind, len(encode(result)), now()))
            if not store.db.execute('SELECT 1 FROM receipts WHERE pack_id=? AND kind=?', (pack_id, kind)).fetchone():
                store.db.execute('INSERT INTO receipts VALUES (?,?,?,?,?,?)',
                    (uuid.uuid4().hex, pack_id, kind, len(encode(result)), digest(result), now()))
        store.db.execute('COMMIT')
    except BaseException:
        store.db.execute('ROLLBACK'); raise
    return result
