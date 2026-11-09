"""Real host completion composition, cursor popup and explicit query dispatch."""
import asyncio
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from prompt_toolkit.completion import CompleteEvent, Completer, Completion, WordCompleter
from prompt_toolkit.styles import Style, merge_styles
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input

from dok import DockConfig, install, uninstall
from dok.features.completion.models import Candidate, Catalog, Registration
from dok.features.completion.runtime import Phase
from tests.dok.support import HostPrompt, Output, render, wait_running


async def until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(.005)


class CompletionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.pipe = self.enterContext(create_pipe_input())
        self.prompt = HostPrompt(self.pipe, Output(columns=110, rows=32), reserve_space_for_menu=8)
        self.dock = install(self.prompt, config_path=Path(self.directory) / "config.jsonc")
        self.dock.configure(DockConfig(audio={"enabled": False}, media={"enabled": False}, toolbar={"enabled": False},
                                      completion={"hub": False}))
        self.task = None

    async def asyncTearDown(self):
        if self.task and not self.task.done():
            self.prompt.app.exit(result="")
        if self.task:
            await self.task
        uninstall(self.prompt)
        await asyncio.sleep(0)

    async def start(self, text):
        self.task = asyncio.create_task(self.prompt.read_and_dispatch(default=text))
        await wait_running(self.prompt.app)
        await render(self.prompt.app)
        await until(lambda: not self.dock._completion.busy)

    async def test_open_close_preserves_prompt_and_freezes_cursor_anchor(self):
        await self.start("git log -h")
        original = self.prompt.default_buffer.document
        window = self.prompt.app.layout.current_window
        old_menu = window.content.menu_position
        self.assertTrue(self.dock.open_completions())
        screen = await render(self.prompt.app)
        self.assertEqual(self.prompt.default_buffer.document, original)
        anchor = self.dock._popups._anchor
        self.assertIs(anchor[0], window)
        point = screen.get_menu_position(window)
        regions = self.dock._popups.layer.mouse_regions
        self.assertTrue(regions)
        self.assertTrue(regions[0][2] > point.y or regions[-1][2] < point.y)
        self.dock._popups.page.prefix.insert_text("new")
        screen = await render(self.prompt.app)
        self.assertEqual(screen.get_menu_position(window), point)
        self.pipe.send_bytes(b"\x1bo")
        await until(lambda: not self.dock._popups.modal)
        self.assertIs(window.content.menu_position, old_menu)
        self.assertEqual(self.prompt.default_buffer.document, original)

    async def test_alt_m_manual_prefix_save_and_close_preserve_prompt(self):
        from dok.features.completion.management import CompletionManager
        await self.start('draft command')
        original = self.prompt.default_buffer.document
        self.pipe.send_bytes(b'\x1bm')
        await until(lambda: self.dock._popups.modal)
        page = self.dock._popups.page
        self.assertIsInstance(page, CompletionManager)
        self.pipe.send_text('a')
        await until(lambda: page.prefix_editor is not None)
        self.pipe.send_text('manual sub\r')
        await until(lambda: page.prefix_editor is None)
        self.assertFalse(self.dock._completion.store.path.exists())
        self.pipe.send_bytes(b'\x1bOQ')  # F2 persists the local draft.
        await until(lambda: self.dock._completion.catalog.get(('manual', 'sub')) is not None and not page.saving)
        self.assertEqual(self.dock._completion_host.catalog, self.dock._completion.store.load())
        self.pipe.send_bytes(b'\x1bm')
        await until(lambda: not self.dock._popups.modal)
        self.assertEqual(self.prompt.default_buffer.document, original)
        self.assertIsNone(self.dock._completion_host._pending)
        self.assertFalse(self.task.done())

    async def test_enter_submits_and_automatically_registers_only_the_returned_output(self):
        await self.start("tool help")
        self.dock.open_completions()
        await render(self.prompt.app)
        page = self.dock._popups.page
        page.prefix.text = "tool"
        self.assertIsNone(self.dock._completion_host._pending)
        self.pipe.send_bytes(b"\t\r")
        self.assertEqual(await asyncio.wait_for(self.task, 2), "tool help")
        shell = self.prompt.interactive_shell
        shell.accepted_prompt_id += 1
        shell.last_command, shell.scrollback.last_output = "tool help", "  --all  All records\n"
        await self.start("")
        await until(lambda: self.dock._completion.phase == Phase.IDLE)
        self.assertEqual([item.name for item in self.dock._completion.catalog.get(("tool",)).items], ["--all"])
        self.assertFalse(self.dock._popups.modal)
        self.assertTrue(self.dock._completion.store.path.exists())
        self.assertIsNone(self.dock._completion_host._pending)
        self.assertEqual(self.prompt.default_buffer.text, "")

    async def test_saved_candidates_use_memory_and_restore_existing_provider(self):
        await self.start("")
        host = self.dock._completion_host
        host.configure(False)
        self.prompt.set_completer(WordCompleter(["custom"]))
        previous = self.prompt.completer
        host.configure(True)
        host.catalog = Catalog(registrations=(Registration(("tool",), items=(Candidate("--all"),)),))
        self.dock._completion.store.load = Mock(side_effect=AssertionError("No I/O while completing"))
        results = list(self.prompt.completer.get_completions(Document("tool --a"), CompleteEvent()))
        self.assertIn("--all ", [item.text for item in results])
        self.assertIn("custom", [item.text for item in self.prompt.completer.get_completions(Document("cu"), CompleteEvent())])
        host.configure(False)
        self.assertIs(self.prompt.completer, previous)

    async def test_another_plugins_replacement_survives_dok_uninstall(self):
        await self.start("")
        self.prompt.set_completer(WordCompleter(["external"]))
        replacement = self.prompt.completer
        self.dock._completion_host.close()
        self.assertIs(self.prompt.completer, replacement)

    async def test_compact_progress_mouse_region_matches_the_visible_panel(self):
        from dok.features.completion.runtime import Progress
        await self.start("")
        self.dock._completion_progress(Progress(("git", "log"), Phase.ANALYZING, True))
        screen = await render(self.prompt.app)
        layer = self.dock._popups.layer
        self.assertEqual(layer.notification.painted_height, 9)
        self.assertEqual(len(layer.mouse_regions), layer.notification.painted_height)
        left, right, bottom = layer.mouse_regions[-1]
        edge = "".join(screen.data_buffer[bottom][x].char for x in range(left, right))
        self.assertTrue(edge.startswith("└"))
        self.assertTrue(edge.endswith("┘"))
        hint = "".join(screen.data_buffer[bottom - 1][x].char for x in range(left, right))
        self.assertIn("Alt+X", hint)
        self.assertNotIn("Alt+N", hint)
        padding = "".join(screen.data_buffer[bottom - 2][x].char for x in range(left, right))
        self.assertEqual(padding.strip('│ '), '')
        bar = "".join(screen.data_buffer[bottom - 3][x].char for x in range(left, right))
        self.assertIn("━", bar)

    async def test_existing_custom_and_native_suggestions_precede_sorted_generated_items(self):
        calls = []
        class Existing(Completer):
            def __init__(self, name, items):
                self.name, self.items = name, items
            def get_completions(self, document, complete_event):
                calls.append(self.name)
                yield from self.items
        await self.start("")
        host = self.dock._completion_host
        host.configure(False)
        self.prompt.default_completer = Existing("native", [Completion("--local ", -2)])
        self.prompt.set_completer(Existing("custom", [Completion("--z-native ", -2), Completion("--same", -2)]))
        previous = self.prompt.completer
        host.configure(True)
        host.catalog = Catalog(registrations=(Registration(("tool",), items=(
            Candidate("--beta"), Candidate("--same", help="generated"), Candidate("--alpha"))),))
        results = list(self.prompt.completer.get_completions(Document("tool --"), CompleteEvent()))
        self.assertEqual([item.text for item in results], ["--z-native ", "--same", "--local ", "--alpha ", "--beta "])
        self.assertEqual(calls, ["custom", "native"])
        host.configure(False)
        self.assertIs(self.prompt.completer, previous)

    async def test_completion_theme_updates_and_unload_preserves_an_external_style(self):
        await self.start("")
        appearance = self.dock._completion_appearance
        appearance.close()
        base = Style.from_dict({"prompt": "fg:#123456", "scrollbar.button": "bg:#654321"})
        self.prompt.style = base
        config = replace(self.dock.config, completion=replace(self.dock.config.completion,
            menu={"color": "green", "help": "cyan", "selected_bold": True}))
        self.dock.configure(config)
        style = self.prompt.style
        self.assertEqual(style.get_attrs_for_style_str("class:prompt").color, "123456")
        self.assertEqual(style.get_attrs_for_style_str("class:scrollbar.button").bgcolor, "654321")
        self.assertEqual(style.get_attrs_for_style_str("class:completion-menu.completion").color,
                         config.theme.resolve("green")[1:])
        self.assertTrue(style.get_attrs_for_style_str("class:completion-menu.meta.completion.current").bold)
        self.dock.configure(replace(config, theme=replace(config.theme, colors=replace(config.theme.colors, green="#abcdef"))))
        self.assertEqual(style.get_attrs_for_style_str("class:completion-menu.completion").color, "abcdef")
        external = merge_styles([style, Style.from_dict({"other": "bold"})])
        self.prompt.style = external
        appearance.close()
        self.assertIs(self.prompt.style, external)
        self.assertEqual(external.get_attrs_for_style_str("class:prompt").color, "123456")
        self.assertEqual(external.get_attrs_for_style_str("class:completion-menu.completion").color, "")
