"""Sign the verified Windows installer manifest for the public binary feed.

Only a green Windows installation test may be published. The signing seed is
read from a CI secret and never written to the repository or release assets.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import sys

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sedo import windows_updates


def build_manifest(setup: Path, build_info: Path, installation_test: Path,
                   signing_seed_b64: str) -> dict:
    version = windows_updates.WINDOWS_VERSION
    if setup.name != f'SEDO_Rechnungstool_Setup_{version}.exe':
        raise ValueError('Der Installer passt nicht zur Programmversion.')
    build = json.loads(build_info.read_text(encoding='utf-8'))
    smoke = json.loads(installation_test.read_text(encoding='utf-8-sig'))
    with setup.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if (build.get('version') != version or build.get('setup_sha256') != digest
            or smoke.get('installer_sha256') != digest
            or smoke.get('installed_program_self_check') is not True
            or smoke.get('upgrade_preserved_data') is not True
            or smoke.get('uninstall_successful') is not True
            or smoke.get('github_commit') != build.get('commit')
            or build.get('windows_checks', 0) < 10):
        raise ValueError('Geprüfter Windows-Installer oder Installationsnachweis fehlt.')
    if not windows_updates.PUBLIC_KEY_B64:
        raise ValueError('Der öffentliche Update-Schlüssel ist noch nicht festgelegt.')
    seed = base64.b64decode(signing_seed_b64, validate=True)
    if len(seed) != 32:
        raise ValueError('Ungültiger Signaturschlüssel.')
    private = Ed25519PrivateKey.from_private_bytes(seed)
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)
    if base64.b64encode(public).decode('ascii') != windows_updates.PUBLIC_KEY_B64:
        raise ValueError('Der CI-Schlüssel passt nicht zum fest eingebauten Prüfschlüssel.')
    manifest = {
        'schema': 1, 'channel': 'test', 'version': version,
        'installer_sha256': digest, 'installer_size': setup.stat().st_size,
    }
    manifest['signature'] = base64.b64encode(
        private.sign(windows_updates._json_bytes(manifest))).decode('ascii')
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--setup', type=Path, required=True)
    parser.add_argument('--build-info', type=Path, required=True)
    parser.add_argument('--installation-test', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    seed = os.environ.get('SEDO_UPDATE_SIGNING_KEY', '')
    if not seed:
        parser.error('SEDO_UPDATE_SIGNING_KEY fehlt.')
    result = build_manifest(args.setup, args.build_info, args.installation_test, seed)
    with args.output.open('x', encoding='utf-8') as target:
        json.dump(result, target, sort_keys=True, indent=2)
        target.write('\n')
    print(f'Signiertes Windows-Manifest: {args.output}')


if __name__ == '__main__':
    main()
