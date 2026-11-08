"""Category resizing persists only released geometry, separately for each page."""

import asyncio
import tempfile
import unittest
from unittest.mock import Mock

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import create_pipe_input

from examples.hub.preview import create_preview
from hub.config.preferences import PreferencesStore
from hub.config.sidebar_heights import SidebarHeights
from hub.widget.mouse_contract import MouseBinding
from tests.hub.support import SizedOutput, eventually


class SidebarHeightStorageTests(unittest.TestCase):
    def test_geometry_has_its_own_record_and_does_not_save_preferences(self):
        with tempfile.TemporaryDirectory() as root:
            store = PreferencesStore(root)
            self.assertEqual(store.snapshot().sidebar_heights, SidebarHeights())
            store.save('profile', {'display_name': 'User'})
            before = store.path.read_bytes()
            value = SidebarHeights(home=.4, settings=.3)
            store.save_sidebar_heights(value)
            self.assertEqual(store.path.read_bytes(), before)
            self.assertEqual(PreferencesStore(root).snapshot().sidebar_heights, value)
            self.assertNotIn('sidebar_heights', store.load())

    def test_invalid_geometry_is_rejected_without_migration(self):
        for value in ({}, {'home': .3}, {'home': .3, 'settings': .4, 'other': .2},
                      {'home': True, 'settings': None}, {'home': 1, 'settings': None},
                      {'home': 0, 'settings': None}, {'home': float('nan'), 'settings': None}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                SidebarHeights.read(value)


class SidebarHeightMouseTests(unittest.IsolatedAsyncioTestCase):
    async def test_home_and_settings_drag_persist_once_on_release_and_keep_profile_fixed(self):
        with tempfile.TemporaryDirectory() as root, create_pipe_input() as pipe:
            output = SizedOutput()
            app, view, _ = create_preview(input=pipe, output=output)
            store = PreferencesStore(root)
            save = Mock(side_effect=store.save_sidebar_heights)
            view.on_sidebar_heights = save
            task = asyncio.create_task(app.run_async(pre_run=lambda: view.show(app)))

            async def paint():
                frame = app.render_counter
                app.invalidate()
                await eventually(lambda: app.render_counter > frame)

            async def mouse(code, x, y, release=False):
                pipe.send_text(f"\x1b[<{code};{x + 1};{y + 1}{'m' if release else 'M'}")
                await asyncio.sleep(.05)
                await paint()

            try:
                await eventually(lambda: app.is_running and view.visible)
                with set_app(app):
                    for page in ('home', 'settings'):
                        if page == 'settings':
                            view.toggle_settings()
                            view.settings.choose('appearance')
                        await paint()
                        initial = view.sidebar_heights
                        positions = app.renderer._last_screen.visible_windows_to_write_positions
                        headings = sorted((rect for window, rect in positions.items()
                                           if callable(getattr(window, 'drag_handler', None))
                                           and window is not view.active_page.edge), key=lambda p: p.ypos)
                        self.assertEqual(len(headings), 2)  # Lower title and rule only.
                        upper, lower = view.active_page.categories
                        app.layout.focus(upper)
                        rect = headings[0]
                        x, y = rect.xpos + 2, rect.ypos
                        profile_y = positions[view.profile_bar.page.window].ypos
                        count = save.call_count
                        # An inactive lower category must not resize either list.
                        await mouse(0, x, y)
                        await mouse(32, x, y + 3)
                        await mouse(0, x, y + 3, True)
                        self.assertEqual(view.sidebar_heights, initial)
                        self.assertEqual(save.call_count, count)
                        # The top title remains fixed even with its category focused.
                        top_window = next(w for w, p in positions.items()
                                          if p.xpos == rect.xpos and p.ypos < y
                                          and p.height == 1)
                        self.assertFalse(hasattr(top_window, 'drag_handler'))
                        if page == 'settings':
                            global_window = next(p for w, p in positions.items() if w.content is upper)
                            self.assertEqual(global_window.ypos + global_window.height, y)
                        app.layout.focus(lower)
                        await paint()
                        await mouse(35, x, headings[1].ypos)
                        rule = next(w for w, p in app.renderer._last_screen.visible_windows_to_write_positions.items()
                                    if p.ypos == headings[1].ypos and p.xpos == rect.xpos)
                        self.assertTrue(MouseBinding.hovered(rule))
                        await mouse(0, x, y)
                        await mouse(32, x, y + 3)
                        self.assertEqual(save.call_count, count)
                        self.assertNotEqual(view.sidebar_heights.ratio(page), initial.ratio(page))
                        await mouse(0, x, y + 3, True)
                        self.assertEqual(save.call_count, count + 1)
                        self.assertEqual(store.snapshot().sidebar_heights, view.sidebar_heights)
                        positions = app.renderer._last_screen.visible_windows_to_write_positions
                        self.assertEqual(positions[view.profile_bar.page.window].ypos, profile_y)
                        other = 'settings' if page == 'home' else 'home'
                        self.assertEqual(view.sidebar_heights.ratio(other), initial.ratio(other))
                    stored = view.sidebar_heights
                    original_rows = output.rows
                    original_panel_height = view.settings._left.geometry[1]
                    output.rows = 22
                    app._on_resize()
                    await paint()
                    self.assertEqual(view.sidebar_heights, stored)
                    self.assertEqual(save.call_count, 2)
                    self.assertEqual(store.snapshot().sidebar_heights, stored)
                    positions = app.renderer._last_screen.visible_windows_to_write_positions
                    shrink = original_rows - output.rows
                    self.assertEqual(positions[view.profile_bar.page.window].ypos, profile_y - shrink)
                    self.assertEqual(view.settings._left.geometry[1], original_panel_height - shrink)
            finally:
                app.exit()
                await task
