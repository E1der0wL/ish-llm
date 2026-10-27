"""Popup borders and actions follow allocated terminal cells in every state."""

import unittest
from types import SimpleNamespace

from prompt_toolkit.application.current import set_app
from prompt_toolkit.input import DummyInput
from prompt_toolkit.layout.containers import to_container
from prompt_toolkit.layout.mouse_handlers import MouseHandlers
from prompt_toolkit.layout.screen import Screen, WritePosition

from examples.hub.preview import create_preview
from hub.ui.chat.history_ui import HistoryUI
from hub.widget.dialog import PopupGeometry, PopupSize
from hub.widget.forms.request_history import RequestHistoryDialog
from hub.widget.controls import RadioList, Button
from hub.widget.dialog import Dialog
from tests.hub.support import SizedOutput


class PopupGeometryTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_list_expands_and_status_stays_below_actions(self):
        app, view, _ = create_preview(input=DummyInput(), output=SizedOutput(132, 40))
        try:
            with set_app(app):
                choices = RadioList([('a', 'Alpha'), ('b', 'Beta')])
                button = Button('Close')
                popup = Dialog(body=choices, buttons=[button])
                view.dialogs.show(popup, choices)
                view.notice = 'status-marker'
                screen = self.paint(popup, 100, 36)
                positions = screen.visible_windows_to_write_positions
                self.assertGreater(positions[choices.window].height, 20)
                self.assertEqual(positions[popup.status_bar].ypos, 34)
                self.assertLess(positions[button.window].ypos, 34)
                text = ''.join(screen.data_buffer[34][x].char for x in range(100))
                self.assertIn('status-marker', text)
                self.assertEqual(view.progress.container().content.create_content(100,1).get_line(0), [])
        finally:
            view.close()

    def paint(self, popup, width, height):
        screen = Screen()
        to_container(popup).write_to_screen(
            screen, MouseHandlers(), WritePosition(0, 0, width, height), "", True, None
        )
        screen.draw_all_floats()
        self.assertEqual(screen.data_buffer[0][0].char, "┌")
        self.assertEqual(screen.data_buffer[0][width - 1].char, "┐")
        self.assertEqual(screen.data_buffer[height - 1][0].char, "└")
        self.assertEqual(screen.data_buffer[height - 1][width - 1].char, "┘")
        for y in range(1, height - 1):
            self.assertEqual(screen.data_buffer[y][0].char, "│")
            self.assertEqual(screen.data_buffer[y][width - 1].char, "│")
        return screen

    async def test_history_loading_loaded_empty_share_medium_border(self):
        app, view, _ = create_preview(input=DummyInput(), output=SizedOutput(180, 50))
        view.project_id = "project"
        pending = []
        history = HistoryUI(SimpleNamespace(
            view=view, _call=lambda *args, completed: pending.append(completed)
        ))
        try:
            with set_app(app):
                history.open()
                self.paint(view._dialog, 100, 36)
                row = dict(id="u", request_id="u", status="completed", text="request",
                           response="answer", runs=[], time="", engine="loop",
                           elapsed=None, error="")
                pending.pop()([row])
                popup = view._dialog
                self.assertEqual(popup.size, PopupSize.MEDIUM)
                self.assertEqual(popup.selected(), [])
                for width in (100, 99, 61):
                    app.output.columns = width + PopupGeometry.MARGIN
                    screen = self.paint(popup, width, 36)
                    positions = [screen.visible_windows_to_write_positions[b.window]
                                 for b in popup.buttons]
                    left, right = positions[0].xpos, positions[-1].xpos + positions[-1].width
                    self.assertLessEqual(abs(left - (width - right)), 1)
                    self.assertEqual(len({p.ypos for p in positions}), 1)
                app.output.columns = 180
                history.show(view.sessions[view.selected].id, [])
                self.paint(view._dialog, 100, 36)
        finally:
            view.close()

    async def test_participant_menu_border_fills_medium_and_small_terminal(self):
        for columns, rows in ((180, 50), (65, 18)):
            app, view, _ = create_preview(input=DummyInput(), output=SizedOutput(columns, rows))
            try:
                with set_app(app):
                    view.participant_ids = ["assistant"]
                    bar = view.output.participants
                    bar.selected = "assistant"
                    bar.expand()
                    self.assertEqual(view._dialog.size, PopupSize.MEDIUM)
                    dimensions = PopupGeometry.dimensions(PopupSize.MEDIUM)
                    self.paint(view._dialog, *(value.preferred for value in dimensions))
            finally:
                view.close()
