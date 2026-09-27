"""Undo only a newly created fixture's Codex trust entry, preserving other bytes."""
import json
import re
import tomllib
from pathlib import Path

from jcm.util import JCMError, atomic_write


def remove_fixture_trust(config_path, fixture_root, backup_path):
    path = Path(config_path)
    original = path.read_bytes()
    atomic_write(backup_path, original)
    data = tomllib.loads(original.decode())
    key = str(Path(fixture_root).resolve())
    project = data.get('projects', {}).get(key)
    if project is None:
        return {'removed': False, 'reason': 'absent'}
    if project != {'trust_level': 'trusted'}:
        raise JCMError('FIXTURE_CONFIG_CHANGED_OUTSIDE_EXPECTED_TRUST_ENTRY')
    heading = '[projects.' + json.dumps(key, ensure_ascii=False) + ']'
    pattern = re.compile(r'^' + re.escape(heading) + r'\n(?:(?!\[).*(?:\n|$))*', re.M)
    updated, count = pattern.subn('', original.decode(), count=1)
    if count != 1:
        raise JCMError('FIXTURE_CONFIG_TABLE_NOT_IDENTIFIED')
    expected = data.copy()
    expected['projects'] = dict(data['projects'])
    del expected['projects'][key]
    if tomllib.loads(updated) != expected:
        raise JCMError('UNRELATED_CONFIG_CHANGE_REFUSED')
    atomic_write(path, updated.encode())
    return {'removed': True, 'project': key, 'other_config_preserved': True}
