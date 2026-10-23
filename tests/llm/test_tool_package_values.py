"""Independent package/manifest wires and absence semantics remain the storage contract."""

from copy import deepcopy
import hashlib
import unittest

from llm.components.tools.package_values import ToolPackageSource, ToolPackageDigests, ToolPackageManifest


class ToolPackageValueTests(unittest.TestCase):
    def test_source_preserves_bytes_and_absent_empty_requirements(self):
        source = "# 한글\r\nasync def main():\r\n    return 1\r\n"
        for value in ({"source": source}, {"source": source, "requirements": ""},
                      {"source": source, "requirements": "# keep comment\r\nhttpx>=0.28,<1\r\n"}):
            before = deepcopy(value)
            record = ToolPackageSource.read(value)
            self.assertEqual(record.to_dict(), before)
            self.assertEqual(record.source.encode("utf-8"), source.encode("utf-8"))
            self.assertEqual(value, before)
            projected = record.to_dict()
            projected["source"] = "changed"
            self.assertEqual(record.to_dict(), before)
        for invalid in ({}, {"source": source, "requirements": None}, {"source": " "},
                        {"source": 1}, {"source": source, "unknown": "value"}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                ToolPackageSource.read(invalid)

    def test_manifest_distinguishes_absent_file_from_explicit_empty_file(self):
        source = "async def main():\n    return 1\n"
        absent, empty = ToolPackageSource(source), ToolPackageSource(source, "")
        expected_source = hashlib.sha256(source.encode("utf-8")).hexdigest()
        self.assertEqual(absent.fingerprints().to_dict(), {"source": expected_source, "requirements": None})
        self.assertEqual(empty.fingerprints().to_dict(),
                         {"source": expected_source, "requirements": hashlib.sha256(b"").hexdigest()})
        self.assertNotEqual(absent.fingerprints(), empty.fingerprints())
        for missing in ("source", "requirements"):
            value = absent.fingerprints().to_dict()
            value.pop(missing)
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                ToolPackageDigests.read(value)

    def test_manifest_closed_header_and_detached_changes(self):
        source = ToolPackageSource("async def main():\n    return 1\n")
        empty = ToolPackageManifest.create("project", "tools")
        self.assertEqual(empty.to_dict(), {"schema_version": 1, "project_id": "project", "component": "tools", "packages": {}})
        populated = empty.with_package("run", source)
        self.assertEqual(empty.packages, {})
        expected = {"schema_version": 1, "project_id": "project", "component": "tools",
                    "packages": {"run": source.fingerprints().to_dict()}}
        self.assertEqual(populated.to_dict(), expected)
        loaded = ToolPackageManifest.read(expected, project_id="project", component="tools")
        expected["packages"].clear()
        self.assertEqual(tuple(loaded.packages), ("run",))
        self.assertEqual(loaded.without_package("run").to_dict(), empty.to_dict())
        self.assertEqual(tuple(loaded.packages), ("run",))
        for field in expected:
            with self.subTest(missing=field), self.assertRaises(ValueError):
                ToolPackageManifest.read({k: v for k, v in expected.items() if k != field}, project_id="project", component="tools")
        for changes in ({"schema_version": True}, {"schema_version": 2}, {"project_id": "other"},
                        {"component": "other"}, {"packages": []}, {"unknown": True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                ToolPackageManifest.read({**expected, **changes}, project_id="project", component="tools")
