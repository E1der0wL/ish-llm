"""Session turn management and project execution activity dialogs."""

from llm.core.models import RunStatus
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Label
from ...widget.controls import TextArea
from ...widget.output.reader import ReadOnlyDialog
import json

from ...widget.controls import Button
from ..error_presentation import operation_notice


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

    RETRY_TURN = "retry_turn"

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
        from ...widget.dialog import Dialog
        from ...widget.forms.request_history import RequestHistoryDialog
        view, t = self.view, self.view.t
        project_id = view.project_id
        if not rows:
            button = Button(t("history_close"), handler=view.close_dialog)
            popup = Dialog(title=t("history_title"), body=Label(t("history_empty")), buttons=[button])
            view.dialogs.show(popup, button)
            return

        def current():
            return (view.project_id == project_id and bool(view.sessions)
                    and view.sessions[view.selected].id == session_id)

        def execute(operation):
            identifiers = popup.selected()
            if len(identifiers) != 1:
                view.notice = t("history_select_one")
                return
            identifier = identifiers[0]
            row = popup.rows[identifier]
            text = popup.drafts[identifier]
            if operation == "edit_turn" and not text.strip():
                view.notice = t("empty_message")
                return
            view.close_dialog()
            if operation == HistoryUI.RETRY_TURN:
                from .resume import resume_turn
                candidates = [run for run in row["runs"] if run["run_id"]
                              and run["status"] in (RunStatus.INTERRUPTED, RunStatus.FAILED)]
                if not candidates:
                    view.notice = t("history_retry_unavailable")
                elif len(candidates) == 1:
                    resume_turn(self.controller, session_id, candidates[0]["run_id"], identifier)
                else:
                    from ...widget.controls import RadioList
                    choices = RadioList([(run["run_id"], run["agent_id"] + " · " + t.status(run["status"]))
                                         for run in candidates], select_on_focus=True)
                    view.open_dialog(t("choose_target"), choices,
                        lambda: resume_turn(self.controller, session_id, choices.current_value, identifier), choices)
                return
            args = (session_id, identifier, text, row.get("request_id", identifier))
            self.controller._call(operation, *args, completed=lambda result:
                result is not None and self.controller._call("snapshot", completed=self.controller._snapshot))

        def delete():
            identifiers = popup.selected()
            if not identifiers:
                view.notice = t("history_select_items")
                return
            view.close_dialog()
            view.open_dialog(t("history_delete"), Label(t("settings_loading")))
            pending = view._dialog
            def ready(plan):
                if view._dialog is not pending or not current():
                    return
                view.close_dialog()
                if plan is None:
                    return
                def confirmed():
                    if not current():
                        return
                    key = (project_id, session_id)
                    if key in view.deleting.pending:
                        return
                    request = view.deleting.start(key, len(plan.get("items", ())))
                    def removed(result):
                        if result is None:
                            def failed_refresh(snapshot):
                                self.controller._snapshot(snapshot)
                                view.deleting.finish(request)
                            self.controller._call("snapshot", completed=failed_refresh)
                            return
                        from dataclasses import replace
                        deleted = set(result["removed"])
                        submitted = getattr(view, "_submitted_messages", {}).get(key, {})
                        for identifier in deleted:
                            submitted.pop(identifier, None)
                        if view.project_id == project_id:
                            texts = {row["text"] for row in rows
                                     if row["id"] in deleted or row.get("request_id") in deleted}
                            view.input_history.discard(session_id, texts)
                            # Forget locally bridged receipts and cached turns before
                            # the next authoritative snapshot, including after errors.
                            for index, session in enumerate(view.sessions):
                                if session.id != session_id:
                                    continue
                                kept, removing = [], False
                                for message in session.messages:
                                    if message.role == "user":
                                        removing = message.id in deleted
                                    if not removing:
                                        kept.append(message)
                                view.sessions[index] = replace(session, messages=tuple(kept))
                        def refreshed(snapshot):
                            self.controller._snapshot(snapshot)
                            view.deleting.finish(request)
                            if current():
                                if view.visible and view._dialog is None:
                                    self.open()
                                if result["errors"]:
                                    for error in result["errors"]:
                                        view.operation_diagnostics.record(project_id, error)
                                    view.notice = "\n".join(dict.fromkeys(
                                        operation_notice(error, t) for error in result["errors"]))
                        self.controller._call("snapshot", completed=refreshed)
                    self.controller._call("delete_turns", session_id, identifiers, plan["revision"], completed=removed,
                        progress=lambda done, total: view.deleting.update(request, done, total))
                summary = t("history_delete_selected", count=len(identifiers))
                if any(not item["queued"] for item in plan.get("items", ())):
                    summary += "\n" + t("history_delete_confirm")
                if plan["blockers"]:
                    summary += "\n" + t("history_abandon_confirm", count=len(plan["blockers"]))
                    summary += "\n" + "\n".join(
                        f"{item['source']['run_id']} · {item['details']['engine']} · {t.status(item['details']['status'])}"
                        for item in plan["blockers"])
                body = TextArea(text=summary, read_only=True, scrollbar=True, wrap_lines=True,
                                height=Dimension(min=1, max=8))
                view.open_dialog(t("history_delete"), body, confirmed,
                    accept_text=t("history_abandon_delete") if plan["blockers"] else t("history_delete"))
            self.controller._call("delete_turns_plan", session_id, identifiers, completed=ready)

        popup = RequestHistoryDialog(view, rows, delete=delete, edit=lambda: execute("edit_turn"),
                                     retry=lambda: execute(HistoryUI.RETRY_TURN), close=view.close_dialog)
        view.dialogs.show(popup, popup.choices)

    def activity(self):
        view, t = self.view, self.view.t
        project_id = view.project_id
        self._activity_dialog(t("settings_loading"))
        pending = view._dialog
        def ready(rows):
            if view._dialog is not pending:
                return
            view.close_dialog()
            if view.project_id != project_id:
                return
            if rows is None and not view.operation_diagnostics.text(project_id, t):
                return
            self._activity_dialog(lambda: "\n\n".join(filter(None, (
                format_activity(rows, t) if rows is not None else "",
                view.operation_diagnostics.text(project_id, t)))))
        self.controller._call("project_activity", completed=ready)

    def _activity_dialog(self, text):
        view = self.view
        popup = ReadOnlyDialog(view.t("project_activity"), text, view._rows, view._columns,
                               view.close_dialog, view.t, render_worker=view.render_worker)
        view.dialogs.show(popup, popup.receiver)
