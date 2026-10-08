"""Strict checks for newly built or freshly unpacked Apple Silicon apps."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import plistlib
import re
import subprocess

_MAGIC = {b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf',
          b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca',
          b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'}
_BUILD_DETRITUS = frozenset({'com.apple.FinderInfo', 'com.apple.ResourceFork'})


def _version(text):
    if not isinstance(text, str) or not re.fullmatch(r'\d+\.\d+(?:\.\d+)?', text):
        raise ValueError('A concrete macOS deployment target is required.')
    values = tuple(map(int, text.split('.')))
    return values + (0,) * (3 - len(values))


def _mach_o_versions(path):
    from macholib.MachO import MachO
    from macholib.mach_o import CPU_TYPE_NAMES, LC_BUILD_VERSION, LC_VERSION_MIN_MACOSX
    result = []
    for header in MachO(str(path)).headers:
        architecture = CPU_TYPE_NAMES.get(header.header.cputype, 'unknown')
        commands = [command for command in header.commands
                    if command[0].cmd in (LC_BUILD_VERSION, LC_VERSION_MIN_MACOSX)]
        if len(commands) != 1:
            raise ValueError('A native library has no unique macOS deployment target.')
        command, value, _ = commands[0]
        encoded = value.minos if command.cmd == LC_BUILD_VERSION else value.version
        result.append((architecture, ((encoded >> 16) & 0xFFFF, (encoded >> 8) & 0xFF, encoded & 0xFF)))
    return result


def _clean_build_metadata(bundle):
    # CPython's os.*xattr APIs are Linux-only. macOS's own tool supports
    # no-follow symlink cleanup and ignores an attribute that is not present.
    for attribute in sorted(_BUILD_DETRITUS):
        subprocess.run(['/usr/bin/xattr', '-r', '-s', '-d', attribute, str(bundle)],
                       check=True, capture_output=True, text=True, timeout=120)


def inspect_bundle(bundle, *, clean_build_metadata=False):
    bundle = Path(bundle).absolute()
    # Do not follow a caller-supplied app or ancestor link before validating the
    # target: cleanup must never be redirected to a different installation.
    if any(path.is_symlink() for path in (bundle, *bundle.parents)):
        raise ValueError('The app bundle and its parent directories must not be symbolic links.')
    bundle = bundle.resolve()
    if not bundle.is_dir() or bundle.suffix != '.app':
        raise ValueError('An existing app bundle is required.')
    paths = [bundle, *bundle.rglob('*')]
    # Validate all links before any optional cleanup; never touch outside files.
    for path in paths:
        if not path.resolve().is_relative_to(bundle):
            raise ValueError('An app bundle contains an external symbolic link.')
    info = plistlib.loads((bundle / 'Contents/Info.plist').read_bytes())
    declared = _version(info.get('LSMinimumSystemVersion'))
    if declared < (14, 0, 0) or info.get('LSArchitecturePriority') != ['arm64']:
        raise ValueError('This release must declare Apple Silicon and macOS 14 or newer.')
    executable = bundle / 'Contents/MacOS' / info['CFBundleExecutable']
    if not executable.resolve().is_relative_to(bundle) or not executable.is_file():
        raise ValueError('The declared app executable is missing or external.')
    seen, native_paths, binaries = set(), set(), []
    for path in paths:
        if not path.is_file() or path.resolve() in seen:
            continue
        seen.add(path.resolve())
        with path.open('rb') as handle:
            if handle.read(4) not in _MAGIC:
                continue
        slices = _mach_o_versions(path)
        if not slices or {architecture for architecture, _ in slices} != {'ARM64'}:
            raise ValueError('This release contains a non-Apple-Silicon native binary.')
        if any(minimum > declared for _, minimum in slices):
            raise ValueError('A native library requires a newer macOS than the app declares.')
        native_paths.add(path.resolve())
        binaries.append({'file': str(path.relative_to(bundle)),
                         'minimum_macos': '.'.join(map(str, max(version for _, version in slices)))})
    if not binaries or executable.resolve() not in native_paths:
        raise ValueError('The native app is incomplete.')
    if clean_build_metadata:
        # Only the two attributes codesign forbids are build detritus. Never
        # remove quarantine, provenance, or other security/provider attributes.
        _clean_build_metadata(bundle)
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(bundle)],
                   check=True, capture_output=True, text=True, timeout=120)
    return {'architecture': 'arm64', 'declared_minimum_macos': info['LSMinimumSystemVersion'],
            'highest_required_macos': max((row['minimum_macos'] for row in binaries), key=_version),
            'binary_count': len(binaries), 'strict_signature_valid': True,
            'cleaned_build_metadata': sorted(_BUILD_DETRITUS) if clean_build_metadata else [],
            'version': info['CFBundleShortVersionString'],
            'binaries': binaries}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--clean-build-metadata', action='store_true')
    args = parser.parse_args()
    report = inspect_bundle(args.bundle, clean_build_metadata=args.clean_build_metadata)
    args.report.write_text(json.dumps(report, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
