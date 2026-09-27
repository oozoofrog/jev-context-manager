import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location('jcm_release', Path(__file__).resolve().parents[1] / 'scripts/release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def test_versions_must_agree_before_any_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
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
        runner.inputs = Mock(return_value=('same', []))
        runner.run = Mock(return_value=subprocess.CompletedProcess([], 0, '', ''))
        runner.git = Mock(side_effect=['a' * 40, 'b' * 40])
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
            root = Path(directory); source = root / 'store'; source.mkdir()
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
            root = Path(directory); source = root / 'store'; source.mkdir()
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
