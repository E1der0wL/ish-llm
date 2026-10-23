"""Whole-audit evidence must not treat narrow static success as completion."""

import tempfile
from pathlib import Path
import unittest

from tests.llm.check_contract_coverage import candidates, inspect


class ContractCoverageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'llm').mkdir()
        (self.root / 'llm/value.py').write_text('class Owner:\n    KEY = "id"\n')
        self.paths = ['llm/value.py']

    def review(self):
        value = inspect(self.root, self.paths, [])['files'][0]
        return {'path': value['path'], 'sha256': value['sha256'],
                'reason': 'Owner declaration and every consumer inspected', 'dynamic_paths_reviewed': True,
                'contracts': [{'owner': 'Owner', 'declaration': 'Owner.KEY', 'consumers': ['consumer'],
                               'tests': ['independent wire and failure tests'], 'complete': True}],
                'decisions': [{'id': entry['id'], 'classification': 'declaration', 'owner': 'Owner',
                               'reason': 'Canonical class declaration'} for entry in value['candidates']]}

    def test_every_literal_role_is_a_candidate_even_single_use_and_false_null(self):
        source = '''def work(value, limit=1):
    result = {"title": value["title"]}
    value.update(enabled=False)
    if value.get("status", None) == "ready":
        return getattr(value, "field")
    match value:
        case None: return "empty"
'''
        values = candidates(source)
        self.assertEqual(len(values), 10)
        self.assertTrue(all(item['area'] == 'implementation' for item in values))
        self.assertTrue({'Dict', 'Subscript', 'Call', 'Compare', 'arguments', 'Match'} <= {item['role'] for item in values})

    def test_no_reviews_or_no_python_literals_cannot_prove_completion(self):
        (self.root / 'llm/asset.txt').write_text('external protocol identifier')
        self.paths.append('llm/asset.txt')
        report = inspect(self.root, self.paths, [])
        self.assertFalse(report['complete'])
        self.assertEqual(report['counts']['unreviewed_files'], 2)
        self.assertEqual(report['counts']['unreviewed_dynamic_paths'], 2)
        self.assertFalse(inspect(self.root, [], [])['complete'])

    def test_new_changed_deleted_files_invalidate_prior_evidence(self):
        review = self.review()
        self.assertTrue(inspect(self.root, self.paths, [review])['complete'])
        (self.root / 'llm/new.py').write_text('')
        self.assertFalse(inspect(self.root, [*self.paths, 'llm/new.py'], [review])['complete'])
        (self.root / 'llm/value.py').write_text('class Owner:\n    KEY = "other"\n')
        self.assertEqual(inspect(self.root, self.paths, [review])['counts']['stale_files'], 1)
        (self.root / 'llm/value.py').unlink()
        self.assertEqual(inspect(self.root, self.paths, [review])['counts']['unreadable_files'], 1)

    def test_missing_duplicate_orphan_and_open_contracts_block_completion(self):
        original = self.review()
        for change in ('missing', 'duplicate', 'orphan', 'contract', 'dynamic', 'violation'):
            from copy import deepcopy
            review = deepcopy(original)
            if change == 'missing': review['decisions'].clear()
            if change == 'duplicate': review['decisions'].append(deepcopy(review['decisions'][0]))
            if change == 'orphan': review['decisions'][0]['id'] = 'unknown'
            if change == 'contract': review['contracts'][0]['complete'] = False
            if change == 'dynamic': review['dynamic_paths_reviewed'] = False
            if change == 'violation': review['decisions'][0]['classification'] = 'violation'
            with self.subTest(change=change):
                self.assertFalse(inspect(self.root, self.paths, [review])['complete'])
        self.assertFalse(inspect(self.root, self.paths, [original, original])['complete'])

    def test_function_literal_cannot_be_approved_as_a_declaration(self):
        (self.root / 'llm/value.py').write_text('def value():\n    return "id"\n')
        report = inspect(self.root, self.paths, [self.review()])
        self.assertFalse(report['complete'])
        self.assertEqual(report['counts']['issues'], 1)
        self.assertEqual(report['counts']['unclassified'], 1)

    def test_late_declarations_and_lambda_bodies_are_not_declaration_areas(self):
        for source in ('def value(): pass\nLATE = "id"\n',
                       'class Owner:\n    def value(self): pass\n    LATE = "id"\n',
                       'FACTORY = lambda: "id"\n'):
            with self.subTest(source=source):
                self.assertEqual(candidates(source)[0]['area'], 'implementation')

    def test_recorded_inventory_covers_current_product_files_and_bytes(self):
        import hashlib
        import json
        from tests.llm.check_contract_coverage import product_files
        root = Path(__file__).resolve().parents[2]
        index = json.loads((root / 'docs/llm/contract-coverage-index.json').read_text())
        files = {item['path']: item for item in index['files']}
        self.assertEqual(len(files), len(index['files']))
        self.assertEqual(sorted(files), product_files(root))
        for path, item in files.items():
            with self.subTest(path=path):
                self.assertEqual(hashlib.sha256((root / path).read_bytes()).hexdigest(), item['sha256'])

    def test_recorded_reviews_are_current_and_match_inventory_without_approving_remaining_files(self):
        import json
        from tests.llm.check_contract_coverage import product_files
        root = Path(__file__).resolve().parents[2]
        reviews = json.loads((root / 'docs/llm/contract-coverage-reviews.json').read_text())
        recorded = json.loads((root / 'docs/llm/contract-coverage-index.json').read_text())
        actual = inspect(root, product_files(root), reviews)
        self.assertEqual(actual['issues'], [])
        self.assertEqual(actual['counts']['stale_files'], 0)
        self.assertEqual(actual['counts'], recorded['counts'])
        self.assertEqual(actual['complete'], recorded['complete'])
        self.assertEqual([(item['path'], item['status']) for item in actual['files']],
                         [(item['path'], item['status']) for item in recorded['files']])
