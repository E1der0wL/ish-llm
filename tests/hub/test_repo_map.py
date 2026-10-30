"""Bounded directory mapping honors native grants, cancellation and Tool selection."""

import asyncio
from dataclasses import replace
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from hub.backend.tools.builtin import HubBuiltinTools
from hub.backend.tools.repo_map import RepoMap
from hub.backend.tools.repo_map_values import RepoMapLimits, RepoMapFormat, RepoMapArguments
from hub.backend.execution.tool_catalog import ToolCatalog, ToolTag
from hub.backend.execution.request_tools import RequestTools
from hub.backend.execution.request_profile import RequestMode
from llm.components.tools.builtin.path_access import path_scope
from llm.core.tool_contracts import ToolEffect


class RepoMapTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.builtins = HubBuiltinTools(self.root)
        self.addAsyncCleanup(self.builtins.close)
        self.mapper = RepoMap(self.builtins.toolkit, replace(RepoMapLimits(), seconds=10))

    def put(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    async def test_structure_python_declarations_and_gitignore_without_execution(self):
        self.put('.gitignore', '*.tmp\nignored/\n')
        self.put('README.md', '# Project\n')
        self.put('main.py', 'from os import path\nclass App:\n    def start(self, name="SECRET"): pass\n'
                 'raise RuntimeError("must not execute")\n')
        self.put('ignored/secret.py', 'def hidden(): pass\n')
        self.put('node_modules/dependency.py', 'def hidden(): pass\n')
        self.put('src/.gitignore', '*.py\n!keep.py\n')
        self.put('src/keep.py', 'async def kept(x): pass\n')
        self.put('src/hide.py', 'def hidden(): pass\n')
        self.put('data.tmp', 'hidden')
        self.put('src/app.ts', 'export const value = 1;')
        (self.root / 'empty').mkdir()
        self.put('signature.py', 'async def run(a, /, b, *, c, **kw): pass\n')
        result = await self.mapper.invoke({})
        self.assertIn('README.md', result['map'])
        self.assertIn('class App', result['map'])
        self.assertIn('L3: def start(self, name)', result['map'])
        self.assertIn('from os import path', result['map'])
        self.assertIn('src/app.ts', result['map'])
        self.assertIn('src/keep.py', result['map'])
        self.assertIn('empty/', result['map'])
        self.assertIn('async def run(a, /, b, *, c, **kw)', result['map'])
        for excluded in ('hidden', 'hide.py', 'data.tmp', 'SECRET', 'raise RuntimeError'):
            self.assertNotIn(excluded, result['map'])

    async def test_host_budgets_cap_large_requests_and_report_partial_results(self):
        for index in range(8):
            self.put(f'{index}.py', f'def function_{index}(value): return value\n')
        self.mapper.limits = replace(self.mapper.limits, files=2, chars=40)
        result = await self.mapper.invoke({'max_files': 1000000, 'max_chars': 1000000})
        self.assertLessEqual(result['files_scanned'], 2)
        self.assertLessEqual(len(result['map']), 40)
        self.assertTrue(result['incomplete'])
        self.assertIn(RepoMapFormat.FILES, result['omissions'])
        self.mapper.limits = replace(self.mapper.limits, files=100, entries=2)
        result = await self.mapper.invoke({})
        self.assertLessEqual(result['entries_scanned'], 2)
        self.assertIn(RepoMapFormat.ENTRIES, result['omissions'])

    async def test_unread_ignore_rules_do_not_expose_ignored_files(self):
        self.put('.gitignore', 'private.py\n')
        self.put('private.py', 'def secret(): pass\n')
        result = await self.mapper.invoke({'max_file_bytes': 2})
        self.assertIn(RepoMapFormat.IGNORE, result['omissions'])
        self.assertNotIn('private', result['map'])

    async def test_integral_char_budget_preserves_native_byte_limit_validation(self):
        self.put('.gitignore', '# local rules\n')
        self.put('main.py', 'def inspect(value): pass\n')
        values = {'max_chars': 24.0, 'max_file_bytes': 1000}
        tool, actual = self.builtins.toolkit.registry.prepare(RepoMapArguments.NAME, values)
        result = await tool.handler(actual)
        self.assertLessEqual(len(result['map']), 24)
        self.assertEqual(actual, values)
        with self.assertRaises(ValueError):
            self.builtins.toolkit.registry.prepare(RepoMapArguments.NAME, {'max_chars': 1.5})
        tool, actual = self.builtins.toolkit.registry.prepare(RepoMapArguments.NAME, {'max_file_bytes': 1000.0})
        with self.assertRaisesRegex(ValueError, 'max_file_bytes'):
            await tool.handler(actual)

    async def test_cache_invalidates_changes_and_authority_is_rechecked(self):
        path = self.put('main.py', 'def first(): pass\n')
        with patch.object(self.mapper, '_parse', wraps=self.mapper._parse) as parse:
            first = await self.mapper.invoke({})
            self.assertEqual(await self.mapper.invoke({}), first)
            self.assertEqual(parse.call_count, 1)
            path.write_text('def changed(): pass\n')
            self.assertIn('changed', (await self.mapper.invoke({}))['map'])
            self.assertEqual(parse.call_count, 2)
        with path_scope({'read_access': {'roots': [], 'follow_symlinks': False}}):
            with self.assertRaises(ValueError):
                await self.mapper.invoke({})
        path.unlink()
        self.assertNotIn('main.py', (await self.mapper.invoke({}))['map'])

    async def test_ignore_updates_focus_large_files_and_unsupported_sources(self):
        self.put('a.py', 'def first(): pass\n')
        self.put('z.py', 'def wanted(): pass\n')
        result = await self.mapper.invoke({'focus': 'z.py', 'max_chars': 35})
        self.assertTrue(result['map'].startswith('z.py\n'))
        self.put('.gitignore', 'z.py\n')
        self.assertNotIn('z.py', (await self.mapper.invoke({}))['map'])
        self.put('large.py', '# x\n' * 100)
        self.put('invalid.py', 'invalid syntax = (')
        self.mapper.limits = replace(self.mapper.limits, file_bytes=100)
        result = await self.mapper.invoke({})
        self.assertIn('large.py', result['map'])
        self.assertIn(RepoMapFormat.LARGE, result['omissions'])
        self.assertIn(RepoMapFormat.PARSE, result['omissions'])

    async def test_path_grants_links_depth_and_empty_directory(self):
        outside = self.root / 'outside'
        inside = self.root / 'inside'
        outside.mkdir()
        inside.mkdir()
        self.put('outside/private.py', 'def private(): pass\n')
        (inside / 'linked').symlink_to(outside, target_is_directory=True)
        (inside / 'cycle').symlink_to(inside, target_is_directory=True)
        result = await self.mapper.invoke({'path': str(inside)})
        self.assertNotIn('private', result['map'])
        self.assertIn(RepoMapFormat.LINKS, result['omissions'])
        with path_scope({'read_access': {'roots': [str(inside)], 'follow_symlinks': True}}):
            with self.assertRaises(ValueError):
                await self.mapper.invoke({'path': str(outside)})
        self.put('deep/a/b/c.py', 'pass')
        result = await self.mapper.invoke({'max_depth': 0})
        self.assertIn(RepoMapFormat.DEPTH, result['omissions'])
        (self.root / 'empty').mkdir()
        empty = await self.mapper.invoke({'path': 'empty'})
        self.assertEqual(empty['files_scanned'], 0)
        self.assertFalse(empty['incomplete'])

    async def test_scanning_is_off_event_loop_and_cancellation_is_drained(self):
        started, stopped = Event(), Event()
        def slow(files, arguments, stop):
            started.set()
            stop.wait(5)
            stopped.set()
        with patch.object(self.mapper, '_scan', side_effect=slow):
            pending = asyncio.create_task(self.mapper.invoke({}))
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(pending, 2)
            self.assertTrue(stopped.is_set())

    async def test_registered_read_tool_is_in_lookup_but_not_greetings(self):
        registry = self.builtins.toolkit.registry
        tool, actual = registry.prepare(RepoMapArguments.NAME, {'path': '.'})
        self.assertEqual(tool.contract.effect, ToolEffect.READ_ONLY)
        self.assertFalse(tool.contract.approval_required)
        catalog = ToolCatalog.from_registry(registry)
        self.assertIn(RepoMapArguments.NAME, RequestTools.names(catalog, RequestMode.LOOKUP))
        self.assertNotIn(RepoMapArguments.NAME, RequestTools.names(catalog, RequestMode.CONVERSATION))
        entry = next(item for item in catalog.entries if item.name == RepoMapArguments.NAME)
        self.assertIn(ToolTag.FILE, entry.tags)
        self.assertIn(RepoMapArguments.NAME, self.builtins.select_tools())
        self.assertIn('map', await tool.handler(actual))
