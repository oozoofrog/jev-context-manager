import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path


class JCMError(Exception):
    """Expected, safe-to-display error code (never a secret-containing payload)."""


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else encode(value)).hexdigest()


def private_dir(path):
    path = Path(path)
    if path.is_symlink():
        raise JCMError('SYMLINK_STORAGE_REFUSED')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.stat().st_uid != os.getuid():
        raise JCMError('STORAGE_OWNER_MISMATCH')
    path.chmod(0o700)
    return path


def atomic_write(path, data):
    path = Path(path)
    if path.is_symlink():
        raise JCMError('SYMLINK_WRITE_REFUSED')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.jcm-')
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{32,64}', value):
        raise JCMError('INVALID_OPAQUE_IDENTIFIER')
    return value


def redact(value):
    """Defense in depth; scope admission is still required before this scrubber."""
    count = 0
    secrets = [v for k, v in os.environ.items()
               if len(v) >= 8 and re.search(r'(API_KEY|TOKEN|SECRET|PASSWORD)$', k, re.I)]

    def visit(item):
        nonlocal count
        if isinstance(item, dict):
            result = {}
            for key, val in item.items():
                if re.search(r'^(authorization|api[_-]?key|password|secret|access_token)$', key, re.I):
                    result[key] = '[REDACTED]'
                    count += 1
                else:
                    result[key] = visit(val)
            return result
        if isinstance(item, list):
            return [visit(v) for v in item]
        if isinstance(item, str):
            for secret in secrets:
                if secret in item:
                    item = item.replace(secret, '[REDACTED]')
                    count += 1
            item, n = re.subn(r'(?i)(?:Bearer\s+[A-Za-z0-9_.\-]{8,}|sk-[A-Za-z0-9_\-]{8,}|(?:api[_-]?key|password|secret)\s*[=:]\s*[\"\']?[^\s\"\',}]+)',
                              '[REDACTED]', item)
            count += n
            return item
        return item

    return visit(value), count
