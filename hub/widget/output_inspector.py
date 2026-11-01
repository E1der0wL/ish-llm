"""Full-screen output inspection with local search and lossless source access."""

from hub.widget.dialog import DialogContext


from hub.locales.keys import MessageKey

from dataclasses import dataclass
from typing import NamedTuple

from prompt_toolkit.application.current import get_app
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import ConditionalContainer, HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Frame, Box

from .clipboard import copy_text
from .controls import Button, TextArea
from .focus import focus_control
from .actions import ActionBar
from .dialog import PopupInput
from .output.output_viewport import OutputViewport
from .output.search_index import IndexedLines, SearchHighlight
from ..ui.input.registry import MOVEMENT_KEYS, ShortcutRegistry
from ..ui.input.keys import KeyName, ShortcutAction, ShortcutLabel
from ..config.theme_style import ThemeSelector
from .output.text_viewport import TextViewport


@dataclass(frozen=True, slots=True)
class SearchRows:
    rows: object
    query: str


class SearchCache(NamedTuple):
    key: tuple
    rows: object


class PreparedSearch(NamedTuple):
    base: object
    hits: tuple
    matches: dict


class SearchableOutputViewport(OutputViewport):
    EMPTY_QUERY = ""
    RENDERED = False
    NO_MATCH = -1
    FIRST_MATCH = 0
    ABSENT = None

    def __init__(self, item, output):
        super().__init__(item, output)
        self.query = SearchableOutputViewport.EMPTY_QUERY
        self.raw_mode = SearchableOutputViewport.RENDERED
        self.hits, self.matches, self.hit_index = (
            (),
            {},
            SearchableOutputViewport.NO_MATCH,
        )
        self._base_cache = SearchableOutputViewport.ABSENT

    def render_request(self, width):
        key, build = super().render_request(width)
        query, raw_mode = self.query, self.raw_mode
        base_key = key + (raw_mode,)

        def rows():
            # This cache is owned by the single render lane. Searching must not
            # re-run Rich or image conversion on every input character.
            if (
                self._base_cache is SearchableOutputViewport.ABSENT
                or self._base_cache.key != base_key
            ):
                value = (
                    [
                        [(TextViewport.EMPTY_STYLE, line)]
                        for line in self.item.raw.splitlines()
                    ]
                    if raw_mode
                    else build()
                )
                self._base_cache = SearchCache(base_key, value)
            return SearchRows(self._base_cache.rows, query)

        return base_key + (query,), rows

    def _prepare_rows_iter(self, value):
        # TextViewport's error fallback supplies literal rows, without a query.
        rows, query = (
            (value.rows, value.query)
            if isinstance(value, SearchRows)
            else (value, SearchableOutputViewport.EMPTY_QUERY)
        )
        base = yield from super()._prepare_rows_iter(rows)
        from .output.search_index import index_lines_iter
        hits, matches = (
            (yield from index_lines_iter(base.fragments, query)) if query else IndexedLines.empty()
        )
        return PreparedSearch(base, hits, matches)

    def _apply_rows(self, result):
        base, self.hits, self.matches = result
        super()._apply_rows(base)
        self.hit_index = (
            SearchableOutputViewport.FIRST_MATCH
            if self.hits
            else SearchableOutputViewport.NO_MATCH
        )
        self._jump()

    def _jump(self):
        if self.hit_index >= SearchableOutputViewport.FIRST_MATCH:
            row, start, _ = self.hits[self.hit_index]
            self.top_line = row
            self.left_column = TextViewport.FIRST_CELL
            if self.raw_mode:
                from prompt_toolkit.utils import get_cwidth

                self.left_column = get_cwidth(self.lines[row][:start])
            self._tail = TextViewport.STOP_FOLLOWING

    def move_match(self, direction):
        if self.hits:
            self.hit_index = (self.hit_index + direction) % len(self.hits)
            self._jump()

    def create_content(self, width, height):
        content = super().create_content(width, height)
        original = content.get_line

        def highlighted(row):
            spans = self.matches.get(row, ())
            if not spans:
                return original(row)
            return SearchHighlight.apply(original(row), spans, lambda: self.hit_index)

        content.get_line = highlighted
        return content


