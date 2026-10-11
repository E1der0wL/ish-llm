"""Real PTK input through expanded output, plus lossless table projections."""

import asyncio
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from prompt_toolkit.input import create_pipe_input

from examples.hub.preview import create_preview
from hub.model import ChatMessage
from hub.ui.output import OutputBlock, OutputObject, OutputParser, RendererRegistry
from hub.ui.output.table import TableData
from hub.widget.output_inspector import OutputInspector
from hub.widget.table_inspector import TableInspector, display_cell
from tests.hub.support import SizedOutput, eventually


class TableDataTests(unittest.TestCase):
    def test_markdown_inline_cells_numeric_sort_and_filter_preserve_source(self):
        source = '| **Name** | Value |\n| --- | --- |\n| `a` \\| b | 10 |\n| 한글 | 2 |\n'
        block = OutputParser().parse(source)[0]
        data = TableData.from_block(block)
        self.assertEqual(data.columns, ("Name", "Value"))
        self.assertEqual(data.rows, (("a | b", "10"), ("한글", "2")))
        self.assertEqual(data.select(column=1), [1, 0])
        self.assertEqual(data.select(column=1, descending=True), [0, 1])
        self.assertEqual(data.select("A |"), [0])
        self.assertEqual(block.raw, source)

    def test_invalid_and_oversized_tables_are_rejected(self):
        for data in ({"columns": [], "rows": []}, {"columns": ["a"], "rows": [[]]},
                     {"columns": ["a"], "rows": [[1]] * 501}):
            with self.assertRaises(ValueError):
                TableData.from_block(OutputBlock("hub-table", json.dumps(data), 0))
        with self.assertRaises(ValueError):
            TableData.from_block(OutputBlock("table", "x" * 262145, 0))
        self.assertLess(len(display_cell("가" * 10000)), 48)
        self.assertTrue(display_cell("가" * 10000).endswith("…"))

    def test_expanded_factory_is_explicit_and_receives_original_object(self):
        registry = RendererRegistry()
        created = []
        def factory(item, context):
            created.append((item, context))
            return "local UI"
        registry.register("hub-example", SimpleNamespace(render=lambda b, c: []), interaction=factory)
        block = OutputBlock("hub-example", "data", 0)
        item = OutputObject("id", "m", block, "demo", (), 0, 0)
        context = object()
        self.assertEqual(registry.create_interaction(item, context), "local UI")
        self.assertEqual(created, [(item, context)])


class ExpandedOutputTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.input = create_pipe_input()
        self.pipe = self.input.__enter__()
        self.size = SizedOutput()
        self.app, self.view, _ = create_preview(input=self.pipe, output=self.size)
        self.task = asyncio.create_task(self.app.run_async())
        self.pipe.send_text("\x11")
        await eventually(lambda: self.view.visible)
        self.view.composer.text = "keep draft"

    async def asyncTearDown(self):
        self.app.exit()
        await self.task
        self.view.render_worker.close()
        self.input.__exit__(None, None, None)

    async def expand(self, source):
        self.view.transcript.set_messages((ChatMessage("assistant", source, id="m"),))
        self.app.invalidate()
        await eventually(lambda: bool(self.view.transcript.control.objects))
        self.pipe.send_text("\x14\r")
        await eventually(lambda: self.view._dialog is not None)
        return self.view._dialog

    async def test_code_search_source_toggle_copy_resize_and_focus(self):
        source = '```python\n' + '\n'.join(f'print("needle {i}")' for i in range(70)) + '\n```'
        popup = await self.expand(source)
        self.assertIsInstance(popup, OutputInspector)
        await eventually(lambda: popup.output._render_key is not None)
        self.pipe.send_text("\x06needle\r")
        await eventually(lambda: len(popup.output.hits) == 70 and not popup.searching)
        self.pipe.send_text("n")
        await eventually(lambda: popup.output.hit_index == 1)
        self.pipe.send_text("N")
        await eventually(lambda: popup.output.hit_index == 0)
        self.pipe.send_text("r")
        await eventually(lambda: popup.output.raw_mode and any("```" in row for row in popup.output.lines))
        self.size.columns, self.size.rows = 48, 14
        self.app.invalidate()
        await eventually(lambda: popup.output._width < 48)
        self.assertEqual(len(popup.output.hits), 70)
        with patch("hub.widget.output_inspector.copy_text", new_callable=AsyncMock, return_value="clipboard_copied") as copied:
            self.pipe.send_text("c")
            await eventually(lambda: copied.await_count == 1)
            self.assertEqual(copied.call_args.args[0], source)
        self.pipe.send_text("\x1b")
        await eventually(lambda: self.view._dialog is None)
        self.assertIs(self.app.layout.current_control, self.view.tags.control)
        self.assertEqual(self.view.composer.text, "keep draft")
        self.assertEqual(self.view.transcript.control.search_query, "")

    async def test_table_selection_sort_filter_no_matches_raw_and_cancel_query(self):
        source = '<hub-table>{"columns":["이름","점수"],"rows":[["Alpha",10],["Beta",2],["Gamma",30]]}</hub-table>'
        ui_thread, threads = threading.get_ident(), []
        original = TableData.from_block
        def parse(block):
            threads.append(threading.get_ident())
            return original(block)
        with patch.object(TableData, "from_block", side_effect=parse):
            popup = await self.expand(source)
            self.assertIsInstance(popup, TableInspector)
            await eventually(lambda: popup.table.data is not None)
        self.assertTrue(threads)
        self.assertNotIn(ui_thread, threads)
        self.pipe.send_text("\x1b[C\r")
        await eventually(lambda: popup.table.order == [1, 0, 2])
        self.assertEqual(popup.table.column, 1)
        self.assertEqual(popup.table.row, 1)  # Original Alpha remains selected.
        await eventually(lambda: popup.table.header.horizontal_scroll == popup.table.window.horizontal_scroll)
        self.pipe.send_text("s")
        await eventually(lambda: popup.table.order == [2, 0, 1])
        self.pipe.send_text("\x06beta\r")
        await eventually(lambda: popup.table.order == [1] and not popup.searching)
        self.pipe.send_text("\x06\x01\x0bnothing")
        await eventually(lambda: popup.table.order == [])
        self.pipe.send_text("\x1b")
        await eventually(lambda: not popup.searching)
        self.assertEqual(popup.table.order, [1])
        self.pipe.send_text("r")
        await eventually(lambda: popup.raw_mode and popup.output._render_key is not None)
        self.assertIn("hub-table", "\n".join(popup.output.lines))
        self.pipe.send_text("r")
        await eventually(lambda: not popup.raw_mode)
        self.assertEqual(popup.table.order, [1])
        self.assertEqual(popup.item.raw, source)
        self.size.columns, self.size.rows = 42, 12
        self.app.invalidate()
        await eventually(lambda: popup.table.window.render_info.window_height <= 8)
        self.pipe.send_text("\x14")
        await eventually(lambda: self.view._dialog is None)
        self.assertIs(self.app.layout.current_control, self.view.tags.control)

    async def test_invalid_table_retains_source_and_closes(self):
        source = '<hub-table>{"columns":[],"rows":[]}</hub-table>'
        popup = await self.expand(source)
        await eventually(lambda: bool(popup.table.error))
        self.pipe.send_text("r")
        await eventually(lambda: popup.output._render_key is not None)
        self.assertEqual("\n".join(popup.output.lines), source)
        self.pipe.send_text("\x1b")
        await eventually(lambda: self.view._dialog is None)

    async def test_markdown_card_body_search(self):
        popup = await self.expand('<hub-card title="본문">**Hello** world\n\nHello again</hub-card>')
        self.pipe.send_text("\x06hello\r")
        await eventually(lambda: len(popup.output.hits) == 2)
        self.assertFalse(popup.output.raw_mode)
        self.pipe.send_text("\x0e")
        await eventually(lambda: popup.output.hit_index == 1)

    async def test_extension_failure_falls_back_and_custom_dispose_is_called(self):
        renderer = SimpleNamespace(render=lambda block, context: [[("", block.text)]])
        def broken(item, context):
            raise ValueError("invalid custom UI")
        self.view.output_renderers.register("hub-broken", renderer, interaction=broken)
        popup = await self.expand('<hub-broken>kept source</hub-broken>')
        self.assertIsInstance(popup, OutputInspector)
        self.assertIn("invalid custom UI", popup.notice)
        self.assertTrue(popup.output.raw_mode)
        disposed = []
        popup.dispose = lambda: disposed.append(True)
        self.pipe.send_text("\x1b")
        await eventually(lambda: self.view._dialog is None)
        self.assertEqual(disposed, [True])
        self.assertIs(self.app.layout.current_control, self.view.tags.control)
