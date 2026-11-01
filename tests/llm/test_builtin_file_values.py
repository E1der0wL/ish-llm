"""File declaration consumption preserves independent wire and real failure effects."""

from copy import deepcopy
import hashlib
import os
import errno
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from llm.components.tools.builtin.files import FileTools
from llm.components.tools.builtin.file_values import FileReadArguments, FileSearchArguments, FileTrashIdentifier
from llm.core.configuration import UNSET


class BuiltinFileValuesTests(unittest.TestCase):
    def test_move_and_restore_preserve_concurrent_destination(self):
        for operation in ("move", "restore"):
            for link in (False, True):
                with self.subTest(operation=operation, link=link), TemporaryDirectory() as root:
                    root = Path(root)
                    tools = FileTools(root)
                    source = root / "original"
                    source.write_bytes(b"source bytes")
                    expected = hashlib.sha256(source.read_bytes()).hexdigest()
                    if operation == "restore":
                        receipt = tools.file_delete({"path": "original", "expected_sha256": expected})
                        source = tools.trash_path(receipt["trash_id"])
                    target = root / "target"
                    victim = root / "victim"
                    victim.write_bytes(b"external bytes")
                    original = FileTools._rename_exclusive

                    def competing(parent, name, destination, following):
                        # A separate process creates the destination after path
                        # admission, immediately before the real atomic syscall.
                        subprocess.run([sys.executable, "-I", "-c",
                            "import pathlib,sys; p=pathlib.Path(sys.argv[1]); "
                            + ("p.symlink_to(sys.argv[2])" if link else "p.write_bytes(b'external bytes')"),
                            str(target), str(victim)], check=True)
                        return original(parent, name, destination, following)

                    with patch.object(FileTools, "_rename_exclusive", staticmethod(competing)):
                        with self.assertRaises(FileExistsError):
                            if operation == "move":
                                tools.file_move({"path": "original", "destination": "target", "expected_sha256": expected})
                            else:
                                tools.file_restore({"path": "target", "trash_id": receipt["trash_id"]})
                    self.assertEqual(source.read_bytes(), b"source bytes")
                    self.assertEqual(target.read_bytes(), b"external bytes")
                    self.assertEqual(target.is_symlink(), link)
                    self.assertEqual(victim.read_bytes(), b"external bytes")

    def test_move_unsupported_and_post_move_sync_failure_do_not_fall_back(self):
        with TemporaryDirectory() as root:
            tools = FileTools(Path(root))
            source, target = Path(root, "source"), Path(root, "target")
            source.write_bytes(b"source")
            args = {"path": "source", "destination": "target", "expected_sha256": hashlib.sha256(b"source").hexdigest()}
            with patch("ctypes.CDLL", return_value=SimpleNamespace()):
                with self.assertRaises(OSError) as caught:
                    tools.file_move(args)
            self.assertEqual(caught.exception.errno, errno.ENOSYS)
            self.assertEqual(source.read_bytes(), b"source")
            self.assertFalse(target.exists())
            with patch("llm.components.tools.builtin.files.os.fsync", side_effect=OSError("sync failed")):
                with self.assertRaisesRegex(OSError, "sync failed"):
                    tools.file_move(args)
            self.assertFalse(source.exists())
            self.assertEqual(target.read_bytes(), b"source")

    def test_read_presence_original_input_and_independent_wire(self):
        with TemporaryDirectory() as root:
            tools = FileTools(Path(root))
            Path(root, "sample").write_bytes(b"one\ntwo\n")
            args = {"path": "sample", "max_lines": 1}
            original = deepcopy(args)
            read = FileReadArguments.read(args)
            self.assertIs(read.start_line, UNSET)
            self.assertIs(read.tail_lines, UNSET)
            self.assertEqual(tools.file_read(args), {"path": "sample", "text": "one\n",
                "sha256": hashlib.sha256(b"one\ntwo\n").hexdigest(), "total_lines": 2,
                "start_line": 1, "next_line": 2, "truncated": True})
            self.assertEqual(args, original)
            self.assertIsNone(FileReadArguments.read({"path": "sample", "start_line": None}).start_line)
            with self.assertRaisesRegex(ValueError, "cannot be combined"):
                tools.file_read({"path": "sample", "tail_lines": 1, "start_line": 1})
            self.assertEqual(FileReadArguments.schema()["required"], ["path"])
            self.assertEqual(FileSearchArguments.schema()["required"], ["query", "case_sensitive", "recursive"])

    def test_patch_commit_failure_preserves_original_bytes_and_mode(self):
        with TemporaryDirectory() as root:
            tools = FileTools(Path(root));target=Path(root, "sample")
            target.write_bytes(b"original\n");target.chmod(0o640)
            before=target.read_bytes();mode=target.stat().st_mode
            args={"path": "sample", "expected_sha256": hashlib.sha256(before).hexdigest(),
                  "old_text": "original", "new_text": "changed"}
            with patch("llm.components.tools.builtin.files.os.replace", side_effect=OSError("commit interrupted")):
                with self.assertRaisesRegex(OSError, "commit interrupted"):
                    tools.file_patch(args)
            self.assertEqual(target.read_bytes(), before)
            self.assertEqual(target.stat().st_mode, mode)
            self.assertEqual([p.name for p in Path(root).iterdir()], ["sample"])

    def test_shared_filter_declaration_drives_typed_search_and_listing(self):
        with TemporaryDirectory() as root:
            tools=FileTools(Path(root));Path(root, "a.txt").write_bytes(b"match\nother\nmatch\n")
            Path(root, "ignore.log").write_bytes(b"match\n")
            args={"query": "MATCH", "case_sensitive": False, "recursive": True,
                  "include": ["*.txt"], "limit": 1, "context_lines": 1}
            original=deepcopy(args);found=tools.file_search(args)
            self.assertEqual(found, {"matches": [{"path": "a.txt", "line": 1, "text": "match",
                "sha256": hashlib.sha256(b"match\nother\nmatch\n").hexdigest(), "context_start_line": 1,
                "context": ["match", "other"]}], "skipped": 0, "scanned": 1, "truncated": True, "next_offset": 1})
            self.assertEqual(args, original)
            listed=tools.file_list({"recursive": True, "include": ["*.txt"]})
            self.assertEqual(listed, {"entries": [{"path": "a.txt", "type": "file"}], "truncated": False})
            schema=FileSearchArguments.schema();schema["properties"]["include"]["minItems"]=0
            self.assertEqual(FileSearchArguments.schema()["properties"]["include"]["minItems"],1)

    def test_trash_identity_and_explicit_byte_limit_reject_before_mutation(self):
        with TemporaryDirectory() as root:
            tools=FileTools(Path(root));Path(root, "original").write_bytes(b"keep")
            for identifier in ("", "a"*31, "a"*33, "A"*32, "../"+"a"*29, "a"*31+"\n"):
                with self.subTest(identifier=identifier), self.assertRaisesRegex(ValueError, "Invalid trash ID"):
                    tools.file_restore({"path": "restored", "trash_id": identifier})
            FileTrashIdentifier.validate("a"*32)
            for maximum in (True, 0, -1):
                with self.subTest(maximum=maximum), self.assertRaises(ValueError):
                    tools.for_arguments({"max_file_bytes": maximum})
            limited=tools.for_arguments({"max_file_bytes": 1})
            with self.assertRaisesRegex(ValueError, "byte limit"):
                limited.file_create({"path": "new", "content": "too big"})
            self.assertEqual(Path(root, "original").read_bytes(), b"keep")
            self.assertEqual([p.name for p in Path(root).iterdir()], ["original"])
