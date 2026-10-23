"""Operational containers remain mandatory; typed reads do not repair lost evidence."""

from copy import deepcopy
import hashlib
import unittest

from llm.components.skills.records import SkillRecord
from llm.components.skills.state import SkillStateIdentity, SkillCatalog, SkillOperationalRecord
from tests.llm.support.skills import skill_markdown


class SkillStateValueTests(unittest.TestCase):
    def setUp(self):
        self.identity = SkillStateIdentity(1, "a" * 32, "skills")
        self.header = {"schema_version": 1, "project_id": "a" * 32, "component": "skills"}
        self.document = {"sha256": hashlib.sha256(b"document").hexdigest(), "bytes": 8, "mode": 0o600}
        self.entry = {**self.header, "id": "guide", "document": self.document, "directories": [],
                      "files": {}, "resources": [], "lineage": None, "source": None}

    def test_exact_wire_and_detached_catalog_and_entry(self):
        wire = {**self.header, "records": {"guide": "b" * 64}}
        catalog = SkillCatalog.read(wire, identity=self.identity)
        entry = SkillOperationalRecord.read(self.entry, identity=self.identity, identifier="guide")
        self.assertEqual(catalog.to_dict(), wire)
        self.assertEqual(entry.to_dict(), self.entry)
        wire["records"].clear()
        self.entry["resources"].append({"uri": "changed"})
        self.assertEqual(catalog.records, {"guide": "b" * 64})
        self.assertEqual(entry.resources, [])
        self.assertEqual(SkillCatalog.empty(self.identity).to_dict(), {**self.header, "records": {}})
        entry.to_dict()["document"]["bytes"] = 0
        self.assertEqual(entry.document["bytes"], 8)

    def test_every_required_field_and_identity_mismatch_fails_without_defaults(self):
        for kind, wire in ((SkillCatalog, {**self.header, "records": {}}), (SkillOperationalRecord, self.entry)):
            kwargs = {"identity": self.identity, **({"identifier": "guide"} if kind is SkillOperationalRecord else {})}
            for missing in wire:
                broken = deepcopy(wire)
                del broken[missing]
                with self.subTest(kind=kind, missing=missing), self.assertRaises(ValueError):
                    kind.read(broken, **kwargs)
            for changed in ({"schema_version": True}, {"schema_version": 2},
                            {"project_id": "b" * 32}, {"component": "data"}):
                with self.subTest(changed=changed), self.assertRaises(ValueError):
                    kind.read({**wire, **changed}, **kwargs)

    def test_invalid_operational_containers_and_digests_are_not_empty_stores(self):
        for records in (None, [], {"guide": "b" * 63}, {"guide": "G" * 64}, {"../guide": "b" * 64}):
            with self.subTest(records=records), self.assertRaises(ValueError):
                SkillCatalog.read({**self.header, "records": records}, identity=self.identity)
        for changes in ({"id": "other"}, {"directories": None}, {"directories": ["b", "a"]},
                        {"directories": ["a", "a"]}, {"directories": ["../outside"]},
                        {"document": {"sha256": "b" * 64}}, {"files": None}, {"resources": None}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                SkillOperationalRecord.read({**self.entry, **changes}, identity=self.identity, identifier="guide")

    def test_projection_omits_optional_empty_values_only_after_required_state_read(self):
        entry = SkillOperationalRecord.read(self.entry, identity=self.identity, identifier="guide")
        markdown = skill_markdown("guide", "Original body")
        record = SkillRecord.from_storage(markdown, files=entry.files, resources=entry.resources,
            lineage=entry.lineage, source=entry.source, identifier=entry.id)
        self.assertEqual(record.to_dict(), {"markdown": markdown})
        self.assertEqual(record.with_files(record.to_dict(), {}), {"markdown": markdown, "files": {}})
        self.assertEqual(record.without_files({"markdown": markdown, "files": {}}), {"markdown": markdown})
