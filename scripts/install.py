#!/usr/bin/env python3
"""Standalone user-scoped installer. No JCM/package dependencies before installation."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
import uuid

REPOSITORY = 'oozoofrog/jev-context-manager'
OWNER = 'jcm-remote-installer-v1'


class InstallError(Exception):
    pass


def absolute(path):
    # Preserve the final path component for symlink/ownership checks.
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def exists(path):
    return os.path.lexists(path)


def write(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    try:
        temp.write_bytes(data)
        temp.chmod(mode)
        os.replace(temp, path)
    finally:
        if exists(temp):
            temp.unlink()


def symlink(path, target):
    temp = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    try:
        temp.symlink_to(target)
        os.replace(temp, path)
    finally:
        if exists(temp):
            temp.unlink()


def remove_owned(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def copy_item(source, target):
    if source.is_symlink():
        target.symlink_to(os.readlink(source))
    elif source.is_dir():
        shutil.copytree(source, target, symlinks=True)
    else:
        shutil.copy2(source, target)


class Transaction:
    """Back up only the exact paths being replaced; roll back ordinary failures."""
    def __init__(self, directory):
        self.directory = directory
        self.entries = []

    def save(self, path):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        backup = self.directory / str(len(self.entries))
        present = exists(path)
        if present:
            copy_item(path, backup)
        self.entries.append((path, backup, present))
        write(self.directory / 'index.json', json.dumps([
            {'path': str(p), 'backup': str(b), 'existed': e} for p, b, e in self.entries
        ], indent=2).encode())

    def rollback(self):
        for path, backup, present in reversed(self.entries):
            if exists(path):
                remove_owned(path)
            if present:
                copy_item(backup, path)


def fetch(url, limit):
    request = urllib.request.Request(url, headers={'User-Agent': 'JCM-installer', 'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=60) as response:
        if not response.url.startswith('https://'):
            raise InstallError('HTTPS redirect required')
        content = response.read(limit + 1)
    if len(content) > limit:
        raise InstallError('Download exceeds the installer size limit')
    return content


def source_files(root):
    paths = [root / 'pyproject.toml']
    for directory in ('src/jcm', 'skills/astra-continuity'):
        paths.extend(p for p in (root / directory).rglob('*')
                     if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc')
    for path in paths:
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            raise InstallError('Invalid source file: ' + str(path))
    if not (root / 'src/jcm/cli.py').is_file() or not (root / 'skills/astra-continuity/SKILL.md').is_file():
        raise InstallError('The selected ref does not contain the JCM runtime and skill')
    return sorted(paths)


def prepare_source(destination, ref, local=None):
    destination.mkdir()
    if local:
        local = absolute(local)
        for path in source_files(local):
            target = destination / path.relative_to(local)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        revision = 'local'
    else:
        quoted = urllib.parse.quote(ref, safe='')
        info = json.loads(fetch(f'https://api.github.com/repos/{REPOSITORY}/commits/{quoted}', 2_000_000))
        revision = info.get('sha', '')
        if not re.fullmatch(r'[a-f0-9]{40}', revision):
            raise InstallError('GitHub did not return an immutable commit SHA')
        archive = fetch(f'https://codeload.github.com/{REPOSITORY}/tar.gz/{revision}', 40_000_000)
        import io
        total = 0
        with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as bundle:
            for member in bundle:
                parts = PurePosixPath(member.name).parts
                if len(parts) < 2:
                    continue
                relative = PurePosixPath(*parts[1:])
                if member.name.startswith('/') or '..' in parts:
                    raise InstallError('Unsafe archive path')
                wanted = (str(relative) == 'pyproject.toml' or
                          str(relative).startswith('src/jcm/') or
                          str(relative).startswith('skills/astra-continuity/'))
                if not wanted or member.isdir():
                    continue
                if not member.isfile():
                    raise InstallError('Archive links and special files are not accepted')
                total += member.size
                if total > 10_000_000:
                    raise InstallError('Source extraction exceeds the installer size limit')
                target = destination / str(relative)
                if exists(target):
                    raise InstallError('Duplicate archive member')
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.extractfile(member) as stream:
                    target.write_bytes(stream.read())
    paths = source_files(destination)
    hashes = {str(p.relative_to(destination)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    return {'repository': REPOSITORY, 'requested_ref': ref, 'revision': revision,
            'source_sha256': hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()}


def build_runtime(source, release):
    environment = release / 'venv'
    logfile = release / 'install.log'
    with logfile.open('w') as log:
        for command in ([sys.executable, '-m', 'venv', str(environment)],
                        [str(environment / 'bin/python'), '-m', 'pip', '--isolated', 'install',
                         '--disable-pip-version-check', '--no-deps', str(source)],
                        [str(environment / 'bin/python'), '-m', 'pip', '--isolated', 'check'],
                        [str(environment / 'bin/python'), '-I', '-m', 'jcm', '--help']):
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
            if result.returncode:
                raise InstallError('Runtime build failed; existing install preserved. Log: ' + str(logfile))
    return subprocess.check_output([str(environment / 'bin/python'), '-I', '-c',
                                   'import jcm; print(jcm.__version__)'], text=True).strip()


def validate_install(prefix, launcher, skill):
    result = subprocess.run([str(launcher), '--help'], stdin=subprocess.DEVNULL, capture_output=True)
    if result.returncode:
        raise InstallError('Installed launcher validation failed')
    if skill and not (skill / 'SKILL.md').is_file():
        raise InstallError('Installed skill is missing')


def install(args):
    os.umask(0o077)
    prefix, bin_dir = absolute(args.prefix), absolute(args.bin_dir)
    skill = None if args.no_skill else absolute(args.skill_dir)
    launcher = bin_dir / 'jcm'
    if prefix.is_symlink():
        raise InstallError('Installation prefix must not be a symlink')
    if launcher == prefix / 'bin/jcm' or (skill and (skill.is_relative_to(prefix) or prefix.is_relative_to(skill) or launcher.is_relative_to(skill))):
        raise InstallError('Installation destinations overlap')
    prefix.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (prefix / 'install.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InstallError('Another JCM install is already running for this prefix') from None
        manifest_path = prefix / 'install.json'
        old = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
        managed = old.get('installer') == OWNER
        for path in (prefix / 'current', prefix / 'bin/jcm', manifest_path):
            if exists(path) and not managed:
                raise InstallError('Unmanaged installation prefix; choose another --prefix')
        if launcher.is_dir() and not launcher.is_symlink():
            raise InstallError('Launcher destination is a directory; choose another --bin-dir')
        owned_launcher = launcher.is_symlink() and launcher.resolve() == (prefix / 'bin/jcm').resolve()
        if exists(launcher) and not owned_launcher and not args.replace_existing:
            raise InstallError('Existing jcm launcher; use --replace-existing to back it up or choose --bin-dir')
        if skill and exists(skill):
            marker = skill / '.jcm-install.json'
            owned = marker.is_file() and json.loads(marker.read_text()).get('prefix') == str(prefix)
            if not owned and not args.replace_existing:
                raise InstallError('Existing astra-continuity skill; use --replace-existing to back it up or choose --skill-dir')
        release = prefix / 'releases' / uuid.uuid4().hex
        release.mkdir(parents=True, mode=0o700)
        source = release / 'source'
        info = prepare_source(source, args.ref, args.source)
        version = build_runtime(source, release)
        # Keep the venv at its build path. Moving a venv breaks absolute entry-point shebangs.
        runtime = prefix / 'current/venv/bin/python'
        wrapper = ('#!/bin/sh\n# Managed by JCM remote installer.\nJCM_LAUNCHER=' +
                   shlex.quote(str(prefix / 'bin/jcm')) + '\nexport JCM_LAUNCHER\nexec ' +
                   shlex.quote(str(runtime)) + ' -I -m jcm "$@"\n')
        staged_skill = release / 'skill'
        if skill:
            shutil.copytree(source / 'skills/astra-continuity', staged_skill)
            write(staged_skill / 'JCM_RUNTIME.md', (
                '# Installed JCM runtime\n\nUse this absolute launcher instead of relying on PATH:\n\n```sh\n' +
                shlex.quote(str(prefix / 'bin/jcm')) + ' --help\n```\n\n' +
                'The installer does not activate projects or authorize Jev egress.\n').encode())
            document = staged_skill / 'SKILL.md'
            body = document.read_text().replace('# Astra continuity\n',
                '# Astra continuity\n\nRead [JCM_RUNTIME.md](JCM_RUNTIME.md) for the installed absolute CLI path.\n', 1)
            document.write_text(body)
            write(staged_skill / '.jcm-install.json', json.dumps({'installer': OWNER, 'prefix': str(prefix)}).encode())
        transaction = Transaction(prefix / 'backups' / release.name)
        info.update(installer=OWNER, version=version, prefix=str(prefix), release=str(release),
                    launcher=str(launcher), skill=str(skill) if skill else None,
                    previous_release=old.get('release'), backup=str(transaction.directory))
        try:
            (prefix / 'bin').mkdir(exist_ok=True)
            bin_dir.mkdir(parents=True, exist_ok=True)
            transaction.save(prefix / 'current')
            symlink(prefix / 'current', release)
            transaction.save(prefix / 'bin/jcm')
            write(prefix / 'bin/jcm', wrapper.encode(), 0o700)
            transaction.save(launcher)
            symlink(launcher, prefix / 'bin/jcm')
            if skill:
                skill.parent.mkdir(parents=True, exist_ok=True)
                transaction.save(skill)
                if exists(skill):
                    remove_owned(skill)
                shutil.copytree(staged_skill, skill)
            validate_install(prefix, launcher, skill)
            transaction.save(manifest_path)
            write(manifest_path, json.dumps(info, indent=2).encode())
        except BaseException:
            transaction.rollback()
            raise
        # Old releases and backups are retained; existing profiles use the stable current path.
        return info


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--ref', default=os.environ.get('JCM_REF', 'main'), help='GitHub branch, tag or full commit SHA')
    result.add_argument('--prefix', default='~/.local/share/jcm')
    result.add_argument('--bin-dir', default='~/.local/bin')
    result.add_argument('--skill-dir', default='~/.agents/skills/astra-continuity', help='Full target skill folder')
    result.add_argument('--no-skill', action='store_true')
    result.add_argument('--replace-existing', action='store_true', help='Back up and replace conflicting launcher/skill paths')
    result.add_argument('--source', type=Path, help='Use a local checkout instead of downloading (development only)')
    return result


def main():
    if sys.version_info < (3, 11) or sys.platform not in ('darwin', 'linux'):
        raise SystemExit('JCM installer requires Python 3.11+ on macOS/Linux')
    try:
        info = install(parser().parse_args())
    except (InstallError, OSError, ValueError, subprocess.SubprocessError) as error:
        print('JCM install failed: ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps(info, indent=2))
    print('\nInstalled CLI: ' + shlex.quote(info['launcher']))
    if info['skill']:
        print('Codex skill: ' + info['skill'] + ' (refresh/restart Codex if it does not appear)')
    print('Project activation: ' + shlex.quote(info['launcher']) + ' --repo /absolute/project enable --install-hooks')
    print('No project was activated; Jev egress remains subject to explicit per-project policy.')
    print('If needed, add the launcher directory to PATH; shell startup files were not changed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
