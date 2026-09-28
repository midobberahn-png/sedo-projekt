import importlib.util
from pathlib import Path
import unittest

from tools import check_known_pypdf_advisories as checker

class AdvisoryCheckerTests(unittest.TestCase):
    def test_actual_6180_matches_all_six(self):
        result = checker.assess(b'pypdf==6.18.0 \\\n    --hash=sha256:example\n')
        self.assertEqual(result['match_count'], 6)
        self.assertFalse(result['security_release_approved'])

    def test_6181_still_matches_three(self):
        self.assertEqual(checker.assess(b'pypdf==6.18.1\n')['match_count'], 3)

    def test_6190_does_not_automatically_grant_release(self):
        result = checker.assess(b'pypdf==6.19.0\n')
        self.assertEqual(result['match_count'], 0)
        self.assertFalse(result['security_release_approved'])
        self.assertFalse(result['full_dependency_scan'])

    def test_commented_old_pin_not_used(self):
        self.assertEqual(checker.assess(b'# pypdf==6.18.0\npypdf==6.19.0\n')['match_count'], 0)

    def test_missing_pin_errors(self):
        with self.assertRaises(ValueError): checker.assess(b'Django==5.2.17\n')

    def test_duplicate_pin_errors(self):
        with self.assertRaises(ValueError): checker.assess(b'pypdf==6.18.0\npypdf==6.19.0\n')

    def test_prerelease_never_considered_patched(self):
        with self.assertRaises(ValueError): checker.assess(b'pypdf==6.19.0rc1\n')

    def test_range_without_pin_errors(self):
        with self.assertRaises(ValueError): checker.assess(b'pypdf>=6.19.0\n')

    def test_invalid_encoding_errors(self):
        with self.assertRaises(UnicodeError): checker.assess(b'\xff\xff')

    def test_bounded_input(self):
        with self.assertRaises(ValueError): checker.assess(b'X'*2_000_001)

if __name__ == '__main__': unittest.main(verbosity=2)
