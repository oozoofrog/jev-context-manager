import os
import subprocess
from pathlib import Path

from .util import digest

SKIP = {'.git', '.venv', '__pycache__', 'node_modules', '.codex', 'evidence', 'dist', 'build'}


def git(root, *args):
    # No shell, diff helpers, textconv, user-supplied fsmonitor, or optional locks.
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_OPTIONAL_LOCKS='0', GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
    result = subprocess.run(['git', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null',
                             '-C', str(root), *args], capture_output=True, env=env, timeout=10)
    return result.stdout.decode(errors='replace').strip() if result.returncode == 0 else None


def snapshot(root):
    root = Path(root).resolve(strict=True)
    files, gaps = {}, []
    for base, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP and not d.endswith('.egg-info')
                         and not (Path(base) / d).is_symlink())
        for name in sorted(names):
            path = Path(base) / name
            if path.is_symlink():
                gaps.append('SYMLINK_FILE_EXCLUDED')
                continue
            if len(files) >= 1000:
                gaps.append('SNAPSHOT_FILE_CEILING')
                break
            try:
                if path.stat().st_size > 2_000_000:
                    gaps.append('LARGE_FILE_NOT_HASHED')
                    continue
                files[str(path.relative_to(root))] = digest(path.read_bytes())
            except OSError:
                gaps.append('FILE_CHANGED_OR_UNREADABLE')
        if len(files) >= 1000:
            break
    top = git(root, 'rev-parse', '--show-toplevel')
    data = {'root': str(root), 'git': 'available' if top else 'N/A',
            'git_dir': git(root, 'rev-parse', '--absolute-git-dir') if top else None,
            'head': git(root, 'rev-parse', '--verify', 'HEAD') if top else None,
            'branch': git(root, 'symbolic-ref', '--short', '-q', 'HEAD') if top else None,
            'dirty': git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=all') if top else None,
            'files': files, 'gaps': sorted(set(gaps))}
    data['fingerprint'] = digest(data)
    return data
