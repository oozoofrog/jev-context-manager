"""Read JSONL without a line-size admission limit or an in-memory line buffer.

Spooling bounds raw read buffers. JSON decoding still materializes one record;
this is not a constant-memory parser for arbitrarily large JSON values.
"""
import hashlib
import json
import tempfile

BLOCK_BYTES = 262144


def read_record(stream, end=None, prefix=None):
    start = stream.tell()
    checksum = hashlib.sha256()
    complete = False
    with tempfile.SpooledTemporaryFile(max_size=BLOCK_BYTES, mode='w+b') as spool:
        while end is None or stream.tell() < end:
            chunk = stream.readline(BLOCK_BYTES if end is None else min(BLOCK_BYTES, end - stream.tell()))
            if not chunk:
                break
            spool.write(chunk)
            if prefix is not None:
                prefix.update(chunk)
            complete = chunk.endswith(b'\n')
            checksum.update(chunk[:-1] if complete else chunk)
            if complete:
                break
        if stream.tell() == start:
            return None
        result = {'start': start, 'end': stream.tell(), 'hash': checksum.hexdigest(), 'complete': complete}
        if complete:
            spool.seek(0)
            try:
                value = json.load(spool)
                if not isinstance(value, dict):
                    raise ValueError()
                result['value'] = value
            except (ValueError, UnicodeDecodeError):
                result['error'] = 'TRANSCRIPT_MALFORMED_LINE'
        return result
