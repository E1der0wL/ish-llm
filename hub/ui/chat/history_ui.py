"""Session turn management and project execution activity dialogs."""

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import HSplit, VSplit
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Label, TextArea
from ...widget.reader import ReadOnlyDialog
from ...widget.summary import clipped_summary
import json

from ...widget.controls import Button, RadioList


def turn_label(row, index, t):
    summary = " ".join(row["text"].split())[:60]
    return f"{index + 1}. {summary} · {t.status(row['status'])}"


def provider_detail(row, t):
    """Display stored frames, without inventing unavailable exception messages."""
    value = row["details"]
    details = value.get("details", {})
    exceptions = details.get("exceptions", ()) if isinstance(details, dict) else ()
    lines = [t("activity_provider_" + row["scope"]), str(row["time"])]
    if row.get("log_path"):
        lines.append(t("activity_provider_file", path=row["log_path"]))
    if not isinstance(exceptions, (list, tuple)) or not exceptions:
        lines.append(json.dumps(value, ensure_ascii=False, indent=2))
        return "\n".join(lines)
    # Retain other diagnostic fields (stage, elapsed, attempt, chunk count, ...).
    summary = {**value, "details": {key: data for key, data in details.items() if key != "exceptions"}}
    lines.append(json.dumps(summary, ensure_ascii=False, indent=2))
    lines.append(t("activity_provider_traceback"))
    for exception in exceptions:
        if not isinstance(exception, dict):
            lines.append(str(exception))
            continue
        label = str(exception.get("type", "Exception"))
        if exception.get("status_code") is not None:
            label += f" · HTTP {exception['status_code']}"
        lines.append(label)
        for key, data in exception.items():
            if key not in ("type", "status_code", "frames"):
                lines.append(f"  {key}: {data}")
        for frame in exception.get("frames", ()):
            if isinstance(frame, dict):
                lines.append(f"  {frame.get('file', '?')}:{frame.get('line', '?')} · {frame.get('function', '?')}()")
    return "\n".join(lines)


