"""Release metadata and strict ZIP/signature gates, using synthetic bundles."""
import ast
import importlib.util
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('sedo_macos_bundle', ROOT / 'tools/macos_bundle.py')
bundle_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle_check)


class MacOSBundleTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.bundle = Path(directory.name).resolve() / 'Synthetic.app'
        self.executable = self.bundle / 'Contents/MacOS/synthetic'
        self.executable.parent.mkdir(parents=True)
        self.executable.write_bytes(b'\xcf\xfa\xed\xfe' + b'synthetic-not-executable')
        self.info = {'CFBundleExecutable': 'synthetic', 'CFBundleShortVersionString': '0.0.1',
                     'LSMinimumSystemVersion': '14.0', 'LSArchitecturePriority': ['arm64']}
        self.write_info()
        version_patch = patch.object(bundle_check, '_mach_o_versions', return_value=[('ARM64', (14, 0, 0))])
        self.versions = version_patch.start()
        self.addCleanup(version_patch.stop)
        command_patch = patch.object(bundle_check.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0))
        self.command = command_patch.start()
        self.addCleanup(command_patch.stop)

    def write_info(self):
        (self.bundle / 'Contents/Info.plist').write_bytes(plistlib.dumps(self.info))

    def test_complete_metadata_and_strict_signature_required(self):
        with patch.object(bundle_check, '_clean_build_metadata') as attributes:
            report = bundle_check.inspect_bundle(self.bundle)
            attributes.assert_not_called()
        self.assertEqual(report['binary_count'], 1)
        self.assertEqual(report['architecture'], 'arm64')
        self.assertEqual(report['highest_required_macos'], '14.0.0')
        self.assertTrue(report['strict_signature_valid'])
        self.command.assert_called_once_with(
            ['/usr/bin/codesign', '--verify', '--deep', '--strict', str(self.bundle.resolve())],
            check=True, capture_output=True, text=True, timeout=120)

    def test_missing_low_or_invalid_minimum_fails_before_signing(self):
        for minimum in (None, '11.0', '13.9.9', '14', 'current'):
            with self.subTest(minimum=minimum):
                self.info['LSMinimumSystemVersion'] = minimum if minimum is not None else ''
                self.write_info()
                with self.assertRaises(ValueError):
                    bundle_check.inspect_bundle(self.bundle)
        self.command.assert_not_called()

    def test_newer_dependency_cannot_silently_raise_minimum(self):
        self.versions.return_value = [('ARM64', (15, 0, 0))]
        with self.assertRaises(ValueError):
            bundle_check.inspect_bundle(self.bundle)
        self.command.assert_not_called()

    def test_non_arm64_dependency_is_rejected(self):
        self.versions.return_value = [('x86_64', (14, 0, 0))]
        with self.assertRaises(ValueError):
            bundle_check.inspect_bundle(self.bundle)

    def test_architecture_declaration_is_mandatory(self):
        del self.info['LSArchitecturePriority']
        self.write_info()
        with self.assertRaises(ValueError):
            bundle_check.inspect_bundle(self.bundle)

    def test_main_executable_must_really_be_native(self):
        self.executable.write_bytes(b'not-a-native-executable')
        with self.assertRaises(ValueError):
            bundle_check.inspect_bundle(self.bundle)

    def test_external_links_rejected_before_metadata_cleanup(self):
        (self.bundle / 'external').symlink_to(self.bundle.parent)
        with patch.object(bundle_check, '_clean_build_metadata') as attributes:
            with self.assertRaises(ValueError):
                bundle_check.inspect_bundle(self.bundle, clean_build_metadata=True)
            attributes.assert_not_called()

    def test_top_level_bundle_symlink_rejected_without_system_commands(self):
        alias = self.bundle.parent / 'Alias.app'
        alias.symlink_to(self.bundle, target_is_directory=True)
        with self.assertRaises(ValueError):
            bundle_check.inspect_bundle(alias, clean_build_metadata=True)
        self.versions.assert_not_called()
        self.command.assert_not_called()

    def test_parent_directory_symlink_rejected_without_system_commands(self):
        alias = self.bundle.parent / 'linked-parent'
        alias.symlink_to(self.bundle.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            bundle_check.inspect_bundle(alias / self.bundle.name, clean_build_metadata=True)
        self.versions.assert_not_called()
        self.command.assert_not_called()

    def test_cleanup_preserves_quarantine_provenance_and_other_attributes(self):
        report = bundle_check.inspect_bundle(self.bundle, clean_build_metadata=True)
        self.assertEqual(set(report['cleaned_build_metadata']),
                         {'com.apple.FinderInfo', 'com.apple.ResourceFork'})
        commands = [call.args[0] for call in self.command.call_args_list]
        cleanup = [command for command in commands if command[0] == '/usr/bin/xattr']
        self.assertEqual(len(cleanup), 2)
        self.assertEqual({command[4] for command in cleanup},
                         {'com.apple.FinderInfo', 'com.apple.ResourceFork'})
        self.assertTrue(all(command[1:4] == ['-r', '-s', '-d'] for command in cleanup))

    def test_bad_signature_is_never_downgraded_to_warning(self):
        self.command.side_effect = subprocess.CalledProcessError(1, 'codesign')
        with self.assertRaises(subprocess.CalledProcessError):
            bundle_check.inspect_bundle(self.bundle)

    def test_spec_declares_matching_minimum_and_architecture(self):
        tree = ast.parse((ROOT / 'sedo_desktop.spec').read_text())
        bundle = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'BUNDLE')
        info = next(keyword.value for keyword in bundle.keywords if keyword.arg == 'info_plist')
        values = {ast.literal_eval(key): value for key, value in zip(info.keys, info.values)}
        self.assertEqual(ast.literal_eval(values['LSMinimumSystemVersion']), '14.0')
        self.assertEqual(ast.literal_eval(values['LSArchitecturePriority']), ['arm64'])

    def test_archive_recipe_preserves_links_but_excludes_xattrs(self):
        tree = ast.parse((ROOT / 'tools/teststand.py').read_text())
        commands = []
        for node in ast.walk(tree):
            if isinstance(node, ast.List) and node.elts and isinstance(node.elts[0], ast.Constant) and node.elts[0].value == 'ditto':
                commands.append([item.value for item in node.elts if isinstance(item, ast.Constant)])
        archive = next(command for command in commands if '-c' in command)
        self.assertIn('--norsrc', archive)
        self.assertIn('--noextattr', archive)
        self.assertIn('--keepParent', archive)
        self.assertNotIn('--sequesterRsrc', archive)

    def test_native_mac_dist_is_staged_outside_the_output_fileprovider(self):
        tree = ast.parse((ROOT / 'tools/teststand.py').read_text())
        assignment = next(node for node in ast.walk(tree)
                          if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
                             and target.id == 'native_dist' for target in node.targets))
        expression = compile(ast.Expression(assignment.value), '<native-dist-test>', 'eval')
        from types import SimpleNamespace
        self.assertEqual(eval(expression, {'Path': Path, 'tmp': '/private/tmp/synthetic',
                                          'output': Path('/synthetic-output'),
                                          'sys': SimpleNamespace(platform='darwin')}),
                         Path('/private/tmp/synthetic/desktop'))

    def test_fresh_archive_verification_does_not_clean_metadata(self):
        tree = ast.parse((ROOT / 'tools/teststand.py').read_text())
        commands = [node for node in ast.walk(tree) if isinstance(node, ast.List)
                    and 'tools/macos_bundle.py' in ast.unparse(node)]
        self.assertEqual(len(commands), 2)
        fresh = next(node for node in commands if 'archive-check.json' in ast.unparse(node))
        self.assertNotIn('--clean-build-metadata', ast.unparse(fresh))


if __name__ == '__main__':
    unittest.main()
