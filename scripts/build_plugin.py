#!/usr/bin/env python3
"""Build/check the self-contained marketplace payload from canonical runtime files."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins/jev-context-manager'


def build(check=False):
    files = {Path('runtime/jcm') / p.name: p.read_bytes() for p in (ROOT / 'src/jcm').glob('*.py')}
    files[Path('skills/astra-continuity/SKILL.md')] = (ROOT / 'skills/astra-continuity/SKILL.md').read_bytes()
    files[Path('runtime-manifest.json')] = (json.dumps({str(p): hashlib.sha256(b).hexdigest()
        for p, b in sorted(files.items())}, indent=2) + '\n').encode()
    for relative, data in files.items():
        target = PLUGIN / relative
        if check:
            if not target.is_file() or target.read_bytes() != data:
                raise SystemExit('Stale plugin payload: ' + str(relative))
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    extras = set((PLUGIN / 'runtime/jcm').glob('*.py')) - {PLUGIN / p for p in files}
    if extras:
        raise SystemExit('Unexpected runtime files: ' + str(extras))
    print('Plugin payload ' + ('verified' if check else 'built'))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    build(parser.parse_args().check)
