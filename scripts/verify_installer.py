"""Real isolated installer smoke: install, upgrade, then execute a previously saved hook."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--remote-ref', help='Fetch the published install.sh at this ref instead of local installer source')
    args = parser.parse_args()
    lane = 'remote' if args.remote_ref else 'local'
    base = Path(tempfile.mkdtemp(prefix='jcm installer ' + lane + ' ')).resolve()
    prefix, skill, bindir, project = base / 'runtime', base / 'skills/astra-continuity', base / 'bin', base / 'project'
    project.mkdir()
    (project / 'sample.py').write_text('x = 1\n')
    unrelated = base / 'skills/unrelated/SKILL.md'
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text('Keep this unrelated skill unchanged.\n')
    observed = [Path.home() / '.codex/config.toml', Path.home() / '.codex/hooks.json']
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None for p in observed}
    if args.remote_ref:
        url = 'https://raw.githubusercontent.com/oozoofrog/jev-context-manager/' + args.remote_ref + '/install.sh'
        with urllib.request.urlopen(url, timeout=30) as response:
            (base / 'install.sh').write_bytes(response.read())
        command = ['bash', str(base / 'install.sh'), '--ref', args.remote_ref]
    else:
        command = [sys.executable, str(ROOT / 'scripts/install.py'), '--source', str(ROOT)]
    command += ['--prefix', str(prefix), '--bin-dir', str(bindir), '--skill-dir', str(skill)]
    jcm = [str(bindir / 'jcm'), '--home', str(base / 'private-data'), '--repo', str(project)]
    result = {'lane': lane, 'base': str(base), 'pass': False}
    enabled = False
    with (ROOT / f'evidence/installer-{lane}-smoke.log').open('w') as log:
        try:
            subprocess.run(command, check=True, stdout=log, stderr=log)
            first = json.loads((prefix / 'install.json').read_text())
            subprocess.run(jcm + ['enable', '--install-hooks'], check=True, stdout=log, stderr=log)
            enabled = True
            profile = next((base / 'private-data/profiles').glob('*.json'))
            original = profile.read_bytes()
            policy = json.loads(original)
            assert policy['cli_argv'][0] == str(prefix / 'bin/jcm'), policy['cli_argv']
            hooks = (project / '.codex/hooks.json').read_bytes()
            subprocess.run(command, check=True, stdout=log, stderr=log)
            second = json.loads((prefix / 'install.json').read_text())
            assert first['release'] != second['release'] and Path(first['release']).is_dir()
            assert profile.read_bytes() == original
            assert (project / '.codex/hooks.json').read_bytes() == hooks
            payload = {'hook_event_name': 'UserPromptSubmit', 'session_id': 'installer-smoke',
                       'turn_id': 'one', 'cwd': str(project), 'prompt': 'Synthetic installation smoke test.'}
            captured = subprocess.run(policy['cli_argv'] + ['hook', '--stdin'], input=json.dumps(payload),
                                      capture_output=True, text=True, check=True)
            assert 'bootstrap new --request-token' in captured.stdout
            status = json.loads(subprocess.check_output(jcm + ['status'], text=True))
            assert status['event_count'] == 1 and status['successful_provider_transport_calls'] == 0
            assert unrelated.read_text() == 'Keep this unrelated skill unchanged.\n'
            after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None for p in observed}
            assert before == after
            result.update(pass_=True, first_release=first['release'], second_release=second['release'],
                          revision=second['revision'], version=second['version'],
                          profile_and_hooks_preserved=True, saved_hook_after_upgrade=True,
                          previous_release_retained=True, unrelated_skill_preserved=True,
                          observed_global_config_unchanged=True, Jev_calls=0,
                          skill_installed=(skill / 'SKILL.md').is_file(), fresh_Codex_skill_discovery='not_tested')
            result['pass'] = result.pop('pass_')
        except Exception as error:
            result['error'] = str(error)
        finally:
            if enabled:
                subprocess.run(jcm + ['disable'], check=True, stdout=log, stderr=log)
                result['fixture_disabled'] = True
    (ROOT / f'evidence/installer-{lane}-smoke.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))
    return 0 if result['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