def format_activity(rows, t):
    """Render the public activity contract, preserving its commit order."""
    levels = {"failed": "error", "error": "error", "critical": "error", "warning": "warning",
              "interrupted": "warning", "paused": "warning", "completed": "success"}
    blocks = []
    for row in rows:
        source = row["source"]
        lines = [f"{t.icons.alerts[levels.get(row['status'], 'info')]} {row['time']} · {t.status(row['status'])} · {row['event']}",
                 t("activity_source", session_id=source.get("session_id", "—"), run_id=source.get("run_id", "—"))]
        if row.get("provider"):
            lines.append(provider_detail(row, t))
        if source.get("step_id"):
            lines.append(t("activity_step", step_id=source["step_id"]))
        if row.get("code"):
            lines.append(t("activity_code", code=row["code"]))
        if row.get("provider_diagnostics"):
            lines.append(t("activity_provider_run_details"))
            lines.extend(provider_detail(record, t) for record in row["provider_diagnostics"])
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
        project_id = view.project_id
        if not rows:
            view.open_dialog(t("history_title"), Label(t("history_empty")))
            return
        popup_width = lambda: max(1, min(80, view._columns() - 4))
        # Reserve frame, dialog padding, radio marker and scrollbar cells.
        # Recompute on resize; CJK/emoji must be counted as terminal cells.
        choices = RadioList([(row["id"], lambda row=row, i=i: clipped_summary(
            turn_label(row, i, t), popup_width() - 11)) for i, row in enumerate(rows)], select_on_focus=True)
        choices.window.height = Dimension(min=1, max=max(1, min(8, view._rows() - 17)))
        by_id = {row["id"]: row for row in rows}
        def detail():
            row = by_id[choices.current_value]
            return "\n".join(clipped_summary(line, popup_width() - 6) for line in (
                f"{row['time']} · {row['engine']} · {t.elapsed(row['elapsed'])}",
                f"{t('history_response')}: {row['response']}", row['error']))
        def clone():
            boundary = choices.current_value
            view.close_dialog()
            view.session_dialog("clone", turns=rows, through_message_id=boundary)
        def delete():
            request_id = choices.current_value
            view.close_dialog()
            view.open_dialog(t("history_delete"), Label(t("settings_loading")))
            pending_dialog = view._dialog
            def ready(plan):
                if view._dialog is not pending_dialog:
                    return
                view.close_dialog()
                if (plan is None or view.project_id != project_id or not view.sessions
                        or view.sessions[view.selected].id != session_id):
                    return
                blockers = plan["blockers"]
                run_ids = [item["source"]["run_id"] for item in blockers]
                def confirmed():
                    if (view.project_id != project_id or not view.sessions
                            or view.sessions[view.selected].id != session_id):
                        return
                    def removed(result):
                        if result:
                            pending = view._submitted_messages.get((project_id, session_id), {})
                            pending.pop(request_id, None)
                            self.controller._call("snapshot", completed=self.controller._snapshot)
                            if view.project_id == project_id and view.sessions[view.selected].id == session_id:
                                self.open()
                    self.controller._call("delete_turn", session_id, request_id, run_ids,
                                          plan["revision"], completed=removed)
                if blockers:
                    rows = [f"{item['source']['run_id']} · {item['details']['engine']} · {t.status(item['details']['status'])}"
                            for item in blockers]
                    body = HSplit([Label(t("history_abandon_confirm", count=len(rows))),
                        TextArea(text="\n".join(rows), read_only=True, scrollbar=True,
                                 height=Dimension(min=2, max=8))], padding=1)
                else:
                    body = Label(t("history_delete_confirm"))
                view.open_dialog(t("history_delete"), body, confirmed,
                    accept_text=t("history_abandon_delete") if blockers else None)
            self.controller._call("delete_turn_plan", session_id, request_id, completed=ready)
        def cancel():
            request_id = choices.current_value
            if by_id[request_id]["status"] != "queued":
                view.notice = t("history_cancel_unavailable")
                return
            view.close_dialog()
            def confirmed():
                def cancelled(result):
                    if result:
                        view._submitted_messages.get((project_id, session_id), {}).pop(request_id, None)
                        view.preparing.finish((project_id, session_id))
                        self.controller._call("snapshot", completed=self.controller._snapshot)
                    # Re-read also on a promotion race; never interrupt that Run.
                    if view.project_id == project_id and view.sessions[view.selected].id == session_id:
                        self.open()
                self.controller._call("cancel_request", session_id, request_id, completed=cancelled)
            view.open_dialog(t("history_cancel"), Label(t("history_cancel_confirm")), confirmed)
        choices.control.hub_shortcuts.add(["d"], "d", "delete", lambda e: delete())
        choices.control.hub_shortcuts.add(["r"], "r", "clone", lambda e: clone())
        choices.control.hub_shortcuts.add(["c"], "c", "cancel", lambda e: cancel())
        details = Label(detail)
        details.window.height = 3
        body = HSplit([Label(lambda: clipped_summary(t("history_hint"), popup_width() - 6)), choices, details,
                       VSplit([Button(t("history_delete"), handler=delete, width=14),
                               Button(t("history_clone"), handler=clone, width=20),
                               Button(t("history_cancel"), handler=cancel, width=14)], padding=2)], padding=1,
                      width=lambda: Dimension.exact(max(1, popup_width() - 4)))
        view.open_dialog(t("history_title"), body, focus=choices,
                         width=lambda: Dimension.exact(popup_width()))
        view._dialog.shortcut_context = "history"

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
            self._activity_dialog(lambda: format_activity(rows, t))
        self.controller._call("project_activity", completed=ready)

    def _activity_dialog(self, text):
        view = self.view
        popup = ReadOnlyDialog(view.t("project_activity"), text, view._rows, view._columns,
                               view.close_dialog, view.t, render_worker=view.render_worker)
        self.activity_output = popup.output
        view.dialogs.show(popup, popup.receiver)
