#!/usr/bin/env python3
"""Reproducible JCM release/install; concise output, durable logs, exact-ref checks."""
import argparse
from contextlib import closing
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import sqlite3
import subprocess
import sys
import time
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'oozoofrog/jev-context-manager'
SELECTOR = 'jev-context-manager@jcm'
WORKFLOW = 'Verify JCM distribution'


class ReleaseError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise ReleaseError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def versions(root):
    package = tomllib.loads((root / 'pyproject.toml').read_text())['project']['version']
    plugin = json.loads((root / 'plugins/jev-context-manager/.codex-plugin/plugin.json').read_text())['version']
    runtime = re.search(r'__version__ = "([^"]+)"', (root / 'src/jcm/__init__.py').read_text()).group(1)
    require(package == runtime and package.replace('.dev', '-dev.') == plugin,
            'Package/runtime/plugin versions disagree')
    require(re.fullmatch(r'\d+\.\d+\.\d+(?:-dev\.\d+)?', plugin), 'Unsupported release version')
    return package, plugin, 'v' + plugin


def public_source(path):
    parts = Path(path).parts
    return (path in {'README.md', 'pyproject.toml', '.gitignore', 'install.sh'} or
            parts[0] in {'src', 'tests', 'scripts', 'docs', 'plugins', 'skills', '.github', '.agents'})


def other_plugins(inventory):
    return sorted((p for p in inventory['installed'] if p['pluginId'] != SELECTOR),
                  key=lambda p: p['pluginId'])


def backup_store(source, target):
    """SQLite online snapshots followed by blobs; never copy a live WAL as a DB."""
    if not source.exists():
        return
    target.mkdir(parents=True, exist_ok=True)
    for path in sorted(source.iterdir(), key=lambda p: p.suffix != '.sqlite'):
        dest = target / path.name
        require(not path.is_symlink(), 'Storage backup refuses symlinks: ' + str(path))
        if path.is_dir():
            backup_store(path, dest)
        elif path.suffix == '.sqlite':
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as original:
                with closing(sqlite3.connect(dest)) as copy:
                    original.backup(copy)
                    copy.execute('PRAGMA journal_mode=DELETE')
                    require(copy.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'Backup integrity failed')
        elif not path.name.endswith(('-wal', '-shm')):
            shutil.copy2(path, dest)


