"""Opt-in file validation reuse never caches grants, limits or unverified bytes."""

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from llm.components.tools.builtin import BuiltinTools
from llm.components.tools.builtin.file_text import TextRange


class FileCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_reuse_change_same_mtime_limits_and_cas(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'data'
            path.write_text('가나다 abc')
            async with BuiltinTools(root, read_cache_bytes=1000) as tools:
                tool = tools.registry.get('file_read')
                async def read(**extra):
                    return await tool.handler({'path': 'data', 'offset': 0, 'max_chars': 2, **extra})
                with patch.object(TextRange, 'read', wraps=TextRange.read) as scan:
                    original = await read()
                    self.assertEqual((await read())['text'], '가나')
                    self.assertEqual(scan.call_count, 1)
                    with self.assertRaisesRegex(ValueError, 'byte limit'):
                        await read(max_file_bytes=1)
                    with self.assertRaisesRegex(ValueError, 'changed'):
                        await read(expected_sha256='0' * 64)
                    before = path.stat()
                    path.write_text('마바사 abc')
                    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
                    self.assertEqual((await read())['text'], '마바')
                    self.assertEqual(scan.call_count, 2)
                    with self.assertRaisesRegex(ValueError, 'changed'):
                        await read(expected_sha256=original['sha256'])
                    path.write_bytes(b'\xff')
                    with self.assertRaises(UnicodeDecodeError):
                        await read()
                cache = tools.files.read_cache
            self.assertEqual(cache.size, 0)

    async def test_capacity_eviction_oversized_files_and_no_implicit_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name, text in (('a', 'abcde'), ('b', 'vwxyz'), ('large', '0123456789')):
                (root / name).write_text(text)
            async with BuiltinTools(root) as disabled:
                self.assertIsNone(disabled.files.read_cache)
            async with BuiltinTools(root, read_cache_bytes=8) as tools:
                async def read(name):
                    return await tools.registry.get('file_read').handler({'path': name, 'max_chars': 1})
                with patch.object(TextRange, 'read', wraps=TextRange.read) as scan:
                    for name in ('a', 'a', 'b', 'a', 'large', 'large'):
                        await read(name)
                    self.assertEqual(scan.call_count, 5)
                    self.assertLessEqual(tools.files.read_cache.size, 8)
            for value in (True, 0, -1, 1.5):
                with self.assertRaises(ValueError):
                    BuiltinTools(root, read_cache_bytes=value)

    async def test_cached_path_does_not_bypass_reduced_grant_or_symlink_checks(self):
        from llm.components.tools.builtin.path_access import path_scope
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'file').write_text('original')
            (root / 'allowed').mkdir()
            async with BuiltinTools(root, read_cache_bytes=1000) as tools:
                tool = tools.registry.get('file_read')
                await tool.handler({'path': 'file', 'max_chars': 1})
                with path_scope({'read_access': {'roots': [str(root / 'allowed')], 'follow_symlinks': False}}):
                    with self.assertRaises(ValueError):
                        await tool.handler({'path': 'file', 'max_chars': 1})
                (root / 'link').symlink_to(root / 'file')
                with self.assertRaises(ValueError):
                    await tool.handler({'path': 'link', 'max_chars': 1})
