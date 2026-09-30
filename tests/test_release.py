import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('jcm_release', Path(__file__).resolve().parents[1] / 'scripts/release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def test_versions_must_agree_before_any_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'src/jcm').mkdir(parents=True)
            (root / 'plugins/jev-context-manager/.codex-plugin').mkdir(parents=True)
            (root / 'pyproject.toml').write_text('[project]\nversion="0.1.0.dev8"\n')
            (root / 'src/jcm/__init__.py').write_text('__version__ = "0.1.0.dev8"\n')
            manifest = root / 'plugins/jev-context-manager/.codex-plugin/plugin.json'
            manifest.write_text(json.dumps({'version': '0.1.0-dev.7'}))
            with self.assertRaisesRegex(release.ReleaseError, 'disagree'):
                release.versions(root)
            manifest.write_text(json.dumps({'version': '0.1.0-dev.8'}))
            self.assertEqual(release.versions(root)[2], 'v0.1.0-dev.8')

    def test_failed_validation_never_publishes(self):
        runner = object.__new__(release.Release)
        runner.preflight = Mock()
        runner.validate = Mock(side_effect=release.ReleaseError('tests failed'))
        runner.publish_commit = Mock()
        with self.assertRaisesRegex(release.ReleaseError, 'tests failed'):
            runner.execute()
        runner.publish_commit.assert_not_called()

    def test_existing_tag_is_never_moved(self):
        runner = object.__new__(release.Release)
        runner.fingerprint, runner.names, runner.state, runner.tag = 'same', [], {}, 'v0.1.0-dev.8'
        runner.publish_names = None
        runner.inputs = Mock(return_value=('same', []))
        runner.run = Mock(return_value=subprocess.CompletedProcess([], 0, '', ''))
        runner.git = Mock(side_effect=['', 'a' * 40, 'b' * 40])
        runner.save = Mock()
        with self.assertRaisesRegex(release.ReleaseError, 'different commit'):
            runner.publish_commit()
        self.assertFalse(any(call.args[0] in ('push', 'tag') for call in runner.run.call_args_list))

    def test_wrong_marketplace_cannot_update_user_installation(self):
        runner = object.__new__(release.Release)
        runner.json = Mock(side_effect=[{'installed': []}, {'marketplaces': [{
            'name': 'jcm', 'marketplaceSource': {'sourceType': 'git', 'source': 'https://github.com/other/repo'}}]}])
        runner.run = Mock()
        with self.assertRaisesRegex(release.ReleaseError, 'Unexpected marketplace'):
            runner.install('a' * 40)
        runner.run.assert_not_called()

    def test_sqlite_backup_preserves_committed_wal_data_and_blobs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); source = root / 'store'; source.mkdir()
            (source / 'blobs').mkdir(); (source / 'blobs/data').write_text('durable source')
            connection = sqlite3.connect(source / 'journal.sqlite')
            try:
                connection.execute('PRAGMA journal_mode=WAL')
                connection.execute('CREATE TABLE records(value TEXT)')
                connection.execute("INSERT INTO records VALUES ('new committed value')")
                connection.commit()
                release.backup_store(source, root / 'backup')
                with sqlite3.connect(root / 'backup/journal.sqlite') as copied:
                    self.assertEqual(copied.execute('SELECT value FROM records').fetchone()[0], 'new committed value')
                self.assertEqual((root / 'backup/blobs/data').read_text(), 'durable source')
                self.assertFalse((root / 'backup/journal.sqlite-wal').exists())
            finally:
                connection.close()

    def test_backup_does_not_follow_external_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); source = root / 'store'; source.mkdir()
            (root / 'private').write_text('do not copy')
            (source / 'external').symlink_to(root / 'private')
            with self.assertRaisesRegex(release.ReleaseError, 'symlinks'):
                release.backup_store(source, root / 'backup')
            self.assertFalse((root / 'backup/external').exists())

    def test_raw_evidence_and_task_notes_are_not_auto_published(self):
        self.assertTrue(release.public_source('scripts/release.py'))
        self.assertFalse(release.public_source('.task-notes/private.json'))
        self.assertFalse(release.public_source('evidence/live-plugin-test/adopt.stdout.log'))

    def test_other_plugin_inventory_is_compared_independently_of_order(self):
        first = {'installed': [{'pluginId':'other', 'version':'1'}, {'pluginId':release.SELECTOR,'version':'old'}]}
        second = {'installed': [{'pluginId':release.SELECTOR,'version':'new'}, {'pluginId':'other','version':'1'}]}
        self.assertEqual(release.other_plugins(first), release.other_plugins(second))

    def test_explicit_scope_excludes_untracked_private_docs_and_their_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();(root/'docs').mkdir();(root/'README.md').write_text('Public')
            (root/'docs/public.md').write_text('Reviewed');private=root/'docs/jev-billing-design.md';private.write_text('Private draft')
            scope=root/'scope.json';scope.write_text(json.dumps(['README.md','docs/public.md']))
            runner=object.__new__(release.Release);runner.root=root
            runner.args=SimpleNamespace(include=[],scope_file=str(scope))
            runner.run=Mock(side_effect=lambda name,argv:subprocess.CompletedProcess(argv,0,
                'README.md\0' if name=='tracked-files' else 'docs/public.md\0docs/jev-billing-design.md\0',''))
            fingerprint,names=runner.inputs();private.write_text('Unrelated ongoing draft')
            self.assertEqual(runner.inputs()[0],fingerprint)
            self.assertNotIn('docs/jev-billing-design.md',names)
            self.assertEqual(runner.publish_names,{'README.md','docs/public.md'})

    def test_publication_stages_only_scope_and_preserves_unrelated_work(self):
        runner=object.__new__(release.Release)
        runner.fingerprint,runner.names,runner.state,runner.tag,runner.version='same',[],{},'v1.0.2','1.0.2'
        runner.publish_names={'README.md','docs/public.md'};runner.args=SimpleNamespace(commit_message='Release 1.0.2')
        runner.inputs=Mock(return_value=('same',[]));runner.save=runner.progress=Mock();runner.scan_staged=Mock()
        def run(name,argv):
            value='README.md\0docs/unrelated.md\0' if name=='changed-files' else 'docs/public.md\0docs/jev-billing-design.md\0' if name=='new-files' else ''
            return subprocess.CompletedProcess(argv,0,value,'')
        runner.run=Mock(side_effect=run);head='a'*40
        runner.git=Mock(side_effect=['',head,head,head+'\trefs/heads/main\n'+head+'\trefs/tags/v1.0.2^{}'])
        runner.publish_commit()
        stage=next(c.args[1] for c in runner.run.call_args_list if c.args[0]=='stage')
        self.assertEqual(stage,['git','add','--','README.md','docs/public.md'])
        runner.scan_staged.assert_called_once_with(['README.md','docs/public.md'])

    def test_staged_scan_rejects_literal_credentials_without_echoing_them(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();subprocess.run(['git','init','-q',str(root)],check=True)
            path=root/'README.md';secret='ghp_'+('x'*36);path.write_text(secret)
            subprocess.run(['git','-C',str(root),'add','README.md'],check=True)
            runner=object.__new__(release.Release);runner.root=root;runner.directory=root/'logs';runner.directory.mkdir()
            runner.git=Mock(return_value='README.md\0')
            with self.assertRaisesRegex(release.ReleaseError,'Sensitive'):runner.scan_staged(['README.md'])
            report=(runner.directory/'staged-scan.json').read_text()
            self.assertNotIn(secret,report);self.assertIn('github_token',report)

    def test_new_and_existing_marketplaces_preserve_other_settings_and_plugins(self):
        for existing in (False,True):
            with self.subTest(existing=existing):self.exercise_install(existing)

    def test_unrelated_settings_drift_stops_install_without_restoring_old_config(self):
        self.exercise_install(False,True)

    def exercise_install(self,existing,drift=False):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();home=root/'home';home.mkdir();cfg=home/'config.toml'
            original='model="gpt-6.1-sol"\n# keep this comment\n[custom]\nvalue="untouched"\n';cfg.write_text(original)
            source=root/'plugins/jev-context-manager';(source/'.codex-plugin').mkdir(parents=True);(source/'runtime/jcm').mkdir(parents=True)
            (source/'.codex-plugin/plugin.json').write_text(json.dumps({'version':'1.0.2'}))
            payload=source/'runtime/jcm/__init__.py';payload.write_text('__version__="1.0.2"\n')
            (source/'runtime-manifest.json').write_text(json.dumps({'runtime/jcm/__init__.py':release.sha(payload)}))
            installed=root/'installed';shutil.copytree(source,installed);head='a'*40
            market={'name':'jcm','root':str(root/'market'),'marketplaceSource':{'sourceType':'git','source':'https://github.com/'+release.REPOSITORY+'.git'}}
            runner=object.__new__(release.Release);runner.root=root;runner.home=home;runner.version='1.0.2';runner.tag='v1.0.2'
            runner.directory=root/'logs';runner.directory.mkdir();runner.state={};runner.save=runner.progress=Mock();runner.loader=Mock(return_value=True)
            runner.env={'JCM_HOME':str(root/'absent-store'),'CODEX_HOME':str(home)}
            inventory={'installed':[{'pluginId':'other','version':'same'}]}
            def response(name,argv):
                if name=='plugins-before':return inventory
                if name=='marketplaces':return {'marketplaces':[market] if existing else []}
                if name=='marketplaces-registered':return {'marketplaces':[market]}
                if name=='marketplace-add':
                    self.assertEqual(argv[argv.index('--ref')+1],'main')
                    with cfg.open('a') as stream:stream.write('\n[marketplaces.jcm]\nsource_type="git"\nsource="https://github.com/oozoofrog/jev-context-manager.git"\nref="main"\n')
                    return {'installedRoot':market['root']}
                if name=='plugin-install':
                    with cfg.open('a') as stream:stream.write('\n[plugins."'+release.SELECTOR+'"]\nenabled=true\n')
                    if drift:cfg.write_text(cfg.read_text().replace('gpt-6.1-sol','changed-by-another-writer'))
                    return {'version':'1.0.2','installedPath':str(installed)}
                if name=='plugins-after':return {'installed':inventory['installed']+[{'pluginId':release.SELECTOR,'enabled':True}]}
                raise AssertionError(name)
            runner.json=Mock(side_effect=response);runner.run=Mock(side_effect=lambda name,argv:subprocess.CompletedProcess(argv,0,head if name=='marketplace-revision' else '', ''))
            with patch.dict(os.environ,{'JCM_HOME':str(root/'absent-store')}):
                if drift:
                    with self.assertRaisesRegex(release.ReleaseError,'Unrelated settings'):runner.install(head)
                    runner.loader.assert_not_called();self.assertIn('changed-by-another-writer',cfg.read_text())
                else:
                    runner.install(head);runner.loader.assert_called_once_with(installed)
                    self.assertEqual(release.other_settings(cfg.read_bytes()),release.other_settings(original.encode()))
                    self.assertTrue(cfg.read_text().startswith(original))
                    self.assertTrue(all(v['other_settings_unchanged'] for v in runner.state['config_write_observations']))
                self.assertTrue(runner.state['backup_complete'])

    def test_draft_release_assets_use_draft_aware_lookup(self):
        calls = self.exercise_release('0.1.0-dev.9')
        self.assertIn('--prerelease=true', next(c.args[1] for c in calls if c.args[0] == 'release-publish'))

    def test_stable_release_is_published_as_latest_without_prerelease_flag(self):
        calls = self.exercise_release('1.0.0')
        publish = next(c.args[1] for c in calls if c.args[0] == 'release-publish')
        self.assertIn('--prerelease=false', publish)
        self.assertIn('--latest=true', publish)
        draft = next(c.args[1] for c in calls if c.args[0] == 'release-draft')
        self.assertIn('--prerelease=false', draft)

    def exercise_release(self, version):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            notes = root / 'notes.md'; notes.write_text('Release notes')
            wheel = root / 'package.whl'; wheel.write_bytes(b'wheel')
            fixture = root / 'fixture.json'; fixture.write_text(json.dumps({'pass':True,'live':False,
                'source':'https://github.com/'+release.REPOSITORY+'.git','ref':'a'*40,'marketplace_revision':'a'*40,
                'host_version':'fixture','runtime_manifest_sha256':'b'*64,'checks':{'offline':True},'fixture':'private temporary path'}))
            runner = object.__new__(release.Release)
            runner.root = runner.directory = root
            runner.version, runner.tag = version, 'v' + version
            runner.args = Mock(notes=str(notes),asset=[])
            runner.progress = Mock()
            runner.run = Mock(side_effect=lambda name, argv, **kw: subprocess.CompletedProcess([], 1 if name == 'release-view' else 0, '{}', ''))
            def response(name, argv):
                if argv[:2] == ['gh', 'api']:
                    raise release.ReleaseError('draft release tag endpoint returns 404')
                if argv[-1] == 'assets':
                    return {'assets': []}
                return {'url': 'https://github.com/owner/repo/releases/tag/v1', 'isDraft': False, 'isPrerelease': '-dev.' in version}
            runner.json = Mock(side_effect=response)
            result = runner.release({'wheel': str(wheel)}, {'result': str(fixture)})
            self.assertFalse(result['isDraft'])
            self.assertEqual(sum(call.args[0] == 'asset-upload' for call in runner.run.call_args_list), 2)
            return runner.run.call_args_list
