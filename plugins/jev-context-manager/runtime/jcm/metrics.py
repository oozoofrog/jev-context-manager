"""Observed transport and delivery accounting; never estimates host model usage."""
import json


def provider_metrics(store, decision_ids):
    calls, usage = [], {}
    for decision_id in set(decision_ids):
        calls.extend(dict(row) for row in store.db.execute(
            'SELECT c.*,m.elapsed FROM calls c JOIN call_metrics m ON c.id=m.call_id WHERE m.decision_id=?', (decision_id,)))
        row = store.db.execute('SELECT usage FROM decisions WHERE id=?', (decision_id,)).fetchone()
        if row and row[0]:
            for key, value in json.loads(row[0]).items():
                if type(value) in (int, float):
                    usage[key] = usage.get(key, 0) + value
    return {'transport_calls': len(calls), 'successful_transport_calls': sum(c['status'] == 'success' for c in calls),
            'failed_or_retried_calls': sum(c['status'] != 'success' for c in calls),
            'request_bytes': sum(c['bytes'] for c in calls), 'provider_usage': usage,
            'transport_seconds': sum(c['elapsed'] or 0 for c in calls)}


def delivery_metrics(store, pack_id):
    rows = store.db.execute('SELECT kind,bytes,created FROM delivery_calls WHERE pack_id=?', (pack_id,)).fetchall()
    from datetime import datetime
    pack = store.db.execute('SELECT blob FROM packs WHERE id=?', (pack_id,)).fetchone()
    complete = store.db.execute('SELECT value FROM meta WHERE key=?', ('required_read:' + pack_id,)).fetchone()
    elapsed = None
    if pack and complete:
        elapsed = (datetime.fromisoformat(json.loads(complete[0])['completed_at']) -
                   datetime.fromisoformat(store.blob(pack[0])['created_at'])).total_seconds()
    timing = store.db.execute('SELECT value FROM meta WHERE key=?', ('pack_timing:' + pack_id,)).fetchone()
    return {'dispatch_timing': json.loads(timing[0]) if timing else None, 'required_read_completion_seconds': elapsed, 'required_served_bytes': sum(r['bytes'] for r in rows if r['kind'].startswith('read_brief:')),
            'optional_served_bytes': sum(r['bytes'] for r in rows if r['kind'].startswith(('read_detail:', 'read_full:', 'read_audit:'))),
            'source_expansion_bytes': sum(r['bytes'] for r in rows if r['kind'].startswith('source:')),
            'evidence_lookup_bytes': sum(r['bytes'] for r in rows if r['kind'].startswith('lookup:')),
            'model_consumption_or_use': 'not_observable_by_jcm'}