class Release:
    def __init__(self, args, root=ROOT):
        self.args, self.root = args, root
        self.package, self.version, self.tag = versions(root)
        self.home = Path(os.environ.get('CODEX_HOME', '~/.codex')).expanduser().resolve()
        stamp = time.strftime('%Y%m%d-%H%M%S')
        self.directory = (Path(args.resume).resolve() if args.resume else
                          root / '.task-notes/releases' / (self.tag + '-' + stamp))
        require(self.directory.is_relative_to(root / '.task-notes/releases'), 'Resume directory must belong to this checkout')
        self.directory.mkdir(parents=True, exist_ok=True)
        self.state_path = self.directory / 'result.json'
        self.state = json.loads(self.state_path.read_text()) if args.resume else {
            'version': self.version, 'repository': REPOSITORY, 'steps': {}, 'pass': False}
        require(self.state['version'] == self.version and self.state['repository'] == REPOSITORY,
                'Resume version/repository mismatch')
        self.number = len(list(self.directory.glob('*.log')))
        self.env = {**os.environ, 'PYTHONPATH': str(root / 'src')}
        # Unit/packaging checks never perform inference. Live API evidence is a
        # separate opt-in fixture, not something every release repeats.
        self.env.pop('TYPESAFE_API_KEY', None)

    def save(self):
        write_json(self.state_path, self.state)

    def progress(self, name, result):
        self.state['steps'][name] = result
        self.save()
        print(json.dumps({'stage': name, 'status': 'passed', 'result': str(self.state_path)}), flush=True)

    def run(self, name, argv, check=True, timeout=300, env=None):
        self.number += 1
        log = self.directory / f'{self.number:03d}-{name}.log'
        try:
            result = subprocess.run(argv, cwd=self.root, env=env or self.env,
                                    capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            log.write_bytes((error.stdout or b'') + b'\n' + (error.stderr or b''))
            raise ReleaseError(name + ' timed out; ' + str(log)) from None
        log.write_text(result.stdout + '\n' + result.stderr)
        if check and result.returncode:
            raise ReleaseError(name + ' failed; ' + str(log))
        return result

    def json(self, name, argv, **kwargs):
        return json.loads(self.run(name, argv, **kwargs).stdout)

    def git(self, *args, check=True):
        return self.run('git-' + args[0], ['git', *args], check=check).stdout.strip()

    def inputs(self):
        tracked = self.run('tracked-files', ['git', 'ls-files', '-z']).stdout.split('\0')
        untracked = self.run('untracked-files', ['git', 'ls-files', '--others', '--exclude-standard', '-z']).stdout.split('\0')
        explicit = set(self.args.include)
        for path in explicit:
            require(not Path(path).is_absolute() and '..' not in Path(path).parts, 'Include must be repo-relative')
        names = sorted(set(p for p in tracked + untracked if p and (p in tracked or public_source(p) or p in explicit)))
        digest = hashlib.sha256()
        for name in names:
            path = self.root / name
            require(not path.is_symlink(), 'Release source contains a symlink: ' + name)
            digest.update(name.encode() + b'\0')
            digest.update(path.read_bytes() if path.is_file() else b'<deleted>')
        return digest.hexdigest(), names

    def preflight(self):
        require(self.git('branch', '--show-current') == 'main', 'Release requires main')
        remote = self.git('remote', 'get-url', 'origin').removesuffix('.git')
        require(remote in {f'git@github.com:{REPOSITORY}', f'https://github.com/{REPOSITORY}'}, 'Unexpected origin')
        require(not self.git('diff', '--name-only', '--diff-filter=U'), 'Unresolved merge conflicts')
        for tool in ('gh', 'codex'):
            require(shutil.which(tool), tool + ' is unavailable')
        info = self.json('repository', ['gh', 'repo', 'view', REPOSITORY, '--json', 'nameWithOwner'])
        require(info['nameWithOwner'] == REPOSITORY, 'Unexpected GitHub repository')
        if self.args.publish:
            notes = Path(self.args.notes).resolve()
            require(notes.is_file() and notes.is_relative_to(self.root), 'Release notes must be a repository file')
            require(not self.state.get('notes_sha256') or self.state['notes_sha256'] == sha(notes), 'Release notes changed during resume')
            self.state['notes_sha256'] = sha(notes)
        self.fingerprint, self.names = self.inputs()
        previous = self.state.get('fingerprint')
        require(not previous or previous == self.fingerprint, 'Release inputs changed; start a new run')
        self.state['fingerprint'] = self.fingerprint
        self.save()

    def validate(self):
        prior = self.state['steps'].get('validate')
        if prior and Path(prior['wheel']).is_file() and sha(prior['wheel']) == prior['wheel_sha256']:
            return prior
        self.run('bundle', [sys.executable, 'scripts/build_plugin.py', '--check'])
        self.run('tests', [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests'], timeout=600)
        wheels = self.directory / 'dist'
        self.run('wheel', [sys.executable, '-m', 'pip', 'wheel', '--no-deps', '-w', str(wheels), '.'])
        wheel = wheels / f'jev_context_manager-{self.package}-py3-none-any.whl'
        with zipfile.ZipFile(wheel) as archive:
            require(all(archive.read('jcm/' + p.name) == p.read_bytes() for p in (self.root / 'src/jcm').glob('*.py')),
                    'Wheel differs from source')
        self.run('diff-check', ['git', 'diff', '--check'])
        result = {'wheel': str(wheel), 'wheel_sha256': sha(wheel)}
        self.progress('validate', result)
        return result

    def publish_commit(self):
        require(self.inputs()[0] == self.fingerprint, 'Source changed during validation')
        changed = set(self.run('changed-files', ['git', 'diff', 'HEAD', '--name-only', '-z']).stdout.split('\0')) - {''}
        untracked = set(self.run('new-files', ['git', 'ls-files', '--others', '--exclude-standard', '-z']).stdout.split('\0')) - {''}
        include = sorted(changed | (untracked & set(self.names)))
        if include:
            require(self.args.commit_message, 'Uncommitted release inputs require --commit-message')
            self.run('stage', ['git', 'add', '--', *include])
            self.run('commit', ['git', 'commit', '-m', self.args.commit_message])
        head = self.git('rev-parse', 'HEAD')
        require(not self.state.get('commit') or self.state['commit'] == head, 'Resume commit changed')
        self.state['commit'] = head
        self.save()
        tag = self.git('rev-parse', '--verify', self.tag + '^{commit}', check=False)
        require(not tag or tag == head, 'Existing tag points to a different commit; never move release tags')
        if not tag:
            self.run('tag', ['git', 'tag', '-a', self.tag, '-m', 'JCM ' + self.version, head])
        self.run('push', ['git', 'push', '--atomic', 'origin', 'HEAD:refs/heads/main', 'refs/tags/' + self.tag])
        remote = self.git('ls-remote', 'origin', 'refs/heads/main', 'refs/tags/' + self.tag + '^{}')
        require(all(line.split()[0] == head for line in remote.splitlines()) and len(remote.splitlines()) == 2,
                'Remote branch/tag do not match the release commit')
        self.progress('publish_commit', {'commit': head, 'tag': self.tag})
        return head

    def ci(self, head):
        end = time.monotonic() + 900
        while time.monotonic() < end:
            runs = self.json('ci', ['gh', 'run', 'list', '--repo', REPOSITORY, '--commit', head,
                                  '--json', 'databaseId,workflowName,status,conclusion,headSha', '--limit', '20'])
            matches = [r for r in runs if r['workflowName'] == WORKFLOW and r['headSha'] == head]
            if matches:
                run = max(matches, key=lambda r: r['databaseId'])
                if run['status'] == 'completed':
                    require(run['conclusion'] == 'success', 'CI failed; inspect run ' + str(run['databaseId']))
                    self.progress('ci', run)
                    return
            time.sleep(10)
        raise ReleaseError('CI timed out; resume this run to continue')

    def published_fixture(self, head):
        prior = self.state['steps'].get('published_fixture')
        if prior and Path(prior['result']).is_file() and sha(prior['result']) == prior['sha256']:
            return prior
        directory = self.directory / ('published-fixture-' + str(int(time.time())))
        result = self.json('published-fixture', [sys.executable, 'scripts/verify_plugin.py',
            '--source', f'https://github.com/{REPOSITORY}.git', '--ref', head, '--output-dir', str(directory)], timeout=600)
        require(result['pass'] and result['marketplace_revision'] == head, 'Published installation failed')
        path = directory / 'result.json'
        item = {'result': str(path), 'sha256': sha(path)}
        self.progress('published_fixture', item)
        return item

    def release(self, wheel, fixture):
        notes = Path(self.args.notes).resolve()
        require(notes.is_file() and notes.is_relative_to(self.root), 'Release notes must be a local repository file')
        existing = self.run('release-view', ['gh', 'release', 'view', self.tag, '--repo', REPOSITORY,
                                            '--json', 'url,isDraft'], check=False)
        if existing.returncode:
            self.run('release-draft', ['gh', 'release', 'create', self.tag, '--repo', REPOSITORY,
                '--verify-tag', '--draft', '--prerelease', '--title', 'JCM ' + self.version, '--notes-file', str(notes)])
        assets = [Path(wheel['wheel']), Path(fixture['result'])]
        public_fixture = self.directory / f'jcm-{self.version}-plugin-validation.json'
        shutil.copy2(assets[1], public_fixture)
        assets[1] = public_fixture
        # GitHub's REST lookup by tag returns 404 for a draft; gh resolves drafts
        # through release listing and their release ID.
        available = self.json('release-assets', ['gh', 'release', 'view', self.tag,
                                                 '--repo', REPOSITORY, '--json', 'assets'])
        names = {a['name'] for a in available['assets']}
        for asset in assets:
            if asset.name in names:
                download = self.directory / ('download-' + str(time.time_ns()))
                self.run('asset-download', ['gh', 'release', 'download', self.tag, '--repo', REPOSITORY,
                                           '--pattern', asset.name, '--dir', str(download)])
                require(sha(download / asset.name) == sha(asset), 'Existing release asset differs; refusing overwrite')
            else:
                self.run('asset-upload', ['gh', 'release', 'upload', self.tag, str(asset), '--repo', REPOSITORY])
        self.run('release-publish', ['gh', 'release', 'edit', self.tag, '--repo', REPOSITORY, '--draft=false'])
        result = self.json('release-final', ['gh', 'release', 'view', self.tag, '--repo', REPOSITORY, '--json', 'url,isDraft'])
        require(not result['isDraft'], 'Release remains a draft')
        self.progress('release', result)
        return result

    def loader(self, installed):
        stderr = (self.directory / 'loader.stderr.log').open('w')
        process = subprocess.Popen(['codex', 'app-server', '--stdio'], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=stderr, text=True, bufsize=1, cwd=self.root, env=self.env)
        select = selectors.DefaultSelector(); select.register(process.stdout, selectors.EVENT_READ)
        def send(value):
            process.stdin.write(json.dumps(value) + '\n'); process.stdin.flush()
        def reply(identifier):
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if not select.select(max(0, deadline - time.monotonic())):
                    break
                line = process.stdout.readline()
                if not line:
                    break
                value = json.loads(line)
                if value.get('id') == identifier:
                    require('error' not in value, 'Loader probe failed')
                    return value['result']
            raise ReleaseError('Loader probe timed out')
        try:
            send({'id': 1, 'method': 'initialize', 'params': {'clientInfo': {'name': 'jcm_release_check', 'version': self.version}}})
            reply(1); send({'method': 'initialized', 'params': {}})
            send({'id': 2, 'method': 'skills/list', 'params': {'cwds': [str(self.root)], 'forceReload': True}})
            result = reply(2)
            expected = str(installed / 'skills/astra-continuity/SKILL.md')
            matches = [s for entry in result['data'] for s in entry['skills'] if s['path'] == expected and s['enabled']]
            require(matches, 'Fresh loader did not expose the installed skill')
            write_json(self.directory / 'loader.json', {'skills': matches, 'fresh_process': True})
            return True
        finally:
            select.close(); process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
            stderr.close()

    def install(self, head):
        inventory = self.json('plugins-before', ['codex', 'plugin', 'list', '--json'])
        marketplaces = self.json('marketplaces', ['codex', 'plugin', 'marketplace', 'list', '--json'])
        market = next((m for m in marketplaces['marketplaces'] if m['name'] == 'jcm'), None)
        require(market and market.get('marketplaceSource', {}).get('sourceType') == 'git', 'Expected installed Git-backed jcm marketplace')
        require(market['marketplaceSource']['source'].removesuffix('.git') in
                {f'https://github.com/{REPOSITORY}', f'git@github.com:{REPOSITORY}'}, 'Unexpected marketplace source')
        config = self.home / 'config.toml'
        original = config.read_bytes()
        backup = self.home / 'backups' / ('jcm-' + self.tag + '-' + str(time.time_ns()))
        backup.mkdir(parents=True)
        self.state.update(backup=str(backup), backup_complete=False); self.save()
        shutil.copy2(config, backup / 'config.toml')
        write_json(backup / 'plugins.json', inventory)
        cache = self.home / 'plugins/cache/jcm/jev-context-manager'
        if cache.exists():
            shutil.copytree(cache, backup / 'plugin-cache', symlinks=True)
        store = Path(os.environ.get('JCM_HOME', '~/Library/Application Support/JCM')).expanduser().resolve()
        backup_store(store, backup / 'JCM')
        self.state['backup_complete'] = True; self.save()
        self.run('marketplace-upgrade', ['codex', 'plugin', 'marketplace', 'upgrade', 'jcm', '--json'])
        revision = self.run('marketplace-revision', ['git', '-C', market['root'], 'rev-parse', 'HEAD']).stdout.strip()
        require(revision == head, 'Marketplace revision differs from validated release')
        item = self.json('plugin-install', ['codex', 'plugin', 'add', SELECTOR, '--json'])
        installed = Path(item['installedPath'])
        require(item['version'] == self.version, 'Installed version mismatch')
        after = self.json('plugins-after', ['codex', 'plugin', 'list', '--json'])
        manifest = json.loads((installed / 'runtime-manifest.json').read_text())
        source = self.root / 'plugins/jev-context-manager'
        checks = {'config_bytes_unchanged': config.read_bytes() == original,
                  'other_plugins_unchanged': other_plugins(inventory) == other_plugins(after),
                  'installed_enabled': any(p['pluginId'] == SELECTOR and p['enabled'] for p in after['installed']),
                  'installed_hashes': all(sha(installed / p) == value for p, value in manifest.items()),
                  'installed_matches_source': all((installed / p).read_bytes() == (source / p).read_bytes() for p in manifest),
                  'installed_manifest_matches': (installed / '.codex-plugin/plugin.json').read_bytes() == (source / '.codex-plugin/plugin.json').read_bytes()}
        self.run('installed-help', [str(installed / 'scripts/jcm'), '--help'])
        checks['fresh_loader'] = self.loader(installed)
        require(all(checks.values()), 'Installation verification failed; see backup and logs')
        self.progress('install', {'path': str(installed), 'checks': checks, 'backup': str(backup)})

    def execute(self):
        self.preflight()
        wheel = self.validate()
        if self.args.publish:
            head = self.publish_commit()
            self.ci(head)
            fixture = self.published_fixture(head)
            self.release(wheel, fixture)
            if self.args.install:
                self.install(head)
        self.state['pass'] = True
        self.state.pop('error', None)
        self.save()
        print(json.dumps({'pass': True, 'version': self.version, 'result': str(self.state_path),
                          'release': self.state['steps'].get('release', {}).get('url'),
                          'installed': self.state['steps'].get('install', {}).get('path')}, ensure_ascii=False), flush=True)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--publish', action='store_true', help='Commit, push exact tag, require CI and publish release')
    parser.add_argument('--install', action='store_true', help='Back up and update the current user plugin after publishing')
    parser.add_argument('--notes', help='Repository release-notes file')
    parser.add_argument('--commit-message')
    parser.add_argument('--include', action='append', default=[], help='Explicitly include an untracked evidence file')
    parser.add_argument('--resume', help='Resume the identical inputs using the previous run directory')
    args = parser.parse_args()
    require(not args.install or args.publish, '--install requires --publish')
    require(not args.publish or args.notes, '--publish requires --notes')
    lock_path = ROOT / '.task-notes/release.lock'; lock_path.parent.mkdir(exist_ok=True)
    with lock_path.open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ReleaseError('Another release is running') from None
        release = Release(args)
        try:
            release.execute()
            return 0
        except (ReleaseError, OSError, ValueError) as error:
            release.state.update({'pass': False, 'error': str(error)})
            release.save()
            print(json.dumps({'pass': False, 'error': str(error), 'result': str(release.state_path)}, ensure_ascii=False), flush=True)
            return 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except ReleaseError as error:
        print(json.dumps({'pass': False, 'error': str(error)})); raise SystemExit(1)
