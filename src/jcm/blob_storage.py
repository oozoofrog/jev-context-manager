"""Lossless chunked JSON blobs; the public key remains the original content hash."""
import hashlib
import json
from pathlib import Path

from .util import JCMError, atomic_write, digest, encode, identifier, private_dir

CHUNK_BYTES = 262144


def read_file(path):
    if path.is_symlink():
        raise JCMError('SYMLINK_BLOB_REFUSED')
    try:
        return path.read_bytes()
    except FileNotFoundError:
        raise JCMError('BLOB_MISSING') from None


def manifest(data, key):
    if digest(data) == key:
        return None  # Ordinary JSON, even if it happens to resemble a manifest.
    try:
        value = json.loads(data)
        if (set(value) != {'format', 'hash', 'bytes', 'chunks'} or
                value['format'] != 'jcm-chunks-v1' or value['hash'] != key or
                type(value['bytes']) is not int or value['bytes'] <= CHUNK_BYTES or
                not isinstance(value['chunks'], list) or not value['chunks']):
            raise ValueError()
        for part in value['chunks']:
            if set(part) != {'hash', 'bytes'} or type(part['bytes']) is not int or not 0 < part['bytes'] <= CHUNK_BYTES:
                raise ValueError()
            identifier(part['hash'])
        if sum(p['bytes'] for p in value['chunks']) != value['bytes']:
            raise ValueError()
        return value
    except (ValueError, TypeError, KeyError):
        raise JCMError('BLOB_HASH_MISMATCH') from None


def write(blobs, value):
    data = encode(value)
    key = digest(data)
    target = blobs / key
    if target.exists() or target.is_symlink():
        # Verify all chunks on retry; never acknowledge a damaged prior write.
        read(blobs, key)
        return key
    if len(data) <= CHUNK_BYTES:
        atomic_write(target, data)
        return key
    chunks = private_dir(blobs.parent / 'chunks')
    parts = []
    for start in range(0, len(data), CHUNK_BYTES):
        part = data[start:start + CHUNK_BYTES]
        chunk_key = digest(part)
        path = chunks / chunk_key
        if path.exists() or path.is_symlink():
            if digest(read_file(path)) != chunk_key:
                raise JCMError('BLOB_HASH_MISMATCH')
        else:
            atomic_write(path, part)
        parts.append({'hash': chunk_key, 'bytes': len(part)})
    # Publish the manifest only after every chunk has been atomically persisted.
    atomic_write(target, encode({'format': 'jcm-chunks-v1', 'hash': key, 'bytes': len(data), 'chunks': parts}))
    return key


def read(blobs, key):
    key = identifier(key)
    data = read_file(blobs / key)
    index = manifest(data, key)
    if index:
        if (blobs.parent / 'chunks').is_symlink():
            raise JCMError('SYMLINK_BLOB_REFUSED')
        result = bytearray()
        hasher = hashlib.sha256()
        for part in index['chunks']:
            chunk = read_file(blobs.parent / 'chunks' / part['hash'])
            if len(chunk) != part['bytes'] or digest(chunk) != part['hash']:
                raise JCMError('BLOB_HASH_MISMATCH')
            hasher.update(chunk)
            result.extend(chunk)
        if hasher.hexdigest() != key:
            raise JCMError('BLOB_HASH_MISMATCH')
        data = result
    return json.loads(data)


def collect(blobs, retained):
    chunks = set()
    for key in retained:
        value = manifest(read_file(blobs / key), key)
        if value:
            chunks.update(p['hash'] for p in value['chunks'])
    for directory, keep in ((blobs, retained), (blobs.parent / 'chunks', chunks)):
        if directory.is_symlink():
            raise JCMError('SYMLINK_STORAGE_REFUSED')
        if directory.exists():
            for path in directory.iterdir():
                if path.is_file() and path.name not in keep:
                    path.unlink()
