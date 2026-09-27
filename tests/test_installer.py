import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('jcm_remote_install', ROOT / 'scripts/install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='jcm install tests ')
        self.base = Path(self.temp.name).resolve()
        self.args = installer.parser().parse_args([
            '--source', str(ROOT), '--prefix', str(self.base / 'runtime'),
            '--bin-dir', str(self.base / 'bin'), '--skill-dir', str(self.base / 'skills/astra-continuity')])
        self.build = patch.object(installer, 'build_runtime', return_value='test-version').start()
        self.validate = patch.object(installer, 'validate_install').start()
        self.addCleanup(patch.stopall)
        self.addCleanup(self.temp.cleanup)

    def test_install_and_reinstall_preserve_unrelated_files_and_backup_previous_skill(self):
        unrelated = self.base / 'skills/other/SKILL.md'
        unrelated.parent.mkdir(parents=True)
        unrelated.write_text('untouched')
        config = self.base / 'config.toml'
        config.write_text('original = true\n')
        first = installer.install(self.args)
        skill = Path(first['skill'])
        (skill / 'local-note.txt').write_text('preserve me in backup')
        second = installer.install(self.args)
        self.assertNotEqual(first['release'], second['release'])
        self.assertTrue(Path(first['release']).is_dir())
        self.assertEqual(second['previous_release'], first['release'])
        self.assertEqual((Path(self.args.prefix) / 'current').resolve(), Path(second['release']))
        self.assertEqual(unrelated.read_text(), 'untouched')
        self.assertEqual(config.read_text(), 'original = true\n')
        entries = json.loads((Path(second['backup']) / 'index.json').read_text())
        backup = next(Path(e['backup']) for e in entries if e['path'] == str(skill))
        self.assertEqual((backup / 'local-note.txt').read_text(), 'preserve me in backup')
        self.assertIn(str(Path(self.args.prefix) / 'bin/jcm'), (skill / 'JCM_RUNTIME.md').read_text())
        self.assertFalse((self.base / '.jcm').exists())

    def test_conflicting_skill_requires_explicit_replacement(self):
        skill = Path(self.args.skill_dir)
        skill.mkdir(parents=True)
        (skill / 'SKILL.md').write_text('original skill')
        with self.assertRaisesRegex(installer.InstallError, 'Existing astra-continuity'):
            installer.install(self.args)
        self.build.assert_not_called()
        self.assertEqual((skill / 'SKILL.md').read_text(), 'original skill')
        self.args.replace_existing = True
        result = installer.install(self.args)
        entries = json.loads((Path(result['backup']) / 'index.json').read_text())
        saved = next(Path(e['backup']) for e in entries if e['path'] == str(skill))
        self.assertEqual((saved / 'SKILL.md').read_text(), 'original skill')

    def test_failed_upgrade_restores_runtime_launcher_skill_and_manifest(self):
        first = installer.install(self.args)
        prefix = Path(self.args.prefix)
        manifest = (prefix / 'install.json').read_bytes()
        skill_content = (Path(self.args.skill_dir) / 'SKILL.md').read_bytes()
        launcher = os.readlink(Path(self.args.bin_dir) / 'jcm')
        self.validate.side_effect = installer.InstallError('injected activation failure')
        with self.assertRaisesRegex(installer.InstallError, 'activation failure'):
            installer.install(self.args)
        self.assertEqual((prefix / 'current').resolve(), Path(first['release']))
        self.assertEqual((prefix / 'install.json').read_bytes(), manifest)
        self.assertEqual((Path(self.args.skill_dir) / 'SKILL.md').read_bytes(), skill_content)
        self.assertEqual(os.readlink(Path(self.args.bin_dir) / 'jcm'), launcher)

    def test_build_failure_never_switches_existing_runtime(self):
        first = installer.install(self.args)
        self.build.side_effect = installer.InstallError('offline build')
        with self.assertRaisesRegex(installer.InstallError, 'offline'):
            installer.install(self.args)
        self.assertEqual((Path(self.args.prefix) / 'current').resolve(), Path(first['release']))

    def test_no_skill_and_launcher_conflict(self):
        self.args.no_skill = True
        launcher = Path(self.args.bin_dir) / 'jcm'
        launcher.parent.mkdir()
        launcher.write_text('original executable')
        with self.assertRaisesRegex(installer.InstallError, 'Existing jcm launcher'):
            installer.install(self.args)
        self.args.replace_existing = True
        result = installer.install(self.args)
        self.assertIsNone(result['skill'])
        self.assertFalse(Path(self.args.skill_dir).exists())
        entries = json.loads((Path(result['backup']) / 'index.json').read_text())
        saved = next(Path(e['backup']) for e in entries if e['path'] == str(launcher))
        self.assertEqual(saved.read_text(), 'original executable')

    def test_overlapping_or_unmanaged_prefix_is_refused(self):
        self.args.skill_dir = str(Path(self.args.prefix) / 'current')
        with self.assertRaisesRegex(installer.InstallError, 'overlap'):
            installer.install(self.args)
        self.args.no_skill = True
        prefix = Path(self.args.prefix)
        prefix.mkdir()
        (prefix / 'current').write_text('unmanaged')
        with self.assertRaisesRegex(installer.InstallError, 'Unmanaged'):
            installer.install(self.args)
        self.assertEqual((prefix / 'current').read_text(), 'unmanaged')

    def test_managed_runtime_binding_uses_stable_launcher_and_rejects_bad_path(self):
        from jcm import config
        from jcm.util import JCMError
        launcher = self.base / 'managed jcm'
        launcher.write_text('#!/bin/sh\nexit 0\n')
        launcher.chmod(0o700)
        with patch.dict(os.environ, {'JCM_LAUNCHER': str(launcher)}):
            self.assertEqual(config.runtime_argv(), [str(launcher)])
        with patch.dict(os.environ, {'JCM_LAUNCHER': 'relative-or-missing'}):
            with self.assertRaisesRegex(JCMError, 'INVALID_RUNTIME_LAUNCHER'):
                config.runtime_argv()

    def archive(self, name, symlink=False):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode='w:gz') as tar:
            member = tarfile.TarInfo(name)
            if symlink:
                member.type = tarfile.SYMTYPE
                member.linkname = '/outside'
                tar.addfile(member)
            else:
                member.size = 3
                tar.addfile(member, io.BytesIO(b'bad'))
        return data.getvalue()

    def test_remote_archive_rejects_traversal_and_symlinks(self):
        for i, (name, link) in enumerate([('root/../../outside', False), ('root/src/jcm/link', True)]):
            with patch.object(installer, 'fetch', side_effect=[json.dumps({'sha': 'a' * 40}).encode(), self.archive(name, link)]):
                with self.assertRaises(installer.InstallError):
                    installer.prepare_source(self.base / str(i), 'main')
        self.assertFalse((self.base / 'outside').exists())

    def test_remote_revision_is_resolved_before_source_download(self):
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode='w:gz') as tar:
            for path in installer.source_files(ROOT):
                tar.add(path, arcname='root/' + str(path.relative_to(ROOT)))
        with patch.object(installer, 'fetch', side_effect=[json.dumps({'sha': 'b' * 40}).encode(), data.getvalue()]) as fetch:
            info = installer.prepare_source(self.base / 'remote', 'branch/name')
        self.assertEqual(info['revision'], 'b' * 40)
        self.assertIn('branch%2Fname', fetch.call_args_list[0].args[0])
        self.assertTrue(fetch.call_args_list[1].args[0].endswith('b' * 40))


if __name__ == '__main__':
    unittest.main()
