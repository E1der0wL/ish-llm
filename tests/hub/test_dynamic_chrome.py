"""Translated labels fit their current allocation without leaking button chrome."""

import unittest

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app, set_app
from prompt_toolkit.input import DummyInput
from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.mouse_handlers import MouseHandlers
from prompt_toolkit.layout.screen import Screen, WritePosition
from prompt_toolkit.utils import get_cwidth

from examples.hub.preview import create_preview
from hub.config.theme import HubTheme
from hub.locales import Language
from hub.locales.keys import MessageKey
from hub.widget.actions import ActionBar
from hub.widget.controls import Button, IconButton
from hub.widget.auxiliary import AuxiliaryButton
from hub.widget.input_field import InputField
from hub.model import ChatMessage, ReasoningMessage, TraceItem
from hub.widget.dropdown import DropdownBox
from tests.hub.support import SizedOutput


class DynamicChromeTests(unittest.IsolatedAsyncioTestCase):
    def paint(self, container, width, height=1):
        get_app().render_counter += 1
        screen = Screen()
        container.write_to_screen(screen, MouseHandlers(), WritePosition(0, 0, width, height),
                                  '', True, None)
        screen.draw_all_floats()
        return screen

    def text(self, screen, rect):
        return ''.join(screen.data_buffer[rect.ypos][x].char
                       for x in range(rect.xpos, rect.xpos + rect.width))

    def test_icon_width_stays_fixed_beside_flexible_content_and_unicode_changes(self):
        caption = ['›']
        button = IconButton(lambda: caption[0], handler=lambda: None, width=3)
        bar = VSplit([Window(), button])
        app = Application(layout=Layout(bar), input=DummyInput(), output=SizedOutput())
        with set_app(app):
            for glyph in ('›', '▶', '🔎'):
                caption[0] = glyph
                for width in (12, 50, 100):
                    screen = self.paint(bar, width)
                    rect = screen.visible_windows_to_write_positions[button.window]
                    self.assertEqual((rect.xpos, rect.width), (width - 3, 3))
                    self.assertEqual(self.text(screen, rect).strip(), glyph)

    async def test_input_action_uses_only_its_translated_caption_width(self):
        caption = ['가져오기']
        field = InputField(button_text=lambda: caption[0], button_width=1)
        app = Application(layout=Layout(field), input=DummyInput(), output=SizedOutput())
        with set_app(app):
            for label in ('가져오기', 'Import selected document', 'Add'):
                caption[0] = label
                screen = self.paint(field, 90)
                rect = screen.visible_windows_to_write_positions[field.button.window]
                self.assertEqual(rect.width, get_cwidth(label) + 2)
                self.assertEqual(self.text(screen, rect).strip(), label)

    async def test_home_body_and_sidebars_share_allocated_width_without_extra_indent(self):
        theme = HubTheme(background='#101112', foreground='#dedede', mode='dark')
        for columns in (80, 124):
            app, view, _ = create_preview(input=DummyInput(), output=SizedOutput(columns, 40), theme=theme)
            try:
                view.output.transcript.control.render_worker = None
                view.output.set_messages((
                    ChatMessage('user', 'User request', id='u', status='completed'),
                    ReasoningMessage.create('a', '', author='Reviewer', trace=(
                        TraceItem.thought('r', 'Inspect existing files.'),
                        TraceItem('t', TraceItem.TOOL, 'file_read', 'completed'),)),
                    ChatMessage('assistant', 'Response paragraph.\n\n---\n\n```python\nprint(1)\n```',
                                id='a', author='Reviewer', status='streaming'),
                ))
                with set_app(app):
                    view.show(app)
                    app.layout.focus(view.composer)
                    for sidebar in (None, view.output.tags, view.output.participants):
                        if sidebar is not None:
                            sidebar.open()
                        app.render_counter += 1
                        app.renderer.render(app, app.layout)
                        screen = app.renderer._last_screen
                        rects = screen.visible_windows_to_write_positions
                        transcript = rects[view.output.transcript.window]
                        composer = rects[view.composer.window]
                        heading = rects[view.output.header]
                        self.assertEqual(transcript.xpos, composer.xpos)
                        self.assertEqual(heading.xpos, composer.xpos)
                        self.assertFalse(view.output.heading().startswith(' '))
                        control = view.output.transcript.control
                        self.assertEqual(control._width, transcript.width - 2)
                        rows = [''.join(fragment[1] for fragment in row) for row in control._lines]
                        for caption in ('Reviewer', 'Inspect existing files.', 'Response paragraph.'):
                            self.assertTrue(any(row.startswith(caption) for row in rows), caption)
                        self.assertTrue(any('file_read' in row and not row.startswith(' ') for row in rows))
                        for y in range(transcript.ypos, transcript.ypos + transcript.height):
                            self.assertEqual(screen.data_buffer[y][transcript.xpos + transcript.width - 2].char, ' ')
                        for button in (view.header.restart, view.header.minimize):
                            self.assertEqual(rects[button.window].width, 3)
                        if sidebar is not None:
                            self.assertEqual(rects[sidebar.collapse.window].width, 3)
                        if sidebar is view.output.participants:
                            self.assertGreater(rects[sidebar.add_button.window].width, 3)
                        # Auxiliary actions retain the global surface, not the widget palette.
                        for button in (*view.auxiliary.buttons.values(), *view.composer_actions.buttons):
                            rect = rects[button.window]
                            self.assertEqual(rect.width, AuxiliaryButton.WIDTH)
                            attrs = app._merged_style.get_attrs_for_style_str(screen.data_buffer[rect.ypos][rect.xpos].style)
                            self.assertEqual(attrs.bgcolor, '101112')
            finally:
                view.close()

    def test_translated_button_group_grows_shrinks_and_clips_in_current_frame(self):
        labels = ['Save', 'Cancel']
        buttons = [Button(lambda i=i: labels[i], width=1) for i in range(2)]
        bar = ActionBar(buttons, equal_width=True)
        app = Application(layout=Layout(HSplit([bar, Window()])), input=DummyInput(),
                          output=SizedOutput())
        with set_app(app):
            for captions, width in ((['Save', 'Cancel'], 60),
                                    (['변경 사항 저장', '취소'], 60),
                                    (['Save all project changes', 'Cancel editing'], 23),
                                    (['OK', 'No'], 60)):
                labels[:] = captions
                # Measurement may happen before allocation in the same frame.
                bar.preferred_height(width, 10)
                screen = self.paint(bar, width)
                rects = [screen.visible_windows_to_write_positions[b.window] for b in buttons]
                self.assertEqual(rects[0].width, rects[1].width)
                self.assertEqual(rects[0].ypos, rects[1].ypos)
                self.assertLessEqual(abs(rects[0].xpos - (width - rects[-1].xpos - rects[-1].width)), 1)
                for caption, rect in zip(captions, rects):
                    rendered = self.text(screen, rect)
                    self.assertTrue(rendered.startswith(' ') and rendered.endswith(' '))
                    if get_cwidth(caption) + 2 <= rect.width:
                        self.assertEqual(rendered.strip(), caption)
                    else:
                        self.assertTrue(rendered.strip().endswith('…'), rendered)
                    self.assertLessEqual(get_cwidth(rendered), rect.width)

    def test_dropdown_selected_label_resizes_and_indicator_survives_clipping(self):
        dropdown = DropdownBox([('short', '짧게'), ('long', 'A much longer translated option')],
                               value='short', width=1)
        app = Application(layout=Layout(dropdown), input=DummyInput(), output=SizedOutput())
        with set_app(app):
            for value, width in (('short', 40), ('long', 40), ('long', 14), ('short', 40)):
                dropdown.value = value
                natural = get_cwidth(dropdown.caption() + dropdown.indicator()) + 2
                self.assertEqual(dropdown.width, natural)
                screen = self.paint(dropdown, min(width, natural))
                rect = screen.visible_windows_to_write_positions[dropdown.button.window]
                caption = self.text(screen, rect).strip()
                self.assertTrue(caption.endswith(dropdown.indicator().strip()))
                self.assertEqual(dropdown.floats[0].get_width(), rect.width)
                if natural > width:
                    self.assertIn('…', caption)
                else:
                    self.assertEqual(caption, dropdown.caption() + dropdown.indicator())

    async def test_recipient_title_padding_uses_global_background_at_every_width(self):
        theme = HubTheme(background='#101112', foreground='#dedede', mode='dark')
        app, view, _ = create_preview(input=DummyInput(), output=SizedOutput(116, 38), theme=theme)
        self.addCleanup(view.close)
        view.participant_ids = ('recipient',)
        with set_app(app):
            view.show(app)
            app.layout.focus(view.composer)
            widths = []
            for name in ('봇', '문서를 분석하고 검토하는 에이전트', '긴 수신 대상 ' * 20, '봇'):
                view.agent_titles = {'recipient': name}
                view.select_recipient('recipient')
                app.render_counter += 1
                app.renderer.render(app, app.layout)
                screen = app.renderer._last_screen
                rect = screen.visible_windows_to_write_positions[view.recipient_dropdown.button.window]
                widths.append(rect.width)
                text = self.text(screen, rect)
                for x in range(rect.xpos, rect.xpos + rect.width):
                    attrs = app._merged_style.get_attrs_for_style_str(screen.data_buffer[rect.ypos][x].style)
                    self.assertEqual(attrs.bgcolor, '101112')
                if '…' not in text:
                    self.assertEqual(rect.width, get_cwidth(view.recipient_title()) + 2)
                else:
                    self.assertTrue(text.strip().endswith('…'))
                self.assertIs(app.layout.current_control, view.composer.control)
            self.assertLess(widths[0], widths[1])
            self.assertLess(widths[1], widths[2])
            self.assertEqual(widths[0], widths[-1])

    def test_english_category_and_shortcut_labels_use_sentence_case(self):
        language = Language('en')
        self.assertEqual(language(MessageKey.SETTINGS_GLOBAL), 'Global')
        self.assertIn('Sessions', language(MessageKey.SESSIONS))
        for name, key in vars(MessageKey).items():
            if name.startswith('SHORTCUT_'):
                label = language(key)
                if label and label[0].isalpha():
                    self.assertTrue(label[0].isupper(), (key, label))
