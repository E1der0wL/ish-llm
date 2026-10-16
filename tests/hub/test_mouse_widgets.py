"""Real SGR mouse input must preserve Hub's keyboard and focus contracts."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.layout import HSplit

from examples.hub.preview import install_preview
from hub.model import ChatMessage
from hub.widget.controls import Button, TextArea, RadioList, ScrollablePane
from hub.widget.dialog import Dialog
from hub.widget.mouse import ScrollbarGeometry
from hub.widget.focus_region import grayscale
from hub.widget.output.reader import ReadOnlyDialog
from tests.hub.support import auxiliary, SizedOutput, eventually, minimize
from tests.hub.test_ish_integration import Prompt


class MouseWidgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_custom_background_reaches_checklist_tracks_and_inactive_headings(self):
        from dataclasses import replace
        from hub.widget.controls import CheckboxList
        with set_app(self.app):
            self.view.set_theme(replace(self.view.theme, background="#123456"))
        await self.paint()
        screen = self.app.renderer._last_screen
        attrs = self.app.renderer._attrs_for_style
        headings = [cell for row in screen.data_buffer.values() for cell in row.values()
                    if "class:hub.sidebar-heading" in cell.style and cell.char.strip()]
        self.assertTrue(headings)
        self.assertTrue(all(attrs[cell.style].bgcolor == grayscale("123456")[1:] for cell in headings))
        with set_app(self.app):
            choices = CheckboxList([(i, str(i)) for i in range(20)])
            choices.window.height = 4
            self.view.open_dialog("Check", choices, focus=choices)
        await self.paint()
        rect = self.position(choices.window)
        screen = self.app.renderer._last_screen
        for y in range(rect.ypos, rect.ypos + rect.height):
            self.assertEqual(attrs[screen.data_buffer[y][rect.xpos].style].bgcolor, "123456")
        for selector in ("hub.scrollbar-track", "hub.scrollbar-arrow", "checkbox-list"):
            self.assertEqual(self.view.style.get_attrs_for_style_str("class:hub class:" + selector).bgcolor, "123456")

    async def test_tagbar_click_selects_and_keyboard_opens_code_viewer(self):
        from hub.widget.output.code_viewer import CodeViewer
        self.view.output.transcript.set_messages((ChatMessage("assistant", "```python\nprint(1)\n```", id="code"),))
        await self.paint()
        await eventually(lambda: bool(self.view.output.transcript.control.objects))
        await auxiliary(self.app, self.pipe, self.view, 'tags')
        await eventually(lambda: self.view.output.tags.visible)
        await self.paint()
        for window in (self.view.composer.window, self.view.output.transcript.window):
            await self.mouse(0, *self.at(window))
            await self.mouse(0, *self.at(window), release=True)
            self.assertIs(self.app.layout.current_control, self.view.output.tags.control)
        tag = next(w for w, _ in self.view.mouse.windows if w.content is self.view.output.tags.control)
        await self.mouse(0, *self.at(tag))
        await self.mouse(0, *self.at(tag), release=True)
        self.assertIsNone(self.view._dialog)
        self.assertEqual(self.view.output.tags.selected, self.view.output.transcript.control.objects[0].id)
        self.assertIs(self.app.layout.current_control, self.view.output.tags.control)
        for key in ("\t", " ", "\r"):
            self.pipe.send_text(key)
            await eventually(lambda: self.view._dialog is not None)
            self.assertIsInstance(self.view._dialog, CodeViewer)
            self.assertEqual(self.view._dialog.code_text(), "print(1)\n")
            self.pipe.send_text("\x1b")
            await eventually(lambda: self.view._dialog is None)

        collapse = self.view.output.tags.collapse.window
        await self.paint()
        await self.mouse(0, *self.at(collapse))
        await self.mouse(0, *self.at(collapse), release=True)
        self.assertFalse(self.view.output.tags.visible)
        self.assertIs(self.app.layout.current_control, self.view.composer.control)

    async def asyncSetUp(self):
        self.pipe = self.enterContext(create_pipe_input())
        self.output = SizedOutput()
        self.prompt = Prompt(input=self.pipe, output=self.output)
        self.installation = install_preview(self.prompt)
        self.app, self.view = self.prompt.app, self.installation.view
        self.view.output.transcript.set_messages((ChatMessage("assistant", "\n\n".join(f"Line {i}" for i in range(200))),))
        self.task = asyncio.create_task(self.prompt.prompt_async(pre_run=lambda: self.view.show(self.app)))
        await eventually(lambda: self.app.is_running and self.view.output.transcript.window.render_info is not None
                         and self.view.output.transcript.window.render_info.content_height > 100)
        self.view.output.transcript.control.scroll("home")
        await self.paint()

    async def asyncTearDown(self):
        if not self.task.done():
            self.app.exit(result="")
        await self.task
        self.installation.close()

    async def paint(self):
        painted = asyncio.Event()
        def done(app):
            painted.set()
        self.app.after_render += done
        try:
            self.app.invalidate()
            await asyncio.wait_for(painted.wait(), 3)
        finally:
            self.app.after_render -= done

    def position(self, window):
        return self.app.renderer._last_screen.visible_windows_to_write_positions[window]

    def at(self, window):
        rect = self.position(window)
        return rect.xpos + 1, rect.ypos

    async def mouse(self, code, x, y, *, release=False):
        self.pipe.send_text(f"\x1b[<{code};{x + 1};{y + 1}{'m' if release else 'M'}")
        await asyncio.sleep(.04)
        await self.paint()

    async def test_click_focus_and_hover_wheel_preserve_focusability(self):
        app, view = self.app, self.view
        selected = view.selected
        sidebar = next(window for window, _ in view.mouse.windows if window.content is view._session_control)
        await self.mouse(35, *self.at(sidebar))
        self.assertIs(app.layout.current_control, view.composer.control)
        await self.mouse(0, *self.at(sidebar))
        await self.mouse(0, *self.at(sidebar), release=True)
        self.assertIs(app.layout.current_control, view._session_control)
        self.assertEqual(view.selected, selected)
        await self.mouse(35, *self.at(view.composer.window))
        self.assertIs(app.layout.current_control, view._session_control)
        await self.mouse(0, *self.at(view.composer.window))
        await self.mouse(0, *self.at(view.composer.window), release=True)
        self.assertIs(app.layout.current_control, view.composer.control)
        await self.mouse(35, *self.at(view.output.transcript.window))
        self.assertIs(app.layout.current_control, view.composer.control)
        await self.mouse(65, *self.at(view.output.transcript.window))
        self.assertGreater(view.output.transcript.control.top_line, 0)
        self.assertFalse(view.output.transcript.control.is_focusable())
        self.assertIs(app.layout.current_control, view.composer.control)
        await self.mouse(64, *self.at(view.output.transcript.window))
        self.assertEqual(view.output.transcript.control.top_line, 0)

        # Output delegates a click to the composer, but never gains focus itself.
        with set_app(app):
            view.chat_page.focus_sidebar()
        await self.paint()
        await self.mouse(35, *self.at(view.output.transcript.window))
        await self.mouse(65, *self.at(view.output.transcript.window))
        self.assertIs(app.layout.current_control, view._session_control)
        await self.mouse(0, *self.at(view.output.transcript.window))
        await self.mouse(0, *self.at(view.output.transcript.window), release=True)
        self.assertIs(app.layout.current_control, view.composer.control)
        self.assertFalse(view.output.transcript.control.is_focusable())

    async def test_inactive_page_region_overrides_rich_colors_without_tinting_popups(self):
        self.view.output.transcript.set_messages((ChatMessage("assistant", "**accent**\n\n```python\nprint('bright')\n```"),))
        await self.paint()
        await eventually(lambda: bool(self.view.output.transcript.control.objects))

        def is_gray(window, backgrounds):
            rect = self.position(window)
            screen = self.app.renderer._last_screen
            attrs = self.app.renderer._attrs_for_style
            colors = {(attrs[screen.data_buffer[y][x].style].color,
                       attrs[screen.data_buffer[y][x].style].bgcolor)
                      for y in range(rect.ypos, rect.ypos + rect.height)
                      for x in range(rect.xpos, rect.xpos + rect.width)}
            def gray(color):
                return color in ("", "default") or len(color) == 6 and color[:2] == color[2:4] == color[4:]
            self.assertTrue(all(gray(fg) and gray(bg) for fg, bg in colors), colors)
            self.assertEqual({bg not in ("", "default") for _, bg in colors},
                             {bg not in ("", "default") for bg in backgrounds})

        def heading_cells(active):
            screen = self.app.renderer._last_screen
            attrs = self.app.renderer._attrs_for_style
            cells = [(x, y, cell.char, attrs[cell.style])
                     for y, row in screen.data_buffer.items() for x, cell in row.items()
                     if "class:hub.sidebar-heading" in cell.style.split()
                     and "class:hub.accent" in cell.style.split() and cell.char.strip()]
            self.assertTrue(cells)
            colors = {style.color for _, _, _, style in cells}
            accent = self.view.theme.accent.removeprefix("#")
            gray_accent = grayscale(accent)[1:]
            self.assertEqual(colors, {accent, gray_accent} if active else {gray_accent})
            for _, _, _, style in cells:
                self.assertTrue(style.bold)
                self.assertFalse(style.underline)
            return cells

        for settings in (False, True):
            if settings:
                with set_app(self.app):
                    self.view.toggle_settings()
                    self.view.settings.choose("appearance")
            page = self.view.active_page
            with set_app(self.app):
                page.focus_main()
            await self.paint()
            headings = heading_cells(False)
            sidebar = next(w for w, _ in self.view.mouse.windows if w.content is (
                self.view.settings.global_list if settings else self.view._session_control))
            is_gray(sidebar, {"default"})
            main = (self.view.settings.page.form.fields[("background",)].input.window
                    if settings else self.view.output.transcript.window)
            x, y = self.at(main)
            self.assertNotIn("hub.inactive", self.app.renderer._last_screen.data_buffer[y][x].style)
            cache_key = self.view.output.transcript.control._cache_key
            with set_app(self.app):
                page.focus_sidebar()
            await self.paint()
            self.assertEqual([(x, y, c) for x, y, c, _ in heading_cells(True)],
                             [(x, y, c) for x, y, c, _ in headings])
            is_gray(main, {"484848"} if settings else {"default", "484848"})
            is_gray(page.status, {"default"})
            x, y = self.at(sidebar)
            self.assertNotIn("hub.inactive", self.app.renderer._last_screen.data_buffer[y][x].style)
            self.assertEqual(self.view.output.transcript.control._cache_key, cache_key)

        with set_app(self.app):
            popup = ReadOnlyDialog("Log", "visible popup", self.view._rows, self.view._columns,
                                   self.view.close_dialog, self.view.t)
            self.view.dialogs.show(popup, popup.receiver)
        await self.paint()
        x, y = self.at(popup.output.window)
        self.assertNotIn("hub.inactive", self.app.renderer._last_screen.data_buffer[y][x].style)

    async def test_settings_cursor_only_appears_when_editing_and_only_actions_have_frame(self):
        with set_app(self.app):
            self.view.toggle_settings()
            self.view.settings.choose("appearance")
            self.view.settings.layout.focus_main()
        await self.paint()
        field = self.view.settings.page.form.fields[("background",)]
        with set_app(self.app):
            self.app.layout.focus(field.input)
        await self.paint()
        self.assertFalse(self.app.renderer._last_screen.show_cursor)
        self.pipe.send_text("\r")
        await eventually(lambda: field.editing)
        await self.paint()
        self.assertTrue(self.app.renderer._last_screen.show_cursor)
        self.pipe.send_text("\r")
        await eventually(lambda: not field.editing)
        await self.paint()
        self.assertFalse(self.app.renderer._last_screen.show_cursor)
        screen = self.app.renderer._last_screen
        left = self.view.sidebar_width() + 1
        action_top = self.output.rows - 2 - (4 + len(self.view.settings.page.bottom.rows()))
        # Only the action row has a frame; the option viewport stays borderless.
        for y in range(1, action_top):
            for x in (left, self.output.columns - 1):
                # Section separators may start at the content's left edge.
                self.assertNotIn(screen.data_buffer[y][x].char, "│┌┐└┘")
        self.assertEqual(screen.data_buffer[action_top][left].char, "┌")
        self.assertEqual(screen.data_buffer[action_top][self.output.columns - 1].char, "┐")
        self.assertEqual(screen.data_buffer[self.output.rows - 3][left].char, "└")
        self.assertEqual(screen.data_buffer[self.output.rows - 3][self.output.columns - 1].char, "┘")

    async def test_settings_and_home_share_width_right_inset_and_scrollbar_edge(self):
        for columns in (132, 80):
            self.output.columns = columns
            self.app._on_resize()
            # Home auto-hides its sidebar on narrow terminals; compare the
            # two pages with the same visible sidebar configuration.
            with set_app(self.app):
                self.view.chat_page.focus_sidebar()
            await self.paint()
            chat = self.position(self.view.output.transcript.window)
            chat_edge = chat.xpos + chat.width - 1
            self.assertEqual(chat_edge, columns - 2)
            with set_app(self.app):
                self.view.toggle_settings()
                self.view.settings.choose("appearance")
                self.view.settings.layout.focus_main()
            await self.paint()
            pane = self.view.settings.main
            rect = next(rect for item, rect in self.view.mouse.panes if item is pane)
            settings_edge = rect.xpos + rect.width - 1
            self.assertEqual((rect.xpos, rect.width), (chat.xpos, chat.width))
            self.assertEqual(settings_edge, chat_edge)
            gap = self.position(pane.scrollbar_gap)
            self.assertEqual((gap.xpos, gap.width), (settings_edge - 1, 1))
            field = self.view.settings.page.form.fields[("background",)].input.window
            field_rect = self.position(field)
            self.assertEqual(field_rect.xpos + field_rect.width, gap.xpos)
            cells = self.app.renderer._last_screen.data_buffer
            for y in range(rect.ypos, rect.ypos + rect.height):
                self.assertEqual(cells[y][gap.xpos].char, " ")
            await self.mouse(65, gap.xpos, rect.ypos + 1)
            self.assertEqual(pane.vertical_scroll, 3)
            await self.mouse(0, settings_edge, rect.ypos + rect.height - 1)
            await self.mouse(0, settings_edge, rect.ypos + rect.height - 1, release=True)
            self.assertEqual(pane.vertical_scroll, 4)
            with set_app(self.app):
                self.view.toggle_settings()
            await self.paint()

    async def test_scrollbar_drag_captures_outside_bar_and_arrows_and_track_work(self):
        view = self.view
        rect = self.position(view.output.transcript.window)
        x, top, bottom = rect.xpos + rect.width - 1, rect.ypos + 1, rect.ypos + rect.height - 2
        await self.mouse(0, x, top)
        self.assertIsNotNone(view.mouse.drag)
        await self.mouse(32, x - 12, bottom)
        limit = len(view.output.transcript.control._lines) - view.output.transcript.control._height
        self.assertEqual(view.output.transcript.control.top_line, limit)
        await self.mouse(0, x - 12, bottom, release=True)
        self.assertIsNone(view.mouse.drag)
        self.assertIs(self.app.layout.current_control, view.composer.control)
        await self.mouse(0, x, rect.ypos)
        await self.mouse(0, x, rect.ypos, release=True)
        self.assertEqual(view.output.transcript.control.top_line, limit - 1)
        await self.mouse(0, x, top)
        await self.mouse(0, x, top, release=True)
        self.assertLess(view.output.transcript.control.top_line, limit - 1)

    async def test_settings_blank_description_gutter_and_action_border_click_focus_main(self):
        with set_app(self.app):
            self.view.toggle_settings()
            self.view.settings.choose("appearance")
        await self.paint()
        screen = self.view.settings
        pane = next(rect for item, rect in self.view.mouse.panes if item is screen.main)
        button = self.position(screen.page.save.window)
        points = [(pane.xpos, pane.ypos), (pane.xpos + pane.width, pane.ypos + 2),
                  (pane.xpos + pane.width - 2, pane.ypos + 3),
                  (pane.xpos + 2, button.ypos - 1), (pane.xpos + 2, button.ypos + 2)]
        for point in points:
            with set_app(self.app):
                screen.layout.focus_sidebar()
            await self.paint()
            await self.mouse(35, *point)
            self.assertIs(self.app.layout.current_control, screen.global_list)
            await self.mouse(0, *point)
            await self.mouse(0, *point, release=True)
            self.assertNotIn(self.app.layout.current_control, (screen.global_list, screen.project_list))
            self.assertFalse(screen.editing)
        with set_app(self.app):
            screen.layout.focus_sidebar()
            popup = ReadOnlyDialog("Popup", "body", self.view._rows, self.view._columns,
                                   self.view.close_dialog, self.view.t)
            self.view.dialogs.show(popup, popup.receiver)
        await self.paint()
        await self.mouse(0, *points[1])
        await self.mouse(0, *points[1], release=True)
        self.assertIs(self.app.layout.current_control, popup.receiver)

    async def test_composer_and_settings_action_frame_default_to_five_rows(self):
        self.view.composer.text = ""
        await self.paint()
        rect = self.position(self.view.composer.window)
        cells = self.app.renderer._last_screen.data_buffer
        self.assertEqual(rect.height, 2)
        self.assertEqual(cells[rect.ypos - 1][rect.xpos - 1].char, "┌")
        self.assertEqual(cells[rect.ypos + 3][rect.xpos - 1].char, "└")
        self.assertIn("Enter", "".join(cells[rect.ypos + 2][x].char for x in range(rect.xpos, rect.xpos + rect.width)))
        self.assertEqual(cells[rect.ypos + 2][rect.xpos - 1].char, "│")
        self.assertTrue(all(cells[rect.ypos + 3][x].char == "─" for x in range(rect.xpos, rect.xpos + rect.width)))
        with set_app(self.app):
            self.view.toggle_settings()
            self.view.settings.choose("appearance")
        await self.paint()
        rect = self.position(self.view.settings.page.save.window)
        x = self.view.sidebar_width() + 1
        cells = self.app.renderer._last_screen.data_buffer
        self.assertEqual(rect.height, 1)
        self.assertEqual(cells[rect.ypos - 2][x].char, "┌")
        self.assertEqual(cells[rect.ypos + 2][x].char, "└")

    async def test_button_hover_effect_click_and_modal_isolation(self):
        activated = []
        button = Button("Choose", handler=lambda: activated.append(True))
        with set_app(self.app):
            popup = Dialog(title="Buttons", body=HSplit([button]), buttons=[])
            self.view.dialogs.show(popup, button)
        await self.paint()
        await self.mouse(35, *self.at(button.window))
        self.assertIs(self.app.layout.current_control, button.control)
        self.assertTrue(button.window.hub_mouse_hover)
        rect = self.position(button.window)
        cells = self.app.renderer._last_screen.data_buffer
        self.assertIn("hub.mouse-hover", cells[rect.ypos][rect.xpos + 1].style)
        await self.mouse(0, *self.at(button.window))
        await self.mouse(0, *self.at(button.window), release=True)
        self.assertEqual(activated, [True])
        before = self.view.output.transcript.control.top_line
        await self.mouse(35, *self.at(self.view.composer.window))
        await self.mouse(65, *self.at(self.view.output.transcript.window))
        self.assertIs(self.app.layout.current_control, button.control)
        self.assertEqual(self.view.output.transcript.control.top_line, before)

    async def test_readonly_popup_scrolls_without_focusing_body(self):
        view = self.view
        with set_app(self.app):
            popup = ReadOnlyDialog("Log", "\n".join(f"entry {i}" for i in range(180)),
                view._rows, view._columns, view.close_dialog, view.t)
            view.dialogs.show(popup, popup.receiver)
        await self.paint()
        popup.output.scroll("home")
        await self.paint()
        await self.mouse(35, *self.at(popup.output.window))
        await self.mouse(65, *self.at(popup.output.window))
        self.assertEqual(popup.output.top_line, 3)
        self.assertIs(self.app.layout.current_control, popup.receiver)
        self.assertFalse(popup.output.is_focusable())

    async def test_settings_pane_wheel_and_drag_do_not_snap_to_focused_field(self):
        fields = [TextArea(text=f"field {i}", height=2) for i in range(60)]
        pane = ScrollablePane(HSplit(fields))
        with set_app(self.app):
            popup = Dialog(title="Settings", body=HSplit([pane], height=16), buttons=[])
            self.view.dialogs.show(popup, fields[0])
        await self.paint()
        focus = self.app.layout.current_control
        rect = next(rect for item, rect in self.view.mouse.panes if item is pane)
        await self.mouse(65, rect.xpos + 2, rect.ypos + 2)
        self.assertEqual(pane.vertical_scroll, 3)
        await self.paint()
        self.assertEqual(pane.vertical_scroll, 3)
        self.assertIs(self.app.layout.current_control, focus)
        geometry = pane.geometry()
        x, y = rect.xpos + rect.width - 1, rect.ypos + geometry.top
        await self.mouse(0, x, y)
        self.assertIsNotNone(self.view.mouse.drag)
        await self.mouse(32, x - 5, rect.ypos + rect.height - 2)
        await self.mouse(0, x - 5, rect.ypos + rect.height - 2, release=True)
        self.assertEqual(pane.vertical_scroll, pane.geometry().limit)
        self.assertIs(self.app.layout.current_control, focus)
        self.assertIsNone(self.view.mouse.drag)

    async def test_answer_choices_remain_nonfocusable_and_number_selection_unchanged(self):
        view = self.view
        view.question.sync("p", "s", ({"run_id": "r", "live": True, "request": {
            "id": "q", "revision": 1, "title": "Choose", "source": {"choices": ["Yes", "No"]}}},))
        view.composer.text = "2"
        await self.paint()
        await self.mouse(35, *self.at(view.question.choices.window))
        await self.mouse(0, *self.at(view.question.choices.window), release=True)
        self.assertFalse(view.question.choices.control.is_focusable())
        self.assertIs(self.app.layout.current_control, view.composer.control)
        self.assertEqual(view.composer.text, "2")

    async def test_actual_settings_scroll_preserves_focus_and_keyboard_navigation_resumes(self):
        with set_app(self.app):
            self.view.toggle_settings()
            self.view.settings.choose("appearance")
            self.view.settings.layout.focus_main()
        await self.paint()
        pane = self.view.settings.main
        focus = self.app.layout.current_control
        rect = next(rect for item, rect in self.view.mouse.panes if item is pane)
        self.assertGreater(pane.geometry().limit, 0)
        await self.mouse(65, rect.xpos + 2, rect.ypos + 2)
        self.assertGreater(pane.vertical_scroll, 0)
        self.assertIs(self.app.layout.current_control, focus)
        await self.paint()
        self.assertGreater(pane.vertical_scroll, 0)
        self.pipe.send_text("\x1b[B")
        await eventually(lambda: self.app.layout.current_control is not focus)
        self.assertIsNone(pane._mouse_anchor)

    async def test_list_wheel_keeps_selection_and_hover_does_not_activate(self):
        choices = RadioList([(i, f"Choice {i}") for i in range(50)])
        choices.window.height = 8
        field = TextArea(text="draft", height=1)
        with set_app(self.app):
            self.view.dialogs.show(Dialog(title="List", body=HSplit([choices, field]), buttons=[]), field)
        await self.paint()
        await self.mouse(65, *self.at(choices.window))
        self.assertGreater(choices.window.vertical_scroll, 0)
        self.assertEqual(choices.current_value, 0)
        self.assertIs(self.app.layout.current_control, field.control)
        await self.mouse(35, *self.at(choices.window))
        self.assertIs(self.app.layout.current_control, field.control)
        await self.mouse(0, *self.at(choices.window))
        self.assertIs(self.app.layout.current_control, choices.control)
        self.assertEqual(choices.current_value, 0)

    async def test_settings_alt_scroll_is_local_preserves_editing_and_cursor(self):
        with set_app(self.app):
            self.view.toggle_settings()
            self.view.settings.choose("appearance")
            self.view.settings.layout.focus_main()
        await self.paint()
        screen = self.view.settings
        field = screen.page.form.fields[("background",)]
        with set_app(self.app):
            self.app.layout.focus(field.input)
            field.begin()
        await self.paint()
        before = field.input.text, field.input.buffer.cursor_position
        transcript_top = self.view.output.transcript.control.top_line
        for sequence, expected in (("\x1b[B", 1), ("\x1b[6~", None), ("\x1b[F", "end"),
                                   ("\x1b[5~", None), ("\x1b[H", 0)):
            self.pipe.send_text("\x1b" + sequence)
            await asyncio.sleep(.04)
            await self.paint()
            if expected is not None:
                self.assertEqual(screen.main.vertical_scroll,
                                 screen.main.geometry().limit if expected == "end" else expected)
            self.assertIs(self.app.layout.current_control, field.input.control)
            self.assertTrue(field.editing)
            self.assertEqual((field.input.text, field.input.buffer.cursor_position), before)
            self.assertEqual(self.view.output.transcript.control.top_line, transcript_top)
        self.pipe.send_text("\x1b\x1b[C\x1b\x1b[D")
        await asyncio.sleep(.05)
        self.assertIs(self.app.layout.current_control, field.input.control)
        self.assertEqual(screen.main.vertical_scroll, 0)

    async def test_both_page_edges_drag_resize_and_save_only_width_on_release(self):
        saved = []
        self.view.on_sidebar_width = saved.append
        for settings in (False, True):
            if settings:
                with set_app(self.app):
                    self.view.toggle_settings()
                    self.view.settings.choose("appearance")
                    self.view.settings.layout.focus_main()
            await self.paint()
            focus = self.app.layout.current_control
            width = self.view.sidebar_width()
            rect = self.position(self.view.active_page.edge)
            await self.mouse(35, rect.xpos, rect.ypos + 3)
            self.assertIs(self.app.layout.current_control, focus)
            await self.mouse(0, rect.xpos, rect.ypos + 3)
            await self.mouse(32, rect.xpos + 4, rect.ypos + 5)
            self.assertEqual(self.view.theme.sidebar_width, width + 4)
            self.assertEqual(len(saved), int(settings))
            await self.mouse(0, rect.xpos + 4, rect.ypos + 5, release=True)
            self.assertEqual(saved[-1], width + 4)
            self.assertEqual(len(saved), int(settings) + 1)
            self.assertIs(self.app.layout.current_control, focus)
            self.assertFalse(self.view.active_page.edge.content.is_focusable())

    async def test_sidebar_click_selects_session_and_settings_including_row_whitespace(self):
        from hub.model import SessionView
        self.view.sessions = [SessionView("one"), SessionView("two")]
        await self.paint()
        sidebar = next(w for w, _ in self.view.mouse.windows if w.content is self.view._session_control)
        rect = self.position(sidebar)
        row = self.view._session_control.cursor_position().y + (3 if self.view.sessions[0].status else 1)
        await self.mouse(35, rect.xpos + rect.width - 2, rect.ypos + row)
        self.assertEqual(self.view.selected, 0)
        await self.mouse(0, rect.xpos + rect.width - 2, rect.ypos + row)
        await self.mouse(0, rect.xpos + rect.width - 2, rect.ypos + row, release=True)
        self.assertEqual(self.view.selected, 1)
        self.assertIs(self.app.layout.current_control, sidebar.content)
        with set_app(self.app):
            self.view.toggle_settings()
            self.view.settings.layout.focus_main()
        await self.paint()
        sidebar = next(w for w, _ in self.view.mouse.windows if w.content is self.view.settings.global_list)
        rect = self.position(sidebar)
        await self.mouse(0, rect.xpos + rect.width - 2, rect.ypos + 1)
        await self.mouse(0, rect.xpos + rect.width - 2, rect.ypos + 1, release=True)
        self.assertEqual(self.view.settings.selected, "appearance")
        self.assertIs(self.app.layout.current_control, sidebar.content)

    async def test_transcript_drag_selects_unicode_without_focus_or_draft_changes(self):
        control = self.view.output.transcript.control
        self.view.output.transcript.set_messages((ChatMessage("assistant", "alpha 한글 😀 omega\n\nsecond line", id="m"),))
        await self.paint()
        await eventually(lambda: any("alpha" in "".join(f[1] for f in row) for row in control._lines))
        control.scroll("home")
        self.view.composer.text = "draft"
        await self.paint()
        row = next(i for i in range(len(control._lines)) if "alpha" in control.selection.line(i))
        text = control.selection.line(row)
        from prompt_toolkit.utils import get_cwidth
        rect = self.position(self.view.output.transcript.window)
        start, end = text.index("한"), text.index(" omega")
        x = rect.xpos + get_cwidth(text[:start])
        to = rect.xpos + get_cwidth(text[:end])
        y = rect.ypos + row
        await self.mouse(0, x, y)
        await self.mouse(32, to, y)
        self.assertEqual(control.selection.text, "한글 😀")
        await self.mouse(0, to, y, release=True)
        with patch("hub.widget.clipboard.export_text", new=AsyncMock(return_value="clipboard_copied")) as copied:
            self.pipe.send_text("\x03")
            await eventually(lambda: copied.await_count == 1)
            self.assertEqual(copied.call_args.args[1], "한글 😀")
        self.assertEqual(self.app.clipboard.get_data().text, "한글 😀")
        self.assertIs(self.app.layout.current_control, self.view.composer.control)
        self.assertEqual(self.view.composer.text, "draft")
        self.assertFalse(control.is_focusable())
        self.assertIn("hub.text-selection", self.app.renderer._last_screen.data_buffer[y][x].style)
        self.output.columns -= 10
        self.app._on_resize()
        await self.paint()
        self.assertEqual(control.selection.text, "")

    async def test_expanded_code_and_table_drag_and_copy_selection(self):
        from hub.widget.output_inspector import OutputInspector
        from hub.widget.output.table_inspector import TableInspector
        for source, expected in (("```text\nabcdef\nsecond\n```", OutputInspector),
                                 ("| Name | Value |\n| --- | --- |\n| alpha | 1 |\n| beta | 2 |", TableInspector)):
            self.view.output.transcript.set_messages((ChatMessage("assistant", source, id="m"),))
            await self.paint()
            await eventually(lambda: bool(self.view.output.transcript.control.objects))
            await auxiliary(self.app, self.pipe, self.view, 'tags')
            self.pipe.send_text('\r')
            await eventually(lambda: self.view._dialog is not None)
            popup = self.view._dialog
            self.assertIsInstance(popup, expected)
            control = popup.table if isinstance(popup, TableInspector) else popup.output
            needle = "alpha" if isinstance(popup, TableInspector) else "abcdef"
            await eventually(lambda: control.selection.content is not None and
                             any(needle in control.selection.line(row)
                                 for row in range(control.selection.content.line_count)))
            await self.paint()
            rect = self.position(control.window)
            row = next(row for row in range(control.selection.content.line_count) if needle in control.selection.line(row))
            col = control.selection.line(row).index(needle)
            y = rect.ypos + row
            await self.mouse(0, rect.xpos + col + 1, y)
            await self.mouse(32, rect.xpos + col + 4, y)
            await self.mouse(0, rect.xpos + col + 4, y, release=True)
            selected = control.selection.text
            self.assertTrue(selected)
            self.assertIs(self.app.layout.current_control, popup.receiver)
            self.assertFalse(control.is_focusable())
            with patch("hub.widget.output_inspector.copy_text", new=AsyncMock(return_value="clipboard_copied")) as copy:
                self.pipe.send_text("c")
                await eventually(lambda: copy.await_count == 1)
                copy.assert_awaited_once_with(selected)
            self.pipe.send_text("\x1b")
            await eventually(lambda: self.view._dialog is None)
            self.pipe.send_text('\x1b')
            await eventually(lambda: not self.view.output.tags.visible)

    async def test_popup_during_drag_cancels_capture_and_swallows_release(self):
        rect = self.position(self.view.output.transcript.window)
        await self.mouse(0, rect.xpos + rect.width - 1, rect.ypos + 1)
        self.assertIsNotNone(self.view.mouse.drag)
        activated = []
        button = Button("Never activate on release", handler=lambda: activated.append(True))
        with set_app(self.app):
            self.view.dialogs.show(Dialog(title="New popup", body=button, buttons=[]), button)
        await self.paint()
        self.assertIsNone(self.view.mouse.drag)
        await self.mouse(0, *self.at(button.window), release=True)
        self.assertEqual(activated, [])

    async def test_minimize_and_resize_release_capture_without_changing_shell_mouse(self):
        view = self.view
        rect = self.position(view.output.transcript.window)
        await self.mouse(0, rect.xpos + rect.width - 1, rect.ypos + 1)
        self.output.columns, self.output.rows = 80, 24
        self.app._on_resize()
        await self.paint()
        await self.mouse(32, 70, 12)
        await self.mouse(0, 70, 12, release=True)
        self.assertIsNone(view.mouse.drag)
        await minimize(self.pipe, view)
        await eventually(lambda: not view.visible)
        self.assertIsNone(view.mouse.app)
        self.assertFalse(self.app.mouse_support())
        self.assertIs(self.app.current_buffer, self.prompt.default_buffer)


class ScrollbarGeometryTests(unittest.TestCase):
    def test_tiny_empty_and_overflow_geometry_is_clamped(self):
        for height in (1, 2, 3, 20):
            for total in (0, 1, 1000):
                geometry = ScrollbarGeometry(height, total, height, 0)
                self.assertGreaterEqual(geometry.thumb, 1)
                self.assertEqual(geometry.position(-100), 0)
                self.assertLessEqual(geometry.position(10000), geometry.limit)
