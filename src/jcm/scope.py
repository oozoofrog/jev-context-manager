"""Capture admission precedes blobs, jobs, provider requests and derived data."""
import hashlib
import json
from pathlib import Path

from .util import JCMError, encode
from .transcript_io import read_record


def boundary(store, session):
    scope = store.policy(require_enabled=False).get('capture_scope')
    if not scope:
        return None  # Legacy profiles keep their existing project scope.
    if scope['session'] == session:
        return scope
    row = store.db.execute('SELECT value FROM meta WHERE key=?', ('scope_session:' + session,)).fetchone()
    return json.loads(row[0]) if row else False


def admit_prompt(store, session, turn, identity):
    """Called only on a current host UserPromptSubmit or verified skill entry."""
    if boundary(store, session) is False:
        value = {'session': session, 'mode': 'from_invocation', 'turn': turn,
                 'identity': identity, 'preview_turns': []}
        store.db.execute('INSERT OR IGNORE INTO meta VALUES (?,?)',
                         ('scope_session:' + session, encode(value).decode()))


def prefix_hash(path, length):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        while length:
            block = stream.read(min(length, 1_048_576))
            if not block:
                return None
            result.update(block)
            length -= len(block)
    return result.hexdigest()


def start_offset(store, row, stream=None):
    """Resolve by source identity and prefix proof, never wall time/file size.

    Cached metadata contains only an anchor location and hash. On rotation or
    rewritten prefixes, re-find the exact invocation in ordered session pages.
    If its source disappeared, stop admitting that source and expose the gap.
    """
    rule = boundary(store, row['session'])
    if rule is None or (rule and rule['mode'] == 'whole_session'):
        return 0
    if rule is False:
        return None
    from .adapter import MAX_LINE_BYTES, public_item
    from .bootstrap import discover_all
    paths = discover_all(store, row['session'])
    key = 'scope_anchor:' + row['session']
    cached = store.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    anchor = json.loads(cached[0]) if cached else None
    if anchor:
        path = Path(anchor['path'])
        if (anchor['identity'] != rule['identity'] or path not in paths or
                path.stat().st_ino != anchor['inode'] or
                prefix_hash(path, anchor['end']) != anchor['prefix_hash']):
            anchor = None
    if not anchor:
        for path in paths:
            prefix = hashlib.sha256()
            with path.open('rb') as anchor_stream:
                while True:
                    offset = anchor_stream.tell()
                    line = read_record(anchor_stream, prefix=prefix)
                    if not line or not line['complete']:
                        break
                    try:
                        if line.get('error'):
                            raise JCMError(line['error'])
                        item = public_item(line['value'], row['session'])
                    except JCMError:
                        continue
                    if item and item['role'] == 'user' and item['turn'] == rule['turn'] and item['identity'] == rule['identity']:
                        anchor = {'path': str(path), 'inode': path.stat().st_ino, 'start': offset,
                                  'end': anchor_stream.tell(), 'prefix_hash': prefix.hexdigest(), 'identity': rule['identity']}
                        break
            if anchor:
                store.db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, encode(anchor).decode()))
                break
    if not anchor:
        store.gap('SCOPE_ANCHOR_NOT_OBSERVABLE', row['session'])
        return None
    store.db.execute("DELETE FROM gaps WHERE code='SCOPE_ANCHOR_NOT_OBSERVABLE' AND detail=?", (row['session'],))
    current, origin = paths.index(Path(row['path'])), paths.index(Path(anchor['path']))
    if stream is not None and current >= origin:
        # Use the same descriptor that capture will read. A path can rotate
        # between discovery and open; an offset from another generation cannot
        # authorize bytes in this descriptor.
        position = stream.tell()
        try:
            stream.seek(0)
            if current == origin:
                remaining, proof = anchor['end'], hashlib.sha256()
                while remaining:
                    block = stream.read(min(remaining, 1_048_576))
                    if not block:
                        raise JCMError('SCOPE_SOURCE_CHANGED_DURING_READ')
                    proof.update(block)
                    remaining -= len(block)
                if proof.hexdigest() != anchor['prefix_hash']:
                    raise JCMError('SCOPE_SOURCE_CHANGED_DURING_READ')
            else:
                with Path(row['path']).open('rb') as current_file:
                    if stream.readline(MAX_LINE_BYTES + 1) != current_file.readline(MAX_LINE_BYTES + 1):
                        raise JCMError('SCOPE_SOURCE_CHANGED_DURING_READ')
        finally:
            stream.seek(position)
    return anchor['start'] if current == origin else 0 if current > origin else None


def allows_item(rule, item):
    return not (rule and rule['mode'] == 'from_invocation' and item['role'] != 'user'
                and (item['turn'] in rule.get('preview_turns', []) or item['identity'] in rule.get('preview_ids', [])))
