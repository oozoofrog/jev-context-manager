from contextlib import closing
import json
import os
import shlex
import sqlite3
import sys
import uuid
from pathlib import Path

from .snapshot import git
from .util import JCMError, atomic_write, encode, identifier, now, private_dir

EVENTS = ('SessionStart', 'UserPromptSubmit', 'PostToolUse', 'Stop',
          'PreCompact', 'PostCompact', 'SessionEnd', 'Interrupt')


def runtime_argv():
    # The managed launcher survives versioned-venv upgrades; Python may resolve
    # its executable symlink to a particular release on some platforms.
    launcher = os.environ.get('JCM_LAUNCHER')
    if launcher:
        path = Path(launcher)
        if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
            raise JCMError('INVALID_RUNTIME_LAUNCHER')
        return [str(path)]
    return [sys.executable, '-m', 'jcm']


def default_home():
    return Path(os.environ.get('JCM_HOME', '~/Library/Application Support/JCM')).expanduser()


def connect_registry(home):
    private_dir(home)
    path = Path(home) / 'registry.sqlite'
    if path.is_symlink():
        raise JCMError('SYMLINK_REGISTRY_REFUSED')
    db = sqlite3.connect(path)
    path.chmod(0o600)
    db.execute('CREATE TABLE IF NOT EXISTS projects (root TEXT PRIMARY KEY, repo_id TEXT UNIQUE, git_dir TEXT, created TEXT)')
    db.commit()
    return db


def load(home, root):
    home, root = Path(home).expanduser().resolve(), Path(root).resolve(strict=True)
    if not (home / 'registry.sqlite').is_file():
        raise JCMError('PROJECT_NOT_ENABLED')
    with closing(connect_registry(home)) as db, db:
        row = db.execute('SELECT repo_id, git_dir FROM projects WHERE root=?', (str(root),)).fetchone()
    if not row:
        raise JCMError('PROJECT_NOT_ENABLED')
    config = json.loads((home / 'profiles' / (identifier(row[0]) + '.json')).read_text())
    if config['root'] != str(root):
        raise JCMError('PROJECT_IDENTITY_MISMATCH')
    if (git(root, 'rev-parse', '--absolute-git-dir') or None) != row[1]:
        raise JCMError('GIT_IDENTITY_CHANGED_RE_REGISTER_REQUIRED')
    # Legacy profiles may contain a denial flag; Jev is now automatic for enabled projects.
    return {**config, 'allow_egress': True}


def enable(home, root, transcript_roots=None, max_calls=20):
    home, root = Path(home).expanduser().resolve(), Path(root).resolve(strict=True)
    if (root / '.codex').is_symlink():
        raise JCMError('SYMLINK_PROJECT_CONFIG_REFUSED')
    private_dir(home / 'profiles')
    with closing(connect_registry(home)) as db, db:
        existing = db.execute('SELECT repo_id FROM projects WHERE root=?', (str(root),)).fetchone()
        if existing:
            return load(home, root)
        repo_id = uuid.uuid4().hex
        config = {'schema_version': 1, 'repo_id': repo_id, 'worktree_id': uuid.uuid4().hex,
                  'root': str(root), 'home': str(home), 'enabled': True, 'epoch': 1,
                  'allow_egress': True, 'model': 'jev-1.13.0',
                  'max_daily_calls': max_calls, 'max_attempts': 3, 'max_request_bytes': 80000,
                  'candidate_ceiling': 64, 'pack_byte_ceiling': 48000,
                  'transcript_roots': [str(Path(p).expanduser().resolve()) for p in
                    (transcript_roots if transcript_roots is not None else
                     [Path(os.environ.get('CODEX_HOME', '~/.codex')).expanduser() / 'sessions'])],
                  'cli_argv': runtime_argv() + ['--home', str(home), '--repo', str(root)],
                  'created': now()}
        atomic_write(home / 'profiles' / (repo_id + '.json'), encode(config))
        db.execute('INSERT INTO projects VALUES (?,?,?,?)',
                   (str(root), repo_id, git(root, 'rev-parse', '--absolute-git-dir'), now()))
    local = root / '.codex'
    if local.is_symlink():
        raise JCMError('SYMLINK_PROJECT_CONFIG_REFUSED')
    local.mkdir(exist_ok=True)
    pointer = local / 'jcm.json'
    if pointer.exists():
        # Existing configuration is never silently replaced.
        atomic_write(home / 'backups' / (repo_id + '-jcm.json'), pointer.read_bytes())
    atomic_write(pointer, encode({'schema_version': 1, 'repo_id': repo_id,
                                 'profile_ref': str(home / 'profiles' / (repo_id + '.json'))}))
    return config


def install_hooks(config):
    if config.get('plugin'):
        from .plugin import require_active
        require_active(config['plugin'], config['root'])
        return {'changed': False, 'source': 'plugin', 'trust': 'review_required',
                'path': str(Path(config['plugin']['root']) / 'hooks/hooks.json')}
    root, home = Path(config['root']), Path(config['home'])
    target = root / '.codex/hooks.json'
    if target.parent.is_symlink() or target.is_symlink():
        raise JCMError('SYMLINK_PROJECT_CONFIG_REFUSED')
    layer = target.parent / 'config.toml'
    created_layer = not layer.exists()
    if created_layer:
        atomic_write(layer, b'# Project-scoped JCM lifecycle adapter.\n[features]\nhooks = true\n')
    original = target.read_bytes() if target.exists() else None
    value = json.loads(original) if original else {'hooks': {}}
    hooks = value.setdefault('hooks', {})
    command = shlex.join(config['cli_argv'] + ['hook', '--stdin'])
    for event in EVENTS:
        entries = hooks.setdefault(event, [])
        if any(h.get('command') == command for entry in entries for h in entry.get('hooks', [])):
            continue
        entries.append({'hooks': [{'type': 'command', 'command': command,
                                   'timeout': 3 if event in ('SessionEnd', 'Interrupt') else 20}]})
    data = encode(value)
    if original == data:
        return {'changed': False, 'path': str(target), 'trust': 'not_verified'}
    backup = home / 'backups' / (config['repo_id'] + '-' + uuid.uuid4().hex + '-hooks.json')
    if original is not None:
        atomic_write(backup, original)
    atomic_write(target, data)
    return {'changed': True, 'path': str(target), 'backup': str(backup) if original is not None else None,
            'trust': 'review_required', 'global_config_changed': False, 'created_project_config': created_layer}


def save_policy(config, **changes):
    updated = {**config, **changes, 'allow_egress': True, 'epoch': config['epoch'] + 1}
    atomic_write(Path(config['home']) / 'profiles' / (config['repo_id'] + '.json'), encode(updated))
    return updated
