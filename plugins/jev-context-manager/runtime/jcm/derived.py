"""Compact exact copied source text in tool results without altering audit blobs."""
import json

from .util import digest


def references(store, event, text):
    refs = set()

    def visit(value, depth=0):
        if depth > 32:
            return value  # Preserve deeply nested text; never drop it.
        if isinstance(value, str):
            # Only replace a whole, byte-identical earlier source, and only
            # when its reference is shorter. Novel surrounding text survives.
            if len(value) > 180:
                row = store.db.execute('SELECT t.event_id FROM event_texts t JOIN events e ON e.id=t.event_id WHERE t.text_hash=? AND e.seq<? ORDER BY e.seq LIMIT 1',
                                       (digest(value), event['seq'])).fetchone()
                if row and store.blob(store.event(row[0])['blob']).get('text') == value:
                    refs.add(row[0])
                    return {'jcm_source_reference': row[0], 'text_hash': digest(value)}
            try:
                nested = json.loads(value)
            except (ValueError, RecursionError):
                return value
            if isinstance(nested, (dict, list)):
                changed = visit(nested, depth + 1)
                if changed != nested:
                    return json.dumps(changed, ensure_ascii=False, separators=(',', ':'))
            return value
        if isinstance(value, dict):
            return {k: visit(v, depth + 1) for k,v in value.items()}
        if isinstance(value, list):
            return [visit(v, depth + 1) for v in value]
        return value

    try:
        original = json.loads(text)
    except (ValueError, RecursionError):
        return text, []
    result = visit(original)
    return (json.dumps(result, ensure_ascii=False, separators=(',', ':')), sorted(refs)) if refs else (text, [])
