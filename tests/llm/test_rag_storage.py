"""A missing corpus pointer is corruption, never permission to erase documents."""

import hashlib
import json
from unittest.mock import patch
import unittest

from tests.llm import test_rag_components


class RAGStorageTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_rag_components.RAGTests.asyncSetUp
    add = test_rag_components.RAGTests.add

    def snapshot_files(self):
        root = self.project.paths.root / "rag"
        return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in root.rglob("*") if path.is_file()}

    async def test_missing_active_pointer_blocks_cleanup_and_preserves_documents(self):
        await self.add(identifier="original")
        root = self.project.paths.root / "rag"
        plan = await self.rag.acompaction_plan()
        async def compact():
            await self.rag.acompact([], expected_version=plan["version"])
        (root / "active.json").unlink()
        before = self.snapshot_files()
        rejected = []
        for operation in (self.rag.alist_documents, compact):
            try:
                await operation()
            except (OSError, ValueError):
                rejected.append(True)
            else:
                rejected.append(False)
        # Observe cleanup's real filesystem effect before asserting rejection.
        self.assertEqual({
            "rejected": rejected, "originals_preserved": self.snapshot_files() == before,
            "pointer_recreated": (root / "active.json").exists(),
        }, {"rejected": [True, True], "originals_preserved": True, "pointer_recreated": False})

    async def test_missing_identity_never_reissues_authority_during_read(self):
        await self.add(identifier="original")
        root = self.project.paths.root / "rag"
        (root / "identity.json").unlink()
        before = self.snapshot_files()
        rejected = False
        try:
            await self.rag.alist_documents()
        except (OSError, ValueError):
            rejected = True
        self.assertEqual((rejected, self.snapshot_files() == before,
                          (root / "identity.json").exists()), (True, True, False))

    async def test_explicit_empty_creation_and_reselection_preserve_identity(self):
        root = self.project.paths.root / "rag"
        self.assertEqual(json.loads((root / "active.json").read_text()), {"generation": None})
        identity = (root / "identity.json").read_bytes()
        self.assertEqual(await self.rag.alist_documents(), [])
        plan = await self.rag.acompaction_plan()
        await self.rag.acompact([], expected_version=plan["version"])
        await self.project.components.aselect(["rag"])
        self.assertEqual((root / "identity.json").read_bytes(), identity)
        self.assertEqual(await self.rag.alist_documents(), [])

    async def test_corrupt_metadata_blocks_read_write_backup_and_reinitialization(self):
        await self.add(identifier="original")
        root = self.project.paths.root / "rag"
        plan = await self.rag.acompaction_plan()
        async def compact():
            await self.rag.acompact([], expected_version=plan["version"])
        originals = {name: (root / name).read_bytes() for name in ("active.json", "identity.json")}
        cases = [
            ("active.json", None), ("active.json", {}), ("active.json", {"generation": None}),
            ("active.json", {"generation": False}), ("active.json", {"generation": "../outside"}),
            ("active.json", {"generation": "f" * 32}),
            ("identity.json", None), ("identity.json", {}), ("identity.json", {"id": False}),
            ("identity.json", {"id": "not-an-identity"}),
        ]
        for name, value in cases:
            with self.subTest(name=name, value=value):
                path = root / name
                if value is None:
                    path.unlink()
                else:
                    path.write_text(json.dumps(value))
                before = self.snapshot_files()
                async def add():
                    await self.add(identifier="unexpected")
                async def backup():
                    await self.project.abackup(self.root / "invalid-backup")
                async def reselect():
                    await self.project.components.aselect(["rag"])
                with patch.object(self.embed, "embed", side_effect=AssertionError("No model call on corrupt corpus")):
                    for operation in (self.rag.alist_documents, compact, add, backup, reselect):
                        with self.subTest(operation=operation.__name__), self.assertRaises((OSError, ValueError)):
                            await operation()
                        self.assertEqual(self.snapshot_files(), before)
                self.assertFalse((self.root / "invalid-backup").exists())
                path.write_bytes(originals[name])
        self.assertEqual((await self.rag.aget_document("original"))["revision"], 1)

    async def test_initialization_failure_does_not_publish_partial_component(self):
        from llm.services.infrastructure.storage import atomic_json
        await self.project.components.aremove("rag", permanent=True)
        def fail(path, value):
            atomic_json(path, value)
            if path.name == "active.json":
                raise OSError("initial pointer publication failed")
        with patch("llm.components.rag.corpus_storage.atomic_json", side_effect=fail):
            with self.assertRaisesRegex(OSError, "initial pointer"):
                await self.project.components.aselect(["rag"])
        self.assertFalse((self.project.paths.root / "rag").exists())
        await self.project.components.aselect(["rag"])
        self.assertEqual(await self.rag.alist_documents(), [])

    async def test_invalid_corpus_version_blocks_cleanup_without_coercion(self):
        await self.add(identifier="original")
        root = self.project.paths.root / "rag"
        generation = json.loads((root / "active.json").read_text())["generation"]
        path = root / "generations" / generation / "corpus.json"
        original = json.loads(path.read_text())
        plan = await self.rag.acompaction_plan()
        for version in (None, True, 1.0, 2):
            with self.subTest(version=version):
                value = {**original, "graph_schema_version": version}
                if version is None:
                    del value["graph_schema_version"]
                path.write_text(json.dumps(value))
                before = self.snapshot_files()
                with self.assertRaisesRegex(ValueError, "Unsupported RAG graph schema"):
                    await self.rag.acompact([], expected_version=plan["version"])
                self.assertEqual(self.snapshot_files(), before)


if __name__ == "__main__":
    unittest.main()
