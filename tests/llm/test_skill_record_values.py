"""Skill projections preserve omission, revision inputs and reference-only authority."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
import unittest

from llm.components.skills.records import SkillRecord, SkillResource, SkillSnapshot
from llm.services.infrastructure.storage import revision_token
from tests.llm.support.skills import skill_markdown


class SkillRecordValueTests(unittest.TestCase):
    def test_detached_record_preserves_omission_and_raw_definition_revision(self):
        source = {"markdown": skill_markdown("guide", "Read exact source"),
                  "resources": [{"uri": "https://example.org/manual", "metadata": {"labels": ["original"]}}]}
        expected = deepcopy(source)
        record = SkillRecord.read(source, identifier="guide")
        snapshot = SkillSnapshot("guide", source)
        self.assertEqual(record.to_dict(), expected)
        self.assertEqual(snapshot.revision, revision_token(expected))
        self.assertNotIn("files", record.to_dict())
        self.assertFalse(record.same_files(SkillRecord.read({**expected, "files": {}})))
        source["resources"][0]["metadata"]["labels"].append("changed")
        record.resources[0].to_dict()["metadata"]["labels"].append("other")
        record.parsed_document.frontmatter["name"] = "changed"
        self.assertEqual(record.to_dict(), expected)
        self.assertEqual(snapshot.to_dict(), expected)
        self.assertEqual(snapshot.name, "guide")
        self.assertEqual(record.parsed_document.name, "guide")

    def test_resource_variants_keep_explicit_evidence_and_closed_shape(self):
        variants = [
            ({"uri": "https://example.org/manual"}, False, False, False, None),
            ({"path": "reference.txt"}, False, False, False, "reference.txt"),
            ({"component": "data", "identifier": "manual", "revision": "a" * 64, "path": "reference.txt"}, True, True, False, "reference.txt"),
            ({"component": "tools", "identifier": "helper", "revision": "a" * 64}, True, False, True, None),
        ]
        for wire, external, data, tool, path in variants:
            value = SkillResource.read(wire)
            self.assertEqual(value.to_dict(), wire)
            self.assertEqual((value.external, value.data_reference, value.tool_reference, value.path),
                             (external, data, tool, path))
        for invalid in ({}, {"uri": None}, {"path": "x", "uri": "https://example.org"},
                        {"component": "tools", "identifier": "helper"},
                        {"component": "tools", "identifier": "helper", "revision": "a" * 64, "authorize": True}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                SkillResource.read(invalid)

    def test_lineage_and_selection_are_owner_projections_without_default_evidence(self):
        record = SkillRecord.read({"markdown": skill_markdown("child", "Child"), "resources": [{"uri": "reference"}]})
        self.assertIsNone(record.lineage)
        child = SkillRecord.read(record.with_lineage("parent", "b" * 64))
        self.assertEqual(child.lineage.to_dict(), {"parent": "parent", "parent_revision": "b" * 64})
        self.assertNotIn("lineage", record.to_dict())
        with self.assertRaises(FrozenInstanceError):
            child.lineage.parent = "other"
        self.assertEqual(record.resource(0).to_dict(), {"uri": "reference"})
        for index in (-1, 1, True, None, 0.0):
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, "Invalid Skill resource index"):
                record.resource(index)
        for field in ("markdown", "files", "resources", "lineage", "source"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                SkillRecord.read({**record.to_dict(), field: None})