class ExpandedOutputDialog:
    """Shared full-screen shell for locally registered interactive widgets."""

    ABSENT = None
    EMPTY = ""
    SEARCHING = True
    NOT_SEARCHING = False
    MULTILINE = False
    MODAL = True
    ROW_HEIGHT = 1
    KEY_RECEIVER_HEIGHT = 0
    SPACE = " "
    ACTION_INSET = 4
    MIN_DIMENSION = 1
    ACCEPT_QUERY = False
    CANCEL_QUERY = True

    shortcut_context = DialogContext.READER

    def __init__(self, item, context, output, *, body=ABSENT):
        self.item, self.context, self.output = item, context, output
        self.t = context.language
        self.notice = ExpandedOutputDialog.EMPTY
        self.searching = ExpandedOutputDialog.NOT_SEARCHING
        self._before_query = ExpandedOutputDialog.EMPTY
        self.query = TextArea(
            multiline=ExpandedOutputDialog.MULTILINE,
            height=ExpandedOutputDialog.ROW_HEIGHT,
            prompt=lambda: self.t(MessageKey.OBJECT_QUERY) + ExpandedOutputDialog.SPACE,
        )
        self.query.buffer.on_text_changed += lambda _: self.changed_query(
            self.query.text
        )
        navigation = PopupInput()
        self.shortcuts = registry = navigation.shortcuts
        self.receiver = navigation.receiver
        registry.add(
            [KeyName.ESCAPE],
            ShortcutLabel.ESCAPE,
            ShortcutAction.CLOSE,
            lambda e: context.close(),
        )
        query_keys = ShortcutRegistry()
        query_keys.add(
            [KeyName.ENTER, KeyName.TAB],
            ShortcutLabel.ENTER_TAB,
            ShortcutAction.CONFIRM,
            lambda e: self.finish_query(),
        )
        query_keys.add(
            [KeyName.ESCAPE],
            ShortcutLabel.ESCAPE,
            ShortcutAction.CANCEL,
            lambda e: self.finish_query(cancel=ExpandedOutputDialog.CANCEL_QUERY),
        )
        self.query.control.key_bindings = query_keys.bindings
        query_keys.attach(self.query.control)
        self.query.control.modal = ExpandedOutputDialog.MODAL
        self.buttons = [
            Button(self.t(MessageKey.SHORTCUT_FIND), handler=self.start_query),
            Button(
                self.t(MessageKey.SHORTCUT_COPY),
                handler=lambda: get_app().create_background_task(self.copy()),
            ),
            Button(self.t(MessageKey.SEARCH_CLOSE), handler=context.close),
        ]
        self.actions = ActionBar(
            self.buttons,
            lambda: max(
                ExpandedOutputDialog.MIN_DIMENSION,
                context.columns() - ExpandedOutputDialog.ACTION_INSET,
            ),
            equal_width=True,
        )
        self.status_bar = (
            context.status_bar(self.status, surface=self)
            if context.status_bar else
            Window(FormattedTextControl(self.status), height=self.ROW_HEIGHT,
                   style=ThemeSelector.NOTICE.style)
        )
        self.container = Frame(
            HSplit(
                [
                    body if body is not ExpandedOutputDialog.ABSENT else output,
                    ConditionalContainer(self.query, Condition(lambda: self.searching)),
                    Window(height=self.ROW_HEIGHT),
                    self.actions,
                    Window(height=self.ROW_HEIGHT),
                    Box(self.status_bar, padding=0, padding_left=1, padding_right=1,
                        height=self.ROW_HEIGHT),
                    Window(
                        self.receiver, height=ExpandedOutputDialog.KEY_RECEIVER_HEIGHT
                    ),
                ]
            ),
            title=item.title,
            width=lambda: Dimension(
                min=ExpandedOutputDialog.MIN_DIMENSION,
                preferred=context.columns(),
                max=context.columns(),
            ),
            height=lambda: Dimension(
                min=ExpandedOutputDialog.MIN_DIMENSION,
                preferred=context.rows(),
                max=context.rows(),
            ),
            style=ThemeSelector.DIALOG.style,
        )

    def __pt_container__(self):
        return self.container

    def start_query(self):
        self._before_query = self.query.text
        self.searching = ExpandedOutputDialog.SEARCHING
        focus_control(self.query)

    def finish_query(self, cancel=ACCEPT_QUERY):
        if cancel:
            self.query.text = self._before_query
        self.searching = ExpandedOutputDialog.NOT_SEARCHING
        get_app().layout.focus(self.receiver)

    def changed_query(self, query):
        self.output.query = query
        get_app().invalidate()

    def status(self):
        return self.notice

    async def copy(self):
        try:
            await copy_text(self.selected_text() or self.item.raw)
        except (OSError, RuntimeError) as error:
            self.notice = self.t(MessageKey.ERROR, error=error)
        get_app().invalidate()

    def selected_text(self):
        return self.output.selection.text


class OutputInspector(ExpandedOutputDialog):
    NO_ERROR = ""
    RAW = True
    NEXT_MATCH = 1
    PREVIOUS_MATCH = -1
    RAW_ACTION_POSITION = 1
    ORIGIN = 0
    STATUS_FORMAT = "{mode} • {count}"
    NOTICE_FORMAT = " • {notice}"

    def __init__(self, item, context, *, error=NO_ERROR):
        super().__init__(item, context, context.viewport(item))
        if error:
            self.notice = self.t(MessageKey.OUTPUT_ERROR, error=error)
            self.output.raw_mode = OutputInspector.RAW
        self.shortcuts.add(
            MOVEMENT_KEYS,
            self.t(MessageKey.SHORTCUT_MOVEMENT),
            ShortcutAction.SCROLL,
            lambda e: self.output.scroll(e.key_sequence[-1].key),
        )
        self.actions.insert(1, Button(self.t(MessageKey.SEARCH_PREVIOUS),
                            handler=lambda: self.output.move_match(self.PREVIOUS_MATCH)))
        self.actions.insert(2, Button(self.t(MessageKey.SEARCH_NEXT),
                            handler=lambda: self.output.move_match(self.NEXT_MATCH)))
        self.actions.insert(
            OutputInspector.RAW_ACTION_POSITION,
            Button(self.t(MessageKey.OBJECT_RAW), handler=self.toggle_raw),
        )

    def toggle_raw(self):
        self.output.selection.clear()
        self.output.raw_mode = not self.output.raw_mode
        self.output.top_line = self.output.left_column = OutputInspector.ORIGIN

    def status(self):
        mode = self.t(
            MessageKey.OBJECT_RAW
            if self.output.raw_mode
            else MessageKey.OBJECT_RENDERED
        )
        count = self.t(
            MessageKey.SEARCH_COUNT,
            index=self.output.hit_index + 1,
            count=len(self.output.hits),
        )
        return OutputInspector.STATUS_FORMAT.format(mode=mode, count=count) + (
            OutputInspector.NOTICE_FORMAT.format(notice=self.notice)
            if self.notice
            else OutputInspector.NO_ERROR
        )
