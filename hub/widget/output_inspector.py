"""Full-screen output inspection with local search and lossless source access."""

from dataclasses import dataclass

from prompt_toolkit.application.current import get_app
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import ConditionalContainer, HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Frame

from .clipboard import copy_text
from .controls import Button, TextArea
from .actions import ActionBar
from .dialog import PopupInput
from .output.output_viewport import OutputViewport
from .output.search_index import index_lines
from ..ui.input.registry import MOVEMENT_KEYS, ShortcutRegistry, legend


@dataclass(frozen=True, slots=True)
class SearchRows:
    rows: object
    query: str


class SearchableOutputViewport(OutputViewport):
    def __init__(self, item, output):
        super().__init__(item, output)
        self.query = ""
        self.raw_mode = False
        self.hits, self.matches, self.hit_index = (), {}, -1
        self._base_cache = None

    def render_request(self, width):
        key, build = super().render_request(width)
        query, raw_mode = self.query, self.raw_mode
        base_key = key + (raw_mode,)
        def rows():
            # This cache is owned by the single render lane. Searching must not
            # re-run Rich or image conversion on every input character.
            if self._base_cache is None or self._base_cache[0] != base_key:
                value = [[("", line)] for line in self.item.raw.splitlines()] if raw_mode else build()
                self._base_cache = base_key, value
            return SearchRows(self._base_cache[1], query)
        return base_key + (query,), rows

    def _prepare_rows(self, value):
        # TextViewport's error fallback supplies literal rows, without a query.
        rows, query = (value.rows, value.query) if isinstance(value, SearchRows) else (value, "")
        base = super()._prepare_rows(rows)
        hits, matches = index_lines(base[0], query) if query else ((), {})
        return base, hits, matches

    def _apply_rows(self, result):
        base, self.hits, self.matches = result
        super()._apply_rows(base)
        self.hit_index = 0 if self.hits else -1
        self._jump()

    def _jump(self):
        if self.hit_index >= 0:
            row, start, _ = self.hits[self.hit_index]
            self.top_line = row
            self.left_column = 0
            if self.raw_mode:
                from prompt_toolkit.utils import get_cwidth
                self.left_column = get_cwidth(self.lines[row][:start])
            self._tail = False

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
            result, offset = [], 0
            for style, text in original(row):
                end = offset + len(text)
                cursor = offset
                for index, left, right in spans:
                    left, right = max(offset, left), min(end, right)
                    if left >= right:
                        continue
                    if cursor < left:
                        result.append((style, text[cursor - offset:left - offset]))
                    selected = "current" if index == self.hit_index else "match"
                    result.append((style + " class:hub.search." + selected, text[left - offset:right - offset]))
                    cursor = right
                if cursor < end:
                    result.append((style, text[cursor - offset:]))
                offset = end
            return result
        content.get_line = highlighted
        return content


class ExpandedOutputDialog:
    """Shared full-screen shell for locally registered interactive widgets."""

    shortcut_context = "reader"

    def __init__(self, item, context, output, *, body=None):
        self.item, self.context, self.output = item, context, output
        self.t = context.language
        self.notice = ""
        self.searching = False
        self._before_query = ""
        self.query = TextArea(multiline=False, height=1, prompt=lambda: self.t("object_query") + " ")
        self.query.buffer.on_text_changed += lambda _: self.changed_query(self.query.text)
        navigation = PopupInput()
        self.shortcuts = registry = navigation.shortcuts
        self.receiver = navigation.receiver
        registry.add(["escape", "c-t"], "ESC/Ctrl+T", "close", lambda e: context.close())
        registry.add(["c-f", "/"], "Ctrl+F / /", "find", lambda e: self.start_query())
        registry.add(["c"], "c", "copy", lambda e: e.app.create_background_task(self.copy()))
        query_keys = ShortcutRegistry()
        query_keys.add(["enter", "tab"], "Enter/Tab", "confirm", lambda e: self.finish_query())
        query_keys.add(["escape"], "ESC", "cancel", lambda e: self.finish_query(cancel=True))
        query_keys.add(["c-t"], "Ctrl+T", "close", lambda e: context.close())
        self.query.control.key_bindings = query_keys.bindings
        self.query.control.hub_shortcuts = query_keys
        self.query.control.modal = True
        self.buttons = [
            Button(self.t("shortcut_find"), handler=self.start_query),
            Button(self.t("shortcut_copy"), handler=lambda: get_app().create_background_task(self.copy())),
            Button(self.t("search_close"), handler=context.close),
        ]
        self.actions = ActionBar(self.buttons, lambda: max(1, context.columns() - 4))
        self.container = Frame(HSplit([
            body if body is not None else output,
            ConditionalContainer(self.query, Condition(lambda: self.searching)),
            Window(FormattedTextControl(self.status), height=1, style="class:hub.muted"),
            Window(FormattedTextControl(self.hints), height=1, style="class:hub.footer"),
            self.actions,
            Window(self.receiver, height=0),
        ]), title=item.title,
            width=lambda: Dimension(min=1, preferred=context.columns(), max=context.columns()),
            height=lambda: Dimension(min=1, preferred=context.rows(), max=context.rows()),
            style="class:dialog")

    def __pt_container__(self):
        return self.container

    def hints(self):
        local = getattr(get_app().layout.current_control, "hub_shortcuts", self.shortcuts)
        return "  ".join(f"{key} {action}" for key, action in legend(local.active(), self.t))

    def start_query(self):
        self._before_query = self.query.text
        self.searching = True
        get_app().layout.focus(self.query)

    def finish_query(self, cancel=False):
        if cancel:
            self.query.text = self._before_query
        self.searching = False
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
            self.notice = self.t("error", error=error)
        get_app().invalidate()

    def selected_text(self):
        return self.output.selection.text


class OutputInspector(ExpandedOutputDialog):
    def __init__(self, item, context, *, error=""):
        super().__init__(item, context, context.viewport(item))
        if error:
            self.notice = self.t("output_error", error=error)
            self.output.raw_mode = True
        self.shortcuts.add(MOVEMENT_KEYS, self.t("shortcut_movement"), "scroll",
                           lambda e: self.output.scroll(e.key_sequence[-1].key))
        self.shortcuts.add(["n", "c-n"], "n/Ctrl+N", "next_match", lambda e: self.output.move_match(1))
        self.shortcuts.add(["N", "c-p"], "N/Ctrl+P", "previous_match", lambda e: self.output.move_match(-1))
        self.shortcuts.add(["r"], "r", "object_raw", lambda e: self.toggle_raw())
        self.actions.insert(1, Button(self.t("object_raw"), handler=self.toggle_raw))

    def toggle_raw(self):
        self.output.selection.clear()
        self.output.raw_mode = not self.output.raw_mode
        self.output.top_line = self.output.left_column = 0

    def status(self):
        mode = self.t("object_raw" if self.output.raw_mode else "object_rendered")
        count = self.t("search_count", index=self.output.hit_index + 1, count=len(self.output.hits))
        return f"{mode} · {count}" + (f" · {self.notice}" if self.notice else "")
