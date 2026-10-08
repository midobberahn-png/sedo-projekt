"""Optional Developer ID selection; no key import, unlock or account changes."""
from __future__ import annotations

import os
import re
import subprocess
import sys


def codesign_identity() -> str | None:
    """Return a verified public certificate fingerprint, or keep test signing.

    A display name is intentionally not accepted: an explicit fingerprint
    removes ambiguity between development and distribution certificates. The
    keychain command reads only valid public identity metadata, never keys.
    """
    selected = os.environ.get('SEDO_CODESIGN_IDENTITY', '').strip().upper()
    if not selected:
        return None
    if sys.platform != 'darwin' or not re.fullmatch(r'[0-9A-F]{40}', selected):
        raise RuntimeError('SEDO_CODESIGN_IDENTITY requires a macOS Developer ID Application certificate fingerprint.')
    try:
        result = subprocess.run(
            ['/usr/bin/security', 'find-identity', '-v', '-p', 'codesigning'],
            check=True, capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError('The selected distribution signing identity could not be verified.') from exc
    valid = re.findall(
        r'^\s*\d+\)\s+([0-9A-Fa-f]{40})\s+"Developer ID Application:[^"\r\n]+"\s*$',
        result.stdout, flags=re.MULTILINE,
    )
    if selected not in {fingerprint.upper() for fingerprint in valid}:
        raise RuntimeError('The selected certificate is not a valid Developer ID Application identity.')
    return selected
