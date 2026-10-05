"""Session turn management and project execution activity dialogs."""

from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import HSplit, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Dialog, Label

from ...asset import icon
from ..widgets import Button, RadioList
from ..text_viewport import TextViewport


def turn_label(row, index, t):
    summary = " ".join(row["text"].split())[:60]
    return f"{index + 1}. {summary} · {t.status(row['status'])}"


def format_activity(rows, t):
    """Render the public activity contract, preserving its commit order."""
    levels = {"failed": "error", "interrupted": "warning", "paused": "warning", "completed": "success"}
    blocks = []
    for row in rows:
        source = row["source"]
        lines = [f"{icon.ALERT[levels.get(row['status'], 'info')]} {row['time']} · {t.status(row['status'])} · {row['event']}",
                 t("activity_source", session_id=source["session_id"], run_id=source["run_id"])]
        if source.get("step_id"):
            lines.append(t("activity_step", step_id=source["step_id"]))
        if row.get("code"):
            lines.append(t("activity_code", code=row["code"]))
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) or t("activity_empty")


class HistoryUI:
    def __init__(self, controller):
        self.controller = controller
        self.view = controller.view

    def open(self):
        view = self.view
        project_id = view.project_id
        session_id = view.sessions[view.selected].id
        view.open_dialog(view.t("history_title"), Label(view.t("settings_loading")))
        pending = view._dialog
        def ready(rows):
            if view._dialog is not pending:
                return
            view.close_dialog()
            if rows is None or view.project_id != project_id or view.sessions[view.selected].id != session_id:
                return
            self.show(session_id, rows)
        self.controller._call("turns", session_id, completed=ready)

    def show(self, session_id, rows):
        view, t = self.view, self.view.t
        if not rows:
            view.open_dialog(t("history_title"), Label(t("history_empty")))
            return
        choices = RadioList([(row["id"], turn_label(row, i, t)) for i, row in enumerate(rows)], select_on_focus=True)
        choices.window.height = Dimension(min=1, max=max(1, min(8, view._rows() - 17)))
        by_id = {row["id"]: row for row in rows}
        def detail():
            row = by_id[choices.current_value]
            return (f"{row['time']} · {row['engine']} · {t.elapsed(row['elapsed'])}\n"
                    f"{t('history_response')}: {' '.join(row['response'].split())[:160]}\n"
                    f"{row['error']}")
        def clone():
            boundary = choices.current_value
            view.close_dialog()
            view.session_dialog("clone", turns=rows, through_message_id=boundary)
        def delete():
            request_id = choices.current_value
            view.close_dialog()
            def confirmed():
                def removed(result):
                    if result:
                        self.controller._call("snapshot", completed=self.controller._snapshot)
                        self.open()
                self.controller._call("delete_turn", session_id, request_id, completed=removed)
            view.open_dialog(t("history_delete"), Label(t("history_delete_confirm")), confirmed)
        keys = KeyBindings()
        @keys.add("d", eager=True)
        def delete_key(event):
            delete()
        @keys.add("r", eager=True)
        def clone_key(event):
            clone()
        choices.control.key_bindings = merge_key_bindings([choices.control.key_bindings, keys])
        body = HSplit([Label(t("history_hint")), choices, Label(detail),
                       VSplit([Button(t("history_delete"), handler=delete, width=14),
                               Button(t("history_clone"), handler=clone, width=20)], padding=2)], padding=1)
        view.open_dialog(t("history_title"), body, focus=choices)

    def activity(self):
        view, t = self.view, self.view.t
        project_id = view.project_id
        self._activity_dialog(t("settings_loading"))
        pending = view._dialog
        def ready(rows):
            if view._dialog is not pending:
                return
            view.close_dialog()
            if rows is None or view.project_id != project_id:
                return
            text = format_activity(rows, t)
            self._activity_dialog(text)
        self.controller._call("project_activity", completed=ready)

    def _activity_dialog(self, text):
        view = self.view
        # Prefer a substantial viewport even for short/loading logs. Recompute
        # on each paint so an open dialog follows terminal resizes.
        output = self.activity_output = TextViewport(text,
            height=lambda: Dimension.exact(max(1, min(30, view._rows() - 8))))
        keys = KeyBindings()
        @keys.add(Keys.Any)
        def ignore(event):
            pass
        @keys.add("enter", eager=True)
        @keys.add("escape")
        @keys.add("c-l", eager=True)
        def close(event):
            view.close_dialog()
        for key in ("up", "down", "left", "right", "pageup", "pagedown", "home", "end"):
            @keys.add("escape", key, eager=True)
            def scroll(event):
                output.scroll(event.key_sequence[-1].key)
                event.app.invalidate()
        # Only this invisible key receiver is focusable; the output and dialog
        # have no focusable buttons or fields, and shell/composer input is isolated.
        receiver = FormattedTextControl("", focusable=True, show_cursor=False, key_bindings=keys, modal=True)
        view._dialog_focus = get_app().layout.current_control
        view._dialog = Dialog(title=view.t("project_activity"),
            body=HSplit([Label(view.t("activity_hint")), output, Window(receiver, height=0)]),
            buttons=[], width=lambda: Dimension(preferred=100, max=max(1, view._columns() - 4)),
            with_background=False, modal=False)
        get_app().layout.focus(receiver)
        get_app().invalidate()
