"""Multi-selection request history, editable input and response preview."""

from hub.locales.keys import MessageKey, MessagePattern

from prompt_toolkit.layout import HSplit
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Label

from ..controls import Button, CheckboxList, TextArea
from ..dialog import Dialog
from ..summary import clipped_summary, turn_label


class RequestHistoryDialog(Dialog):
    def __init__(self, view, rows, *, delete, edit, retry, close):
        self.view, self.rows = view, {row["id"]: row for row in rows}
        self.drafts = {row["id"]: row["text"] for row in rows}
        self.current = rows[0]["id"]
        width = lambda: max(1, min(80, view._columns() - 4))
        self.editor = TextArea(text=rows[0]["text"], multiline=True, scrollbar=True, wrap_lines=True,
                               height=lambda: Dimension(min=1, preferred=max(1, min(4, (view._rows() - 17) // 4))))
        self.editor.buffer.on_text_changed += lambda _: self.drafts.update({self.current: self.editor.text})
        self.response = TextArea(text=rows[0]["response"], read_only=True, focusable=False, scrollbar=True,
                                 wrap_lines=True, height=lambda: Dimension(min=1, preferred=max(1, min(7, (view._rows() - 17) // 3))))
        self.choices = CheckboxList([(row["id"], lambda row=row, i=i: clipped_summary(
            turn_label(row, i, view.t), width() - 11))
            for i, row in enumerate(rows)], default_values=[], on_select=self.select)
        self.choices.window.height = lambda: Dimension(min=1, preferred=max(1, min(6, (view._rows() - 17) // 3)))
        for key, action, callback in (("d", "delete", delete), ("e", "edit", edit), ("r", "retry", retry)):
            self.choices.control.hub_shortcuts.add([key], key, action, lambda e, callback=callback: callback())
        body = HSplit([
            Label("\n" + view.t(MessageKey.HISTORY_REQUESTS), style="class:hub.accent bold"),
            Label(lambda: clipped_summary(view.t(MessageKey.HISTORY_HINT), width() - 6)), self.choices,
            Label("\n" + view.t(MessageKey.HISTORY_USER_MESSAGE), style="class:hub.accent bold"), self.editor,
            Label("\n" + view.t(MessageKey.HISTORY_RESPONSE), style="class:hub.accent bold"),
            Label(lambda: clipped_summary(self.details(), width() - 6), style="class:hub.muted"), self.response,
        ], width=lambda: Dimension.exact(max(1, width() - 4)))
        buttons = [Button(view.t(MessagePattern.history(name)), handler=callback, width=9) for name, callback in (
            ("delete", delete), ("edit", edit), ("retry", retry), ("close", close))]
        self.buttons = buttons
        super().__init__(title=view.t(MessageKey.HISTORY_TITLE), body=body, buttons=buttons, shortcut_context="history",
                         width=lambda: Dimension.exact(width()))

    def select(self, identifier):
        if identifier == self.current:
            return
        self.current = identifier
        self.editor.text = self.drafts[identifier]
        self.response.text = self.rows[identifier]["response"]

    def selected(self):
        return [identifier for identifier in self.rows if identifier in self.choices.current_values]

    def details(self):
        row = self.rows[self.current]
        return f"{row['time']} · {row['engine']} · {self.view.t.elapsed(row['elapsed'])} · {row['error']}"
