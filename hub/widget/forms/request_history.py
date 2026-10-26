"""Multi-selection request history, editable input and response preview."""
from hub.ui.input.registry import ShortcutRegistry


from ...backend.history_values import HistoryRow
from ...config.theme_style import ThemeSelector, ThemeStyle
from ...ui.input.keys import KeyName, ShortcutAction

from hub.locales.keys import MessageKey, MessagePattern

from prompt_toolkit.layout import HSplit
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Label

from ..controls import Button, CheckboxList, TextArea
from ..dialog import Dialog, PopupSize, PopupGeometry
from ..summary import clipped_summary, turn_label


class RequestHistoryDialog(Dialog):
    MIN_DIMENSION = 1
    VERTICAL_OVERHEAD = 17
    EDITOR_ROWS = 4
    RESPONSE_ROWS = 7
    CHOICE_ROWS = 6
    RESPONSE_DIVISOR = 3
    SUMMARY_MARGIN = 11
    LABEL_MARGIN = 6
    BUTTON_WIDTH = 9
    MULTILINE = True
    SCROLLBAR = True
    WRAP_LINES = True
    READ_ONLY = True
    RESPONSE_FOCUSABLE = False
    LINE = "\n"
    HEADING_STYLE = ThemeStyle.MODIFIER_SEPARATOR.join(
        (ThemeSelector.ACCENT.style, ThemeStyle.BOLD)
    )
    SHORTCUT_CONTEXT = "history"
    DETAILS = "{time} · {engine} · {elapsed} · {error}"

    def __init__(self, view, rows, *, delete, edit, retry, close):
        self.view, self.rows = view, {HistoryRow(row).id: row for row in rows}
        self.drafts = {HistoryRow(row).id: HistoryRow(row).text for row in rows}
        self.current = HistoryRow(rows[0]).id
        width = lambda: PopupGeometry.dimensions(PopupSize.LARGE)[0].preferred
        self.editor = TextArea(
            text=HistoryRow(rows[0]).text,
            multiline=self.MULTILINE,
            scrollbar=self.SCROLLBAR,
            wrap_lines=self.WRAP_LINES,
            height=lambda: Dimension(
                min=self.MIN_DIMENSION,
                preferred=max(
                    self.MIN_DIMENSION,
                    min(
                        self.EDITOR_ROWS,
                        (view._rows() - self.VERTICAL_OVERHEAD) // self.EDITOR_ROWS,
                    ),
                ),
            ),
        )
        self.editor.buffer.on_text_changed += lambda _: self.drafts.update(
            {self.current: self.editor.text}
        )
        self.response = TextArea(
            text=HistoryRow(rows[0]).response,
            read_only=self.READ_ONLY,
            focusable=self.RESPONSE_FOCUSABLE,
            scrollbar=self.SCROLLBAR,
            wrap_lines=self.WRAP_LINES,
            height=lambda: Dimension(
                min=self.MIN_DIMENSION,
                preferred=max(
                    self.MIN_DIMENSION,
                    min(
                        self.RESPONSE_ROWS,
                        (view._rows() - self.VERTICAL_OVERHEAD)
                        // self.RESPONSE_DIVISOR,
                    ),
                ),
            ),
        )
        self.choices = CheckboxList(
            [
                (
                    HistoryRow(row).id,
                    lambda row=row, i=i: clipped_summary(
                        turn_label(row, i, view.t), width() - self.SUMMARY_MARGIN
                    ),
                )
                for i, row in enumerate(rows)
            ],
            default_values=[],
            on_select=self.select,
        )
        self.choices.window.height = lambda: Dimension(
            min=self.MIN_DIMENSION,
            preferred=max(
                self.MIN_DIMENSION,
                min(
                    self.CHOICE_ROWS,
                    (view._rows() - self.VERTICAL_OVERHEAD) // self.RESPONSE_DIVISOR,
                ),
            ),
        )
        for key, action, callback in (
            (KeyName.CLEAR, ShortcutAction.DELETE, delete),
            (KeyName.EDIT, ShortcutAction.EDIT, edit),
            (KeyName.R, ShortcutAction.RETRY, retry),
        ):
            ShortcutRegistry.require(self.choices.control).add(
                [key], key, action, lambda e, callback=callback: callback()
            )
        body = HSplit(
            [
                Label(
                    self.LINE + view.t(MessageKey.HISTORY_REQUESTS),
                    style=self.HEADING_STYLE,
                ),
                Label(
                    lambda: clipped_summary(
                        view.t(MessageKey.HISTORY_HINT), width() - self.LABEL_MARGIN
                    )
                ),
                self.choices,
                Label(
                    self.LINE + view.t(MessageKey.HISTORY_USER_MESSAGE),
                    style=self.HEADING_STYLE,
                ),
                self.editor,
                Label(
                    self.LINE + view.t(MessageKey.HISTORY_RESPONSE),
                    style=self.HEADING_STYLE,
                ),
                Label(
                    lambda: clipped_summary(
                        self.details(), width() - self.LABEL_MARGIN
                    ),
                    style=ThemeSelector.MUTED.style,
                ),
                self.response,
            ],
        )
        buttons = [
            Button(
                view.t(MessagePattern.history(name)),
                handler=callback,
                width=self.BUTTON_WIDTH,
            )
            for name, callback in (
                (ShortcutAction.DELETE, delete),
                (ShortcutAction.EDIT, edit),
                (ShortcutAction.RETRY, retry),
                (ShortcutAction.CLOSE, close),
            )
        ]
        self.buttons = buttons
        super().__init__(
            title=view.t(MessageKey.HISTORY_TITLE),
            body=body,
            buttons=buttons,
            shortcut_context=self.SHORTCUT_CONTEXT,
            size=PopupSize.LARGE,
        )

    def select(self, identifier):
        if identifier == self.current:
            return
        self.current = identifier
        self.editor.text = self.drafts[identifier]
        self.response.text = HistoryRow(self.rows[identifier]).response

    def selected(self):
        return [
            identifier
            for identifier in self.rows
            if identifier in self.choices.current_values
        ]

    def details(self):
        row = HistoryRow(self.rows[self.current])
        return self.DETAILS.format(
            time=row.time,
            engine=row.engine,
            elapsed=self.view.t.elapsed(row.elapsed),
            error=row.error,
        )
