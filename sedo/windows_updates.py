"""Benutzerinitiierte, signaturgeprüfte Windows-Updates.

Das öffentliche Release-Repository enthält nur Installer und Manifeste, nie
Quellcode oder Zugangsdaten. Ein SHA-256 aus derselben HTTP-Antwort wäre keine
Authentisierung; deshalb muss jedes Manifest mit einem hier fixierten
Ed25519-Schlüssel signiert sein. Ohne Schlüssel bleibt der Kanal gesperrt.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


WINDOWS_VERSION = '3.21.0.5'
RELEASE_REPOSITORY = 'midobberahn-png/sedo-windows-updates'
RELEASES_API = f'https://api.github.com/repos/{RELEASE_REPOSITORY}/releases?per_page=20'
PUBLIC_KEY_B64 = 'Psl84XRBK8gchHE+62pRhlhqtGOyyp1n5TWkgPby6wI='
MANIFEST_NAME = 'SEDO-update.json'
MAX_INSTALLER_BYTES = 1024 * 1024 * 1024
_VERSION = re.compile(r'\d+\.\d+\.\d+\.\d+\Z')
_SHA256 = re.compile(r'[0-9a-f]{64}\Z')


class UpdateError(RuntimeError):
    """Das Update darf nicht angewendet werden."""


@dataclass(frozen=True)
class Update:
    version: str
    url: str
    sha256: str
    size: int


def _version(value: str) -> tuple[int, int, int, int]:
    if not isinstance(value, str) or not _VERSION.fullmatch(value):
        raise UpdateError('Ungültige Windows-Versionsnummer im Update.')
    return tuple(map(int, value.split('.')))


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False).encode('utf-8')


def _https(url: str, host: str | None = None) -> str:
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
            or parsed.password or parsed.fragment or (host and parsed.hostname != host)):
        raise UpdateError('Update-Adresse ist nicht vertrauenswürdig.')
    return url


def _fetch(url: str, limit: int) -> bytes:
    _https(url)
    request = Request(url, headers={'User-Agent': 'SEDO-Windows-Updater',
                                    'Accept': 'application/vnd.github+json'})
    try:
        with urlopen(request, timeout=20) as response:
            _https(response.geturl())
            data = response.read(limit + 1)
    except (OSError, ValueError) as exc:
        raise UpdateError('Update-Server ist nicht erreichbar.') from exc
    if len(data) > limit:
        raise UpdateError('Update-Antwort ist zu groß.')
    return data


def _asset(release: dict, name: str) -> dict:
    matches = [asset for asset in release.get('assets', [])
               if isinstance(asset, dict) and asset.get('name') == name]
    if len(matches) != 1:
        raise UpdateError('Das Update-Paket ist unvollständig oder doppelt vorhanden.')
    return matches[0]


def _release_url(tag: str, name: str) -> str:
    return f'https://github.com/{RELEASE_REPOSITORY}/releases/download/{tag}/{name}'


def _verify_manifest(raw: bytes, release: dict) -> Update:
    if not PUBLIC_KEY_B64:
        raise UpdateError('Der Windows-Updatekanal ist noch nicht aktiviert.')
    try:
        manifest = json.loads(raw)
        if not isinstance(manifest, dict) or set(manifest) != {
                'schema', 'channel', 'version', 'installer_sha256',
                'installer_size', 'signature'}:
            raise ValueError('fields')
        signature = base64.b64decode(manifest.pop('signature'), validate=True)
        key_bytes = base64.b64decode(PUBLIC_KEY_B64, validate=True)
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        Ed25519PublicKey.from_public_bytes(key_bytes).verify(signature, _json_bytes(manifest))
    except (ValueError, TypeError, KeyError, ImportError) as exc:
        raise UpdateError('Update-Manifest ist ungültig oder nicht prüfbar.') from exc
    except Exception as exc:
        # InvalidSignature stammt aus cryptography; bewusst kein unsignierter Fallback.
        raise UpdateError('Update-Signatur ist ungültig.') from exc
    version = manifest['version']
    _version(version)
    if type(manifest['schema']) is not int or manifest['schema'] != 1 or manifest['channel'] != 'test':
        raise UpdateError('Unbekannter Updatekanal oder Manifestformat.')
    tag = f'v{version}-windows'
    if release.get('tag_name') != tag or release.get('prerelease') is not True:
        raise UpdateError('Update-Version passt nicht zum Release.')
    name = f'SEDO_Rechnungstool_Setup_{version}.exe'
    url = _release_url(tag, name)
    asset = _asset(release, name)
    if asset.get('browser_download_url') != url:
        raise UpdateError('Installer-Adresse passt nicht zum Release.')
    size = manifest['installer_size']
    digest = manifest['installer_sha256']
    if (type(size) is not int or size <= 0 or size > MAX_INSTALLER_BYTES
            or not isinstance(digest, str) or not _SHA256.fullmatch(digest)
            or asset.get('size') != size):
        raise UpdateError('Installer-Prüfdaten sind ungültig.')
    return Update(version, url, digest, size)


def check() -> Update | None:
    """Liest nur den öffentlichen Testkanal; installiert niemals selbständig."""
    if not PUBLIC_KEY_B64:
        raise UpdateError('Der Windows-Updatekanal ist noch nicht aktiviert.')
    try:
        releases = json.loads(_fetch(RELEASES_API, 512 * 1024))
    except (ValueError, TypeError) as exc:
        raise UpdateError('Update-Liste ist ungültig.') from exc
    if not isinstance(releases, list):
        raise UpdateError('Update-Liste ist ungültig.')
    current = _version(WINDOWS_VERSION)
    candidates = []
    for release in releases:
        if not isinstance(release, dict) or release.get('draft') or release.get('prerelease') is not True:
            continue
        tag = release.get('tag_name', '')
        if not isinstance(tag, str) or not re.fullmatch(r'v\d+\.\d+\.\d+\.\d+-windows', tag):
            continue
        version = tag[1:-8]
        if _version(version) > current:
            candidates.append((_version(version), release))
    if not candidates:
        return None
    _, latest = max(candidates, key=lambda candidate: candidate[0])
    tag = latest['tag_name']
    manifest_asset = _asset(latest, MANIFEST_NAME)
    if manifest_asset.get('browser_download_url') != _release_url(tag, MANIFEST_NAME):
        raise UpdateError('Manifest-Adresse passt nicht zum Release.')
    return _verify_manifest(_fetch(manifest_asset['browser_download_url'], 16 * 1024), latest)


def download(update: Update, directory: Path) -> Path:
    """Lädt atomar, begrenzt und prüft die signierte Installer-Prüfsumme."""
    version = update.version
    _version(version)
    expected = _release_url(f'v{version}-windows', f'SEDO_Rechnungstool_Setup_{version}.exe')
    if (update.url != expected or not isinstance(update.sha256, str)
            or not _SHA256.fullmatch(update.sha256)
            or type(update.size) is not int or update.size <= 0
            or update.size > MAX_INSTALLER_BYTES):
        raise UpdateError('Installer-Adresse oder Prüfsumme ist ungültig.')
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f'SEDO_Rechnungstool_Setup_{version}.exe'
    fd, temporary = tempfile.mkstemp(prefix='.sedo-update-', suffix='.part', dir=directory)
    digest = hashlib.sha256()
    total = 0
    try:
        with os.fdopen(fd, 'wb') as output:
            request = Request(update.url, headers={'User-Agent': 'SEDO-Windows-Updater'})
            with urlopen(request, timeout=30) as response:
                _https(response.geturl())
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    total += len(block)
                    if total > update.size or total > MAX_INSTALLER_BYTES:
                        raise UpdateError('Installer ist größer als angekündigt.')
                    output.write(block)
                    digest.update(block)
        if total != update.size or digest.hexdigest() != update.sha256:
            raise UpdateError('Installer wurde beschädigt oder verändert.')
        os.replace(temporary, target)
    except BaseException as exc:
        Path(temporary).unlink(missing_ok=True)
        if isinstance(exc, OSError):
            raise UpdateError('Der Windows-Installer konnte nicht vollständig geladen werden.') from exc
        raise
    return target


def start_installer(installer: Path, installed_exe: Path, log: Path) -> None:
    """Überlässt das Ersetzen dem vorhandenen Inno-Setup; Daten bleiben separat."""
    if os.name != 'nt' or not (installed_exe.parent / 'unins000.exe').is_file():
        raise UpdateError('SEDO muss regulär unter Windows installiert sein.')
    if not installer.is_file() or not installed_exe.is_file():
        raise UpdateError('Installiertes Programm oder geprüfter Installer fehlt.')
    subprocess.Popen([str(installer), '/SILENT',
                      '/NORESTART', '/SP-', '/CLOSEAPPLICATIONS',
                      '/SEDOUPDATE=1', '/DIR=' + str(installed_exe.parent),
                      '/LOG=' + str(log)], cwd=installer.parent, close_fds=True)
