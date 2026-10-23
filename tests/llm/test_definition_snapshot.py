"""Definition capability projections preserve selected records and runtime extensions."""

import unittest

from llm.core.definition_snapshot import DefinitionSnapshot


class DefinitionSnapshotTests(unittest.TestCase):
    def test_independent_wire_and_record_projection_preserve_runtime_owner(self):
        configuration = {"config": {"host": "selected"}}
        records = {"guide": {"instruction": "original"}}
        envelope = DefinitionSnapshot(configuration, records).to_dict()
        self.assertEqual(envelope, {"configuration": configuration, "records": records})
        connector = object()
        extended = {**envelope, "connector": connector, "revision": "host-revision"}
        self.assertIs(DefinitionSnapshot.records_from(extended), records)
        self.assertIs(extended["connector"], connector)
        self.assertEqual(extended["revision"], "host-revision")

    def test_record_projection_requires_evidence_without_unrelated_fields(self):
        selected = {"guide": {"instruction": "original"}}
        self.assertIs(DefinitionSnapshot.records_from({"records": selected}), selected)
        self.assertEqual(DefinitionSnapshot.records_from({"records": {}}), {})
        with self.assertRaises(KeyError):
            DefinitionSnapshot.records_from({"configuration": {}})
        with self.assertRaises(TypeError):
            DefinitionSnapshot.records_from(None)
