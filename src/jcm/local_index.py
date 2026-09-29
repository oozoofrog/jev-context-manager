"""Revisioned local lexical/path/symbol search; ranking never establishes scope."""
import re
import unicodedata

from .util import digest


def terms(text):
    text = unicodedata.normalize('NFKC', text).casefold()
    result = set(re.findall(r'[\w./:-]+', text))
    for word in re.findall(r'[가-힣]+', text):
        for width in (2, 3):
            result.update(word[i:i + width] for i in range(len(word) - width + 1))
    return result


def refresh(store, materials, epoch):
    updated = 0
    pending = []

    def commit():
        nonlocal updated
        if not pending:
            return
        store.policy(epoch)
        store.db.execute('BEGIN IMMEDIATE')
        try:
            for material, signature, words in pending:
                if store.event(material['event_id'])['revision'] != material['revision']:
                    from .util import JCMError
                    raise JCMError('SOURCE_CHANGED_DURING_INDEX')
                store.db.execute('DELETE FROM source_terms WHERE event_id=?', (material['event_id'],))
                store.db.executemany('INSERT INTO source_terms VALUES (?,?)', ((word, material['event_id']) for word in words))
                store.db.execute('INSERT OR REPLACE INTO source_index VALUES (?,?,?)',
                                 (material['event_id'], material['revision'], signature))
            store.policy(epoch)
            store.db.execute('COMMIT')
            updated += len(pending)
            pending.clear()
        except BaseException:
            store.db.execute('ROLLBACK')
            raise

    for material in materials:
        signature = digest(material['text'])
        previous = store.db.execute('SELECT revision,text_hash FROM source_index WHERE event_id=?',
                                    (material['event_id'],)).fetchone()
        if previous and tuple(previous) == (material['revision'], signature):
            continue
        pending.append((material, signature, terms(material['text'])))
        # Transaction sizing only: every changed source is indexed. Keep writer
        # leases short without spawning Git and committing once per record.
        if len(pending) >= 128:
            commit()
    commit()
    store.policy(epoch)
    return updated


def search(store, text):
    scores = {}
    # One bound parameter per term avoids SQLite variable-count limits without
    # imposing a candidate limit or dropping less frequent matches.
    for term in terms(text):
        for row in store.db.execute('SELECT event_id FROM source_terms WHERE term=?', (term,)):
            scores[row[0]] = scores.get(row[0], 0) + 1
    return [event_id for event_id, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))]
