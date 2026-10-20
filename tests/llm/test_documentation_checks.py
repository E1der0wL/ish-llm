"""Mutation fixtures for the documentation gate; no Cloud paths or fixed counts."""

from pathlib import Path
import tempfile
import unittest

from tests.llm.check_docs import check


class DocumentationCheckTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "docs").mkdir()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_unicode_duplicate_explicit_and_reference_anchors(self):
        self.write("docs/a.md", "# 재개 예산\n# 재개 예산\n<a id='contract'></a>\n"
                   "[one](#재개-예산) [two](#재개-예산-1) [three](#contract)\n"
                   "[schema][target]\n[target]: <../llm/file name.md#api>\n"
                   "[external](https://example.invalid/missing)\n")
        self.write("llm/file name.md", "# API\n```python\nvalue = None\n```\n")
        result = check(self.root)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["anchors_checked"], 4)
        self.assertEqual(result["python_fences"], 1)

    def test_broken_link_anchor_syntax_and_url_fail_independently(self):
        mutations = ("[x](missing.md)", "[x](#absent)", "```python\nif\n```",
                     "```python title=example\nif\n```", "[x](https://[invalid)",
                     "[x](../../outside.md)", "```python\nx = 1")
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.write("docs/a.md", mutation)
                self.assertTrue(check(self.root)["errors"])

    def test_fences_hide_example_links_and_require_matching_length(self):
        self.write("docs/a.md", "````text\n```python\nif\n```\n[x](absent)\n````\n"
                   "~~~py\nassert 1 == 1\n~~~\n`data['a']['b']` `[x](absent)`\ndata[x][y]\n")
        result = check(self.root)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["python_fences"], 1)
        self.assertEqual(result["relative_links"], 0)

    def test_empty_repository_cannot_pass(self):
        self.assertTrue(check(self.root)["errors"])

    def test_repository_documents(self):
        result = check(Path(__file__).resolve().parents[2])
        self.assertEqual(result["errors"], [])
