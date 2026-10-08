"""Signing selection fails closed without touching private key material."""
import importlib.util
import os
from pathlib import Path
import plistlib
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('sedo_macos_signing', ROOT / 'tools/macos_signing.py')
signing = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(signing)


class MacOSSigningTests(unittest.TestCase):
    fingerprint = 'A' * 40

    def test_default_does_not_query_keychain(self):
        with patch.dict(os.environ, {'SEDO_CODESIGN_IDENTITY': ''}), patch.object(signing.subprocess, 'run') as command:
            self.assertIsNone(signing.codesign_identity())
            command.assert_not_called()

    def test_requires_exact_fingerprint_and_macos(self):
        for platform, selected in (('darwin', '-'), ('darwin', 'Developer ID Application: Example'),
                                   ('darwin', 'A' * 39), ('linux', self.fingerprint)):
            with self.subTest(platform=platform, selected=selected), \
                    patch.dict(os.environ, {'SEDO_CODESIGN_IDENTITY': selected}), \
                    patch.object(signing.sys, 'platform', platform), \
                    patch.object(signing.subprocess, 'run') as command:
                with self.assertRaises(RuntimeError):
                    signing.codesign_identity()
                command.assert_not_called()

    def test_valid_developer_id_is_selected_without_name_disclosure(self):
        output = f'  1) {self.fingerprint} "Developer ID Application: Synthetic Publisher (TESTONLY)"\n  1 valid identities found\n'
        with patch.dict(os.environ, {'SEDO_CODESIGN_IDENTITY': self.fingerprint.lower()}), \
                patch.object(signing.sys, 'platform', 'darwin'), \
                patch.object(signing.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, output)) as command:
            self.assertEqual(signing.codesign_identity(), self.fingerprint)
            self.assertEqual(command.call_args.args[0], ['/usr/bin/security', 'find-identity', '-v', '-p', 'codesigning'])
            self.assertEqual(command.call_args.kwargs['timeout'], 10)

    def test_development_missing_invalid_or_other_identity_rejected(self):
        for output in (
            f'  1) {self.fingerprint} "Apple Development: Synthetic Publisher (TESTONLY)"\n',
            f'  1) {self.fingerprint} "Developer ID Application: Synthetic Publisher (TESTONLY)" (CSSMERR_TP_CERT_EXPIRED)\n',
            f'  1) {"B" * 40} "Developer ID Application: Synthetic Publisher (TESTONLY)"\n',
            '     0 valid identities found\n',
        ):
            with self.subTest(output=output), \
                    patch.dict(os.environ, {'SEDO_CODESIGN_IDENTITY': self.fingerprint}), \
                    patch.object(signing.sys, 'platform', 'darwin'), \
                    patch.object(signing.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, output)):
                with self.assertRaises(RuntimeError):
                    signing.codesign_identity()

    def test_keychain_failure_is_not_silently_downgraded(self):
        for error in (OSError('unavailable'), subprocess.TimeoutExpired('security', 10),
                      subprocess.CalledProcessError(1, 'security')):
            with self.subTest(error=type(error).__name__), \
                    patch.dict(os.environ, {'SEDO_CODESIGN_IDENTITY': self.fingerprint}), \
                    patch.object(signing.sys, 'platform', 'darwin'), \
                    patch.object(signing.subprocess, 'run', side_effect=error):
                with self.assertRaises(RuntimeError):
                    signing.codesign_identity()

    def test_spec_passes_identity_to_exe_for_bundle_propagation(self):
        import ast
        tree = ast.parse((ROOT / 'sedo_desktop.spec').read_text())
        exe = next(node for node in ast.walk(tree)
                   if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'EXE')
        selected = next(keyword.value for keyword in exe.keywords if keyword.arg == 'codesign_identity')
        self.assertIsInstance(selected, ast.Name)
        self.assertEqual(selected.id, 'signing_identity')

    def test_only_microphone_capability_is_granted(self):
        entitlements = plistlib.loads((ROOT / 'tools/macos-entitlements.plist').read_bytes())
        self.assertEqual(entitlements, {'com.apple.security.device.audio-input': True})

    def test_entitlements_are_applied_only_with_distribution_identity(self):
        import ast
        tree = ast.parse((ROOT / 'sedo_desktop.spec').read_text())
        exe = next(node for node in ast.walk(tree)
                   if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'EXE')
        selected = next(keyword.value for keyword in exe.keywords if keyword.arg == 'entitlements_file')
        self.assertIsInstance(selected, ast.IfExp)
        self.assertIsInstance(selected.test, ast.Name)
        self.assertEqual(selected.test.id, 'signing_identity')
        self.assertIsNone(ast.literal_eval(selected.orelse))
        expression = compile(ast.Expression(selected.body), '<entitlement-test>', 'eval')
        self.assertEqual(eval(expression, {'str': str, 'Path': Path, 'SPECPATH': str(ROOT)}),
                         str(ROOT / 'tools/macos-entitlements.plist'))
