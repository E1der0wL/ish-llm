"""Test evidence must describe the same source on Windows and Linux."""

from pathlib import Path
import subprocess
import tempfile
import unittest

from tests.source_tree import copy_snapshot, source_hash
from tests.llm.check_contract_coverage import inspect, product_files


class SourceTreeTests(unittest.TestCase):
    def test_line_endings_preserve_evidence_but_code_changes_invalidate_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'value.py'
            path.write_bytes(b'class Owner:\n    KEY = "id"\n')
            expected = source_hash(path)
            path.write_bytes(path.read_bytes().replace(b'\n', b'\r\n'))
            self.assertEqual(source_hash(path), expected)
            path.write_bytes(path.read_bytes().replace(b'"id"', b'"other"'))
            self.assertNotEqual(source_hash(path), expected)

    def test_snapshot_includes_shared_inputs_and_git_without_changing_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root, target = Path(directory) / 'source', Path(directory) / 'snapshot'
            names = ('llm/value.py', 'hub/value.py', 'tests/source_tree.py',
                     'tests/llm/dependencies-linux.lock', 'ish.platform/pyproject.toml',
                     'ish.platform/src/host.py', 'docs/document-map.md',
                     'docs/hub/README.md', 'tests/hub/fixture.json', 'tests/README.md',
                     '.github/workflows/tests.yml', 'tests/llm/ci/bwrap.apparmor')
            excluded = ('tests/hub/reports/old.py', 'tests/llm/manual/local.py',
                        'llm/__pycache__/stale.py', 'private.key')
            for name in (*names, *excluded):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'# source\r\n')
            manifest = copy_snapshot(root, target)
            self.assertEqual(set(manifest), set(names))
            self.assertFalse((root / '.git').exists())
            self.assertTrue((target / '.git').is_dir())
            self.assertEqual(product_files(target), ['hub/value.py', 'llm/value.py'])
            for name in names:
                self.assertEqual((root / name).read_bytes(), b'# source\r\n')
                self.assertEqual((target / name).read_bytes(), b'# source\n')
                self.assertEqual(manifest[name]['sha256'], source_hash(target / name))
                self.assertNotEqual(manifest[name]['sha256'], manifest[name]['source_sha256'])

    def test_deleted_file_is_removed_from_inventory_and_old_review_is_not_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'hub/value.py'
            path.parent.mkdir()
            path.write_text('')
            subprocess.run(['git', 'init', '--quiet', str(root)], check=True)
            subprocess.run(['git', 'add', '.'], cwd=root, check=True)
            review = {'path': 'hub/value.py', 'sha256': source_hash(path), 'decisions': [],
                      'contracts': [], 'dynamic_paths_reviewed': True, 'reason': 'Empty fixture'}
            path.unlink()
            self.assertEqual(product_files(root), [])
            self.assertIn('orphan file review: hub/value.py', inspect(root, [], [review])['issues'])
