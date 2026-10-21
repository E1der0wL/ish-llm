"""Individual audit classifications cannot hide changed, duplicated or unreviewed code."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tests.llm.check_architecture import collect


class ArchitectureReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = "def check(snapshot, expected_version):\n    if snapshot['version'] != expected_version:\n        raise ValueError('changed')\n"
        files = {
            'llm/reader.py': self.source,
            'llm/owner.py': "def revision(value):\n    return value['revision']\n",
            'docs/revisions.md': '# Revisions\nSnapshot version is a content CAS token, not a storage format.\n',
            'tests/llm/test_revision.py': 'import unittest\nclass RevisionTests(unittest.TestCase):\n    def test_stale(self):\n        self.assertNotEqual("before", "after")\n',
        }
        for name, text in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        finding, = collect(self.root)['findings']
        self.review = {key: finding[key] for key in ('id', 'rule')}
        self.review.update(owner=finding['symbol'], reason='This comparison is the documented content CAS check.',
            contracts=['docs/revisions.md'], tests=['tests.llm.test_revision.RevisionTests.test_stale'],
            sources={name: hashlib.sha256((self.root / name).read_bytes()).hexdigest() for name in files})
        self.baseline = self.root / 'baseline.json'
        self.baseline.write_text(json.dumps({'findings': []}))
        self.reviews = self.root / 'reviews.json'
        self.output = self.root / 'result.json'

    def check(self, reviews, expected=1, *, strict=True):
        self.reviews.write_text(json.dumps({'reviews': reviews}))
        command = [sys.executable, '-m', 'tests.llm.check_architecture', '--root', str(self.root),
                   '--baseline', str(self.baseline), '--reviews', str(self.reviews), '--output', str(self.output)]
        if strict:
            command.append('--strict')
        result = subprocess.run(command, cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return json.loads(self.output.read_text())

    def test_exact_review_keeps_raw_finding_visible_and_resolves_only_that_occurrence(self):
        value = self.check([self.review], expected=0)
        self.assertEqual(len(value['findings']), 1)
        self.assertEqual(value['reviewed'], [self.review])
        self.assertEqual(value['review_errors'], [])
        self.assertEqual((value['new'], value['stale'], value['required_outstanding']), ([], [], 0))
        self.assertEqual(len(self.check([])['new']), 1)

    def test_changed_source_owner_and_duplicate_occurrence_cannot_inherit_review(self):
        path = self.root / 'llm/reader.py'
        for source in (self.source.replace('expected_version:', "'ignored':"),
                       self.source.replace('def check(', 'def another('), self.source + self.source):
            with self.subTest(source=source):
                path.write_text(source)
                result = self.check([self.review])
                self.assertTrue(result['review_errors'])
                self.assertEqual(result['reviewed'], [])
        path.write_text(self.source)

    def test_unrelated_new_candidate_still_fails_with_valid_review(self):
        (self.root / 'llm/other.py').write_text('policy = policy or {}\n')
        value = self.check([self.review])
        self.assertEqual(value['reviewed'], [self.review])
        self.assertEqual(len(value['new']), 1)
        self.assertEqual(value['new'][0]['rule'], 'S2')

    def test_changed_dependency_contract_or_test_invalidates_review_even_without_strict(self):
        for name in ('llm/owner.py', 'docs/revisions.md', 'tests/llm/test_revision.py'):
            path = self.root / name
            original = path.read_bytes()
            with self.subTest(name=name):
                path.write_bytes(original + b'\n# Changed evidence\n')
                value = self.check([self.review], strict=False)
                self.assertTrue(value['review_errors'])
                self.assertEqual(value['reviewed'], [])
            path.write_bytes(original)

    def test_missing_reason_wrong_owner_and_missing_coverage_fail_closed(self):
        for key, value in (('reason', ''), ('owner', 'other'), ('contracts', []), ('tests', []),
                           ('tests', ['tests.llm.test_revision.RevisionTests.test_missing']), ('sources', {})):
            review = {**self.review, key: value}
            with self.subTest(key=key, value=value):
                self.assertTrue(self.check([review])['review_errors'])
        review = deepcopy(self.review)
        del review['sources']['tests/llm/test_revision.py']
        self.assertTrue(self.check([review])['review_errors'])

    def test_duplicate_review_and_review_left_in_debt_cannot_pass(self):
        self.assertTrue(self.check([self.review, self.review])['review_errors'])
        self.baseline.write_text(json.dumps({'findings': collect(self.root)['findings']}))
        value = self.check([self.review])
        self.assertEqual(value['stale'], [self.review['id']])

    def test_missing_explicit_review_file_fails_instead_of_silently_disabling_reviews(self):
        result = subprocess.run([sys.executable, '-m', 'tests.llm.check_architecture', '--root', str(self.root),
            '--baseline', str(self.baseline), '--reviews', str(self.root / 'missing.json'), '--output', str(self.output)],
            cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertTrue(json.loads(self.output.read_text())['review_errors'])

    def test_retired_api_and_pending_provider_boundary_cannot_be_approved(self):
        path = self.root / 'llm/reader.py'
        for source in ('backend.on_event(callback)\n', 'import litellm\n'):
            with self.subTest(source=source):
                path.write_text(source)
                finding = collect(self.root)['findings'][0]
                review = deepcopy(self.review)
                review.update(id=finding['id'], rule=finding['rule'], owner=finding['symbol'])
                review['sources']['llm/reader.py'] = hashlib.sha256(path.read_bytes()).hexdigest()
                self.assertTrue(self.check([review])['review_errors'])
