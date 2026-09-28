#!/usr/bin/env python3
"""Read-only, dated check for six known pypdf advisories; NOT a release approval.

Exit 1: at least one known affected version range matches the explicit pin.
Exit 0: none of these six ranges matches; other vulnerabilities remain unassessed.
Exit 2: unsupported/missing/ambiguous input, never silently treated as safe.
No network, package installation, application import, or business-data access.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

SNAPSHOT_DATE = '2026-09-28'
ADVISORIES = (
    ('GHSA-jw7q-gvrg-4vj3', '6.18.1', 'Malformed FlateDecode recovery runtime', '2026-09-11'),
    ('GHSA-g9cg-prrw-2r8q', '6.18.1', 'Font Widths memory consumption', '2026-09-11'),
    ('GHSA-fp3h-c4fm-7vvf', '6.18.1', 'ToUnicode memory consumption', '2026-09-11'),
    ('GHSA-php9-fj8v-98fj', '6.19.0', 'Appearance stream generation or flattening runtime', '2026-09-16'),
    ('GHSA-w23x-9jrw-r45c', '6.19.0', 'Alphabetical page-label memory consumption', '2026-09-16'),
    ('GHSA-v247-6f48-mgcj', '6.19.0', 'Dictionary-based embedded-file API runtime', '2026-09-16'),
)


def stable_version(value: str) -> tuple[int, int, int]:
    if not re.fullmatch(r'[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}', value):
        raise ValueError('Expected an explicit stable x.y.z version; pre/dev/local releases require manual review.')
    return tuple(map(int, value.split('.')))


def assess(data: bytes) -> dict:
    if len(data) > 2_000_000:
        raise ValueError('Lock input exceeds the 2 MB review limit.')
    text = data.decode('utf-8')
    pins = re.findall(r'^\s*pypdf\s*==\s*([^\s\\;#]+)', text, re.MULTILINE | re.IGNORECASE)
    if len(pins) != 1:
        raise ValueError('Exactly one explicit pypdf==x.y.z pin is required.')
    version = stable_version(pins[0])
    matches = []
    for identifier, fixed, description, published in ADVISORIES:
        if version < stable_version(fixed):
            matches.append({
                'id': identifier, 'affected_range': '<'+fixed, 'fixed_from': fixed,
                'upstream_severity': 'Moderate', 'published': published,
                'description': description,
                'source': 'https://github.com/py-pdf/pypdf/security/advisories/'+identifier,
                'application_exploit_demonstrated': False,
            })
    return {
        'snapshot_date': SNAPSHOT_DATE, 'package': 'pypdf', 'pinned_version': pins[0],
        'lock_sha256': hashlib.sha256(data).hexdigest(),
        'known_matching_advisories': matches, 'match_count': len(matches),
        'verdict': 'BLOCKED_KNOWN_DEPENDENCY_ADVISORIES' if matches else 'NO_MATCH_IN_THIS_DATED_SUBSET',
        'full_dependency_scan': False, 'security_release_approved': False,
        'scope': 'Six maintainer advisories only; version-range check is not an application exploit test or full SCA.',
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('lock', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        with args.lock.open('rb') as handle:
            data = handle.read(2_000_001)
        result = assess(data)
        output = json.dumps(result, indent=2, ensure_ascii=False)+'\n'
        if args.output:
            args.output.write_text(output, encoding='utf-8')
        print(output, end='')
        return 1 if result['match_count'] else 0
    except (OSError, UnicodeError, ValueError) as exc:
        print('Review failed: '+str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
