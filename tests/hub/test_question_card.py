"""Request cards are readable without introducing another input/focus target."""

from dataclasses import replace
import unittest
from unittest.mock import Mock, patch

from hub.config.theme import HubTheme
from hub.model import ChatMessage, HubSnapshot, RetrySummary, RunSummary, SessionSummary
from hub.ui.live import LiveHubView
from hub.widget.transcript.conversation import render_messages
from prompt_toolkit.application import Application
from prompt_toolkit.application.current import set_app
from prompt_toolkit.formatted_text.utils import fragment_list_width
from prompt_toolkit.input import DummyInput
from prompt_toolkit.layout import HSplit, Layout
from prompt_toolkit.layout.mouse_handlers import MouseHandlers
from prompt_toolkit.layout.screen import Screen, WritePosition
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.utils import get_cwidth


class QuestionCardTests(unittest.TestCase):
    def setUp(self):
        self.view = LiveHubView()
        self.addCleanup(self.view.render_worker.close)
        self.addCleanup(self.view.close)

    def show(self, request, *, live=True):
        request = {"id": "q", "revision": 1, **request}
        self.view.question.sync("p", "s", ({"run_id": "r", "live": live, "request": request},))
        return self.view.question

    def draw_choices(self, width):
        choices = self.view.question.choices.container
        app = Application(layout=Layout(HSplit([choices, self.view.composer]),
                                        focused_element=self.view.composer),
                          input=DummyInput(), output=DummyOutput())
        self.paint_count = getattr(self, "paint_count", 0) + 1
        app.render_counter = self.paint_count
        screen = Screen()
        with set_app(app):
            height = choices.preferred_height(width, 100).preferred
            choices.write_to_screen(screen, MouseHandlers(), WritePosition(0, 0, width, height),
                                    "class:hub", True, None)
        return [list(screen.data_buffer[y][x] for x in range(width)) for y in range(height)]

    def test_choices_border_fills_available_width_and_wraps_on_narrow_screens(self):
        question = self.show({"title": "Choose", "source": {"choices": ["승인", "거절"]}})
        self.view.composer.text = "2"
        rows = self.draw_choices(100)
        self.assertEqual(len(rows), 4)
        self.assertEqual("".join(cell.char for cell in rows[0]), "┌" + "─" * 98 + "┐")
        self.assertIn("(*) 2. 거절", "".join(cell.char for cell in rows[2]))
        self.assertTrue(all(row[99].char == "│" for row in rows[1:-1]))
        theme = HubTheme(background="#102030")
        style = theme.style()
        for row in rows:
            for cell in row:
                self.assertEqual(style.get_attrs_for_style_str(cell.style).bgcolor, "102030")
        checked = style.get_attrs_for_style_str(rows[2][2].style)
        self.assertTrue(checked.bold)
        self.assertEqual(checked.color, theme.accent.removeprefix("#"))

        long_label = "아주 긴 선택지 " * 8
        question.item["request"]["source"]["choices"] = [long_label, "다음"]
        width = get_cwidth("( ) 1. " + long_label) + 22
        rows = self.draw_choices(width)
        self.assertEqual(rows[0][width - 1].char, "┐")
        self.assertEqual(rows[-1][width - 1].char, "┘")
        rows = self.draw_choices(24)
        self.assertGreater(len(rows), 4)
        self.assertEqual(rows[0][23].char, "┐")
        self.assertTrue(all(row[23].char == "│" for row in rows[1:-1]))
        self.assertEqual(rows[-1][23].char, "┘")
        rendered = "".join(cell.char for row in rows[1:-1] for cell in row[1:-1])
        self.assertIn("2. 다음", rendered)
        self.assertEqual(rendered.count("아주"), 8)

    def test_optional_choices_leave_one_blank_line_and_accept_free_text(self):
        question = self.show({"title": "What should change?", "source": {}})
        rows = self.draw_choices(100)
        self.assertEqual(len(rows), 1)
        self.assertEqual("".join(cell.char for cell in rows[0]).strip(), "")
        self.assertNotIn("선택지", question.hint())
        self.view.on_answer = Mock()
        self.view.composer.text = "Add documentation."
        question.answer()
        self.view.on_answer.assert_called_once_with(question.item, "reply", "Add documentation.")

    def test_fixed_headings_and_compact_details_hint_follow_icon_style(self):
        from hub.asset.icon import UNICODE
        question = self.show({"title": "Long question", "source": {}})
        self.assertEqual(question.heading(), "  질문")
        self.assertEqual(question.hint().splitlines()[-1], " Ctrl+O 전체 내용 보기")
        question.item["request"]["action"] = {"tool": "shell_execute", "arguments": {}}
        self.assertEqual(question.heading(), "  승인 요청")
        self.view.t.icon_source = UNICODE
        self.assertEqual(question.heading(), " ? 승인 요청")

    def test_question_markdown_and_number_highlight_keep_composer_as_only_input(self):
        question = self.show({"title": "Choose **output**", "description": "Use **Markdown** or `plain text`?",
                              "source": {"choices": ["Markdown", "Text"]}})
        for width in (24, 80):
            _, build = question.details.render_request(width)
            rows = build()
            rendered = "\n".join("".join(text for _, text in row) for row in rows)
            self.assertIn("Markdown", rendered)
            self.assertIn("Choose output", rendered)
            self.assertNotIn("**", rendered)
            self.assertNotIn("`", rendered)
            self.assertTrue(any("bold" in style for row in rows for style, _ in row))
            self.assertLessEqual(max(map(fragment_list_width, rows)), width)
        self.assertFalse(question.details.is_focusable())
        self.view.composer.text = "2"
        selected = "".join(text for style, text in question.options() if "selected" in style)
        self.assertIn("2. Text", selected)
        self.assertNotIn("1. Markdown", selected)
        self.assertEqual(question.choices.current_value, "2")
        self.assertFalse(question.choices.control.is_focusable())
        self.assertTrue(question.details.window.dont_extend_height())
        self.view.composer.text = ""
        self.assertFalse(any("selected" in style for style, text in question.options()))
        self.assertIsNone(question.choices.current_value)

    def test_approval_has_colored_risk_and_code_target_without_payload_dump(self):
        question = self.show({"title": "Tool 실행 승인", "risk_scheme": "hub-risk-v1", "risk": 70,
            "action": {"tool": "file_create", "arguments": {"path": "src/example.py", "content": "PRIVATE_BODY" * 1000}},
            "options": [{"id": "approve", "label": "승인"}, {"id": "deny", "label": "거절"}]}, live=False)
        _, build = question.details.render_request(64)
        rows = build()
        text = "\n".join("".join(value for _, value in row) for row in rows)
        self.assertIn("src/example.py", text)
        self.assertIn("high (70)", text)
        self.assertNotIn("PRIVATE_BODY", text)
        self.assertTrue(any("bg:" in style for row in rows for style, value in row if "example.py" in value))
        self.assertTrue(any(self.view.theme.notice.lower() in style.lower() for row in rows for style, value in row if "high" in value))

    def test_new_question_never_keeps_previous_card_while_render_is_pending(self):
        question = self.show({"title": "Previous request", "source": {}})
        question.details.create_content(60, 5)
        self.view.question.sync("p", "s", ({"run_id": "r", "live": True,
            "request": {"id": "next", "title": "Current request", "source": {}}},))
        self.assertIn("Current request", "\n".join(question.details.lines))
        self.assertNotIn("Previous request", "\n".join(question.details.lines))
        self.assertIsNone(question.details._requested_key)

    def test_retry_moves_to_assistant_header_with_local_countdown(self):
        self.view.apply_snapshot(HubSnapshot("p", "Project", "model", "file",
            (SessionSummary("s", "Session", "running"),), "s",
            (ChatMessage("assistant", "Partial", id="a", status="streaming", elapsed_seconds=1),),
            run=RunSummary("r", "running", "loop", 1, True, (), "", RetrySummary(1, 4, 110))))
        with patch("hub.widget.feedback.retry_status.monotonic", return_value=104):
            messages = self.view.preparing.project(("p", "s"), self.view.sessions[0].messages)
        rows = render_messages(messages, 100, HubTheme(), self.view.t, section="header")
        text = "".join(value for row in rows for _, value in row)
        self.assertIn("2번째 시도까지 6초", text)
        self.assertNotIn("Partial", text)
        self.view._last_snapshot = replace(self.view._last_snapshot, run=None)
        self.assertEqual(self.view.preparing.project(("p", "s"), messages)[0].activity, "")
