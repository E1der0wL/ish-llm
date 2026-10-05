"""Check the rendering boundaries that differ from a plain TextArea."""

import unittest
from unittest.mock import patch

from prompt_toolkit.styles import Style, merge_styles
from prompt_toolkit.utils import get_cwidth

from hub.ui.chat.conversation import ChatMessage, ConversationControl, render_messages
from hub.config.theme import HubTheme


class ConversationTests(unittest.TestCase):
    def test_horizontal_scroll_is_bounded_to_overflow_and_resets_on_resize(self):
        control = ConversationControl((), HubTheme())
        with patch("hub.ui.chat.conversation.render_messages", return_value=[[("", "x" * 100)]]):
            control.create_content(80, 10)
            for _ in range(30):
                control.scroll("right")
            self.assertEqual(control.left_column, 20)
            control.scroll("left")
            self.assertEqual(control.left_column, 19)
            control.create_content(120, 10)
            self.assertEqual(control.left_column, 0)
            self.assertFalse(control.is_focusable())

    def test_markdown_rules_use_box_drawing_without_rewriting_code(self):
        from rich.markdown import HorizontalRule, Markdown
        for role in ("assistant", "user", "draft"):
            with self.subTest(role=role):
                source = "Above\n\n---\n\nBelow\n\n```text\n---\n```\n\n`a---b`"
                lines = render_messages((ChatMessage(role, source),), 70, HubTheme())
                text = "\n".join("".join(part[1] for part in line) for line in lines)
                self.assertIn("─" * 10, text)
                self.assertIn("---", text)
                self.assertIn("a---b", text)
                self.assertNotIn("-" * 10, text)
        self.assertIs(Markdown.elements["hr"], HorizontalRule)

    def test_short_user_markdown_has_a_compact_right_aligned_background(self):
        theme = HubTheme(accent2="#123456")
        messages = (ChatMessage("user", "**짧은 메시지**", "2026-10-03 12:34:56",
                                status="committed", author="tester"),)
        lines = render_messages(messages, 100, theme)
        body = next(line for line in lines if any("짧은 메시지" in text for _, text in line))
        text = "".join(text for _, text in body)
        self.assertGreater(get_cwidth(text[:text.index("짧은")]), 80)
        self.assertEqual(get_cwidth(text), 98)
        block = [(style, text) for style, text in body if "hub.user-block" in style]
        self.assertLess(sum(get_cwidth(text) for _, text in block), 20)
        self.assertEqual(theme.style().get_attrs_for_style_str(block[0][0]).bgcolor, theme.user_background[1:])
        header = "".join(text for _, text in lines[0])
        self.assertIn("tester · ✓ 접수됨 · 2026-10-03 12:34:56", header)

    def test_user_lines_are_right_aligned_after_cjk_wrapping(self):
        text = "한글과 English를 섞은 긴 사용자 메시지입니다. " * 4
        for width in (26, 60, 99):
            lines = render_messages((ChatMessage("user", text, "10:24"),), width, HubTheme())
            for line in lines:
                visible = "".join(fragment[1] for fragment in line)
                if visible.strip():
                    self.assertEqual(get_cwidth(visible), width - 2)

    def test_assistant_and_user_markdown(self):
        source = "### Heading\n**bold** and `inline`\n\n- item\n\n```python\nprint('hi')\n```\n\n| Key | Value |\n| --- | --- |\n| a | b |"
        lines = render_messages((ChatMessage("assistant", source),), 70, HubTheme())
        text = "\n".join("".join(part[1] for part in line) for line in lines)
        self.assertNotIn("###", text)
        self.assertNotIn("**", text)
        self.assertNotIn("```", text)
        self.assertIn("Heading", text)
        self.assertIn("print", text)
        self.assertIn("Key", text)
        self.assertTrue(any("bold" in style for line in lines for style, _ in line))
        literal = render_messages((ChatMessage("user", "**literal**"),), 70, HubTheme())
        self.assertNotIn("**literal**", "".join(text for line in literal for _, text in line))
        self.assertTrue(any("bold" in style and "literal" in text for line in literal for style, text in line))

    def test_terminal_background_overrides_host_and_rich_code_backgrounds(self):
        style = merge_styles([Style.from_dict({"": "bg:#ff0000"}), HubTheme().style()])
        lines = render_messages((ChatMessage("assistant", "`code`\n\n```python\nx = 1\n```"),),
                                60, HubTheme(comment="default"))
        for line in lines:
            for fragment_style, _ in line:
                attrs = style.get_attrs_for_style_str("class:hub " + fragment_style)
                self.assertIn(attrs.bgcolor, ("", "default"))
        dark = HubTheme.dark().style().get_attrs_for_style_str("class:hub.composer")
        self.assertEqual(dark.bgcolor, "111923")

    def test_resize_content_and_theme_invalidate_render_cache(self):
        messages = (ChatMessage("assistant", "**A response** " * 30),)
        control = ConversationControl(messages, HubTheme())
        with patch("hub.ui.chat.conversation.render_messages", wraps=render_messages) as render:
            wide = control.create_content(90, 20)
            control.create_content(90, 20)
            self.assertEqual(render.call_count, 1)
            narrow = control.create_content(30, 20)
            self.assertGreater(narrow.line_count, wide.line_count)
            control.theme = HubTheme.dark()
            control.create_content(30, 20)
            control.messages = (ChatMessage("assistant", "Updated"),)
            control.create_content(30, 20)
            self.assertEqual(render.call_count, 4)


if __name__ == "__main__":
    unittest.main()
