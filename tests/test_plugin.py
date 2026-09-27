import json
import os
import shlex
import shutil
import subprocess
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import test_continuity as fixtures
from jcm import config
from jcm.bootstrap import existing
from jcm.follower import follower_status
from jcm.plugin import PLUGIN_ID, bind, require_active
from jcm.util import JCMError

REPO = Path(__file__).resolve().parents[1]


class PluginTests(unittest.TestCase):
    setUp = fixtures.ContinuityTests.setUp
    tearDown = fixtures.ContinuityTests.tearDown
    capture = fixtures.ContinuityTests.capture
    transcript = fixtures.ContinuityTests.transcript
    user_line = fixtures.ContinuityTests.user_line

    def setup_plugin(self):
        self.codex = self.base / 'codex'
        self.plugin = self.codex / 'plugins/cache/jcm/jev-context-manager/0.1.0-dev.4'
        shutil.copytree(REPO / 'plugins/jev-context-manager', self.plugin)
        self.settings = self.codex / 'config.toml'
        self.settings.write_text('[plugins."' + PLUGIN_ID + '"]\nenabled = true\n')
        self.binding = {'id': PLUGIN_ID, 'codex_home': str(self.codex), 'root': str(self.plugin)}
        self.env = {**os.environ, 'CODEX_HOME': str(self.codex), 'JCM_HOME': str(self.home)}

    def bind(self):
        self.cfg = bind(self.cfg, self.binding, migrate=True)
        self.store.config = self.cfg

    def test_bundle_is_self_contained_and_matches_canonical_source(self):
        subprocess.run(['python3', str(REPO / 'scripts/build_plugin.py'), '--check'], check=True, capture_output=True)
        self.setup_plugin()
        (self.root / 'jcm.py').write_text('raise RuntimeError("workspace import")')
        result = subprocess.run([str(self.plugin / 'scripts/jcm'), '--repo', str(self.root),
                                 'status'], env=self.env, cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['root'], str(self.root))

    def test_migration_preserves_records_policy_and_unrelated_hooks(self):
        self.setup_plugin()
        event = self.capture('old', 't', 'preserve me')
        config.install_hooks(self.cfg)
        path = self.root / '.codex/hooks.json'
        data = json.loads(path.read_text())
        data['hooks']['Stop'][0]['hooks'].append({'type': 'command', 'command': 'echo unrelated'})
        path.write_text(json.dumps(data))
        before = path.read_bytes()
        self.bind()
        self.assertEqual(self.store.events()[0]['id'], event)
        self.assertTrue(self.cfg['allow_egress'])
        self.assertTrue(self.cfg['enabled'])
        self.assertIn('echo unrelated', path.read_text())
        self.assertNotIn('hook --stdin', path.read_text())
        backups = list((self.home / 'backups').glob('*pre-plugin-hooks.json'))
        self.assertEqual(backups[0].read_bytes(), before)
        self.assertEqual(config.install_hooks(self.cfg)['source'], 'plugin')

    def test_disabled_removed_malformed_and_project_disabled_fail_closed(self):
        self.setup_plugin()
        self.bind()
        good = self.settings.read_text()
        for bad in [good.replace('true', 'false'), '', 'not valid toml']:
            self.settings.write_text(bad)
            with self.assertRaisesRegex(JCMError, 'PLUGIN_INACTIVE'):
                self.capture('old', 'x', 'must not be saved')
        self.settings.write_text(good)
        (self.root / '.codex/config.toml').write_text('[features]\nhooks = false\n')
        with self.assertRaisesRegex(JCMError, 'PLUGIN_INACTIVE'):
            self.capture('old', 'x', 'must not be saved')
        (self.root / '.codex/config.toml').unlink()
        shutil.rmtree(self.plugin)
        with self.assertRaisesRegex(JCMError, 'PLUGIN_INACTIVE'):
            self.capture('old', 'x', 'must not be saved')
        self.assertEqual(self.store.events(), [])
        # Cleanup/admin commands remain available without active plugin state.
        self.store.change_policy(enabled=False)

    def test_unregistered_plugin_hook_is_noop_and_creates_no_storage(self):
        self.setup_plugin()
        env = {**self.env, 'JCM_HOME': str(self.base / 'absent')}
        result = subprocess.run([str(self.plugin / 'scripts/jcm'), 'plugin-hook'], env=env,
                                input=json.dumps({'cwd': str(self.root)}), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertFalse((self.base / 'absent').exists())

    def test_unbound_standalone_profile_is_not_silently_adopted(self):
        self.setup_plugin()
        result = subprocess.run([str(self.plugin / 'scripts/jcm'), 'plugin-hook'], env=self.env,
            input=json.dumps({'cwd': str(self.root), 'hook_event_name':'SessionStart','session_id':'x'}),
            text=True, capture_output=True)
        self.assertEqual(result.stdout, '')
        self.assertEqual(self.store.events(), [])
        self.assertNotIn('plugin', config.load(self.home, self.root))

    def test_native_hook_uses_external_profile_reference_without_home_environment(self):
        self.setup_plugin()
        self.bind()
        env = {k:v for k,v in self.env.items() if k != 'JCM_HOME'}
        result = subprocess.run([str(self.plugin / 'scripts/jcm'), 'plugin-hook'], env=env,
            input=json.dumps({'cwd': str(self.root), 'hook_event_name':'SessionStart','session_id':'x'}),
            text=True, capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn('unavailable', result.stdout)
        self.assertEqual(len(self.store.events()), 1)

    def test_disabled_plugin_can_still_report_status_and_disable_project(self):
        self.setup_plugin()
        self.bind()
        self.settings.write_text(self.settings.read_text().replace('true', 'false'))
        command = [str(self.plugin / 'scripts/jcm'), '--repo', str(self.root)]
        result = subprocess.run(command + ['status'], env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.assertIs(json.loads(result.stdout)['plugin']['active'], False)
        result = subprocess.run(command + ['disable'], env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0)
        self.assertIs(config.load(self.home,self.root)['enabled'], False)

    def test_migration_stops_old_follower_and_plugin_disable_stops_new_one(self):
        self.setup_plugin()
        path = self.transcript()
        with path.open('ab') as f: f.write(self.user_line('old'))
        old = existing(self.store, 'prior', path, install=False)
        self.assertTrue(old['follower']['running'])
        self.bind()
        self.assertFalse(follower_status(self.store, old['source']['key'])['running'])
        with patch.dict(os.environ, self.env):
            fresh = existing(self.store, 'prior', path)
        self.assertTrue(fresh['follower']['running'])
        self.settings.write_text(self.settings.read_text().replace('true', 'false'))
        for _ in range(40):
            state = follower_status(self.store, fresh['source']['key'])
            if not state['running']: break
            time.sleep(.05)
        self.assertFalse(state['running'])
        self.assertEqual(state['error'], 'PLUGIN_INACTIVE_OR_UNVERIFIED')
        count = len(self.store.events())
        with path.open('ab') as f: f.write(self.user_line('after disable', 'later'))
        time.sleep(.6)
        self.assertEqual(len(self.store.events()), count)

    def test_upgrade_rebinds_without_changing_policy_or_event_identity(self):
        self.setup_plugin()
        self.capture('old', 't', 'keep')
        self.bind()
        version2 = self.plugin.with_name('0.1.0-dev.5')
        shutil.copytree(self.plugin, version2)
        shutil.rmtree(self.plugin)
        updated = bind(self.cfg, {**self.binding, 'root': str(version2)})
        self.assertEqual(updated['cli_argv'][0], str(version2 / 'scripts/jcm'))
        self.assertEqual(updated['allow_egress'], self.cfg['allow_egress'])
        self.assertEqual(len(self.store.events()), 1)
        require_active(updated['plugin'])

    def test_wrong_distribution_binding_rejected(self):
        self.setup_plugin()
        with self.assertRaisesRegex(JCMError, 'PLUGIN_INACTIVE'):
            bind(self.cfg, {**self.binding, 'id':'other@jcm'}, migrate=True)
        self.assertNotIn('plugin', config.load(self.home, self.root))


if __name__ == '__main__':
    unittest.main()
