"""Codex marketplace binding. No project activation merely from loading a plugin."""
import json
import os
import shlex
import tomllib
import time
import uuid
from pathlib import Path

from .util import JCMError, atomic_write, encode

PLUGIN_ID = 'jev-context-manager@jcm'


def environment_binding():
    root = os.environ.get('JCM_PLUGIN_ROOT')
    if not root:
        return None
    home = Path(os.environ.get('CODEX_HOME', '~/.codex')).expanduser().resolve()
    binding = {'id': PLUGIN_ID, 'codex_home': str(home), 'root': str(Path(root).resolve())}
    require_active(binding)
    return binding


def require_active(binding, project=None):
    """Fail closed on missing, disabled, removed or unsupported persistent state.

    Deliberately do not treat an old process's environment as enablement authority.
    This guard is re-read at capture/provider boundaries and by the follower.
    """
    try:
        home, root = Path(binding['codex_home']), Path(binding['root'])
        expected = home / 'plugins/cache/jcm/jev-context-manager'
        if binding['id'] != PLUGIN_ID or root.parent != expected or not root.is_dir():
            raise ValueError()
        manifest = json.loads((root / '.codex-plugin/plugin.json').read_text())
        if manifest['name'] != 'jev-context-manager':
            raise ValueError()
        config = tomllib.loads((home / 'config.toml').read_text())
        if config.get('plugins', {}).get(PLUGIN_ID, {}).get('enabled') is not True:
            raise ValueError()
        if config.get('features', {}).get('hooks') is False:
            raise ValueError()
        if project:
            # Explicit project disable must not leave an adopted-session follower alive.
            layer = Path(project) / '.codex/config.toml'
            local = tomllib.loads(layer.read_text()) if layer.exists() else {}
            if local.get('plugins', {}).get(PLUGIN_ID, {}).get('enabled') is False:
                raise ValueError()
            if local.get('features', {}).get('hooks') is False:
                raise ValueError()
    except (KeyError, ValueError, OSError, TypeError):
        raise JCMError('PLUGIN_INACTIVE_OR_UNVERIFIED') from None


def remove_owned_hooks(config):
    """Migrate only the exact command owned by this profile; preserve other hooks."""
    target = Path(config['root']) / '.codex/hooks.json'
    if target.parent.is_symlink() or target.is_symlink():
        raise JCMError('SYMLINK_PROJECT_CONFIG_REFUSED')
    if not target.exists():
        return None
    original = target.read_bytes()
    value = json.loads(original)
    command = shlex.join(config['cli_argv'] + ['hook', '--stdin'])
    removed = 0
    for event, entries in value.get('hooks', {}).items():
        kept = []
        for entry in entries:
            handlers = entry.get('hooks', [])
            filtered = [h for h in handlers if h.get('command') != command]
            removed += len(handlers) - len(filtered)
            if filtered or not handlers:
                kept.append({**entry, 'hooks': filtered} if handlers else entry)
        value['hooks'][event] = kept
    if not removed:
        return None
    backup = Path(config['home']) / 'backups' / (config['repo_id'] + '-' + uuid.uuid4().hex + '-pre-plugin-hooks.json')
    atomic_write(backup, original)
    atomic_write(target, encode(value))
    return {'removed_handlers': removed, 'backup': str(backup)}


def bind(config, binding, migrate=False):
    from .store import Store
    require_active(binding, config['root'])
    old = config.get('plugin')
    if old and (old['id'], old['codex_home']) != (binding['id'], binding['codex_home']):
        raise JCMError('PLUGIN_BINDING_MISMATCH')
    if not old and not migrate:
        raise JCMError('PLUGIN_BIND_REQUIRED')
    argv = [str(Path(binding['root']) / 'scripts/jcm'), '--home', config['home'], '--repo', config['root']]
    if old == binding and config['cli_argv'] == argv:
        return config
    # Stop old standalone runtimes before migrating. Once bound, even leftover
    # commands running the current runtime observe the persistent plugin guard.
    store = Store(config)
    try:
        if migrate and not old:
            # Older separately installed runtimes do not know this plugin guard.
            # Their existing policy check still lets us stop them without PID kills.
            store.change_policy(enabled=False)
            from .follower import follower_status
            keys = [r[0] for r in store.db.execute('SELECT key FROM sources')]
            deadline = time.monotonic() + 4
            while any(follower_status(store, k)['running'] for k in keys):
                if time.monotonic() >= deadline:
                    raise JCMError('PLUGIN_MIGRATION_FOLLOWER_STOP_UNVERIFIED_PROJECT_DISABLED')
                time.sleep(.1)
            remove_owned_hooks(config)
        updated = store.change_policy(plugin=binding, cli_argv=argv)
        if migrate and not old:
            updated = store.change_policy(enabled=config['enabled'])
    finally:
        store.close()
    if migrate and old:
        remove_owned_hooks(config)
    return updated


def hook_main():
    import sys
    from . import config
    from .adapter import hook
    from .store import Store
    try:
        raw = sys.stdin.buffer.read(1_000_001)
        if len(raw) > 1_000_000:
            raise JCMError('HOOK_INPUT_TOO_LARGE')
        payload = json.loads(raw)
        root = payload.get('cwd')
        if not isinstance(root, str) or not Path(root).is_absolute():
            raise JCMError('HOOK_ABSOLUTE_CWD_REQUIRED')
        try:
            # The project holds only a reference; records stay outside it. Honor
            # a custom --home even when the host does not inherit JCM_HOME.
            pointer = Path(root) / '.codex/jcm.json'
            home = config.default_home()
            reference = None
            if pointer.exists():
                if pointer.is_symlink() or pointer.parent.is_symlink():
                    raise JCMError('SYMLINK_PROJECT_CONFIG_REFUSED')
                reference = json.loads(pointer.read_text())
                profile = Path(reference['profile_ref'])
                if not profile.is_absolute() or profile.parent.name != 'profiles':
                    raise JCMError('INVALID_PROFILE_REFERENCE')
                home = profile.parent.parent
            policy = config.load(home, root)
            if reference and (reference.get('repo_id') != policy['repo_id'] or
                    Path(reference['profile_ref']).resolve() !=
                    Path(policy['home']) / 'profiles' / (policy['repo_id'] + '.json')):
                raise JCMError('PROJECT_IDENTITY_MISMATCH')
        except JCMError as exc:
            if str(exc) == 'PROJECT_NOT_ENABLED':
                return 0
            raise
        if not policy.get('plugin') or not policy['enabled']:
            return 0
        binding = environment_binding()
        if not binding:
            raise JCMError('PLUGIN_BIND_REQUIRED')
        policy = bind(policy, binding)
        store = Store(policy)
        try:
            result = hook(store, payload)
        finally:
            store.close()
        sys.stdout.buffer.write(encode(result) + b'\n')
        return 0
    except (JCMError, ValueError, OSError, KeyError, TypeError, AttributeError) as exc:
        code = str(exc) if isinstance(exc, JCMError) else type(exc).__name__
        sys.stdout.buffer.write(encode({'systemMessage': 'JCM plugin capture unavailable: ' + code}) + b'\n')
        return 0  # Optional continuity never blocks the user's prompt.
