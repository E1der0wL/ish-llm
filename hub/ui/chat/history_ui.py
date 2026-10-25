"""Session turn management and project execution activity dialogs."""

from hub.locales.keys import MessageKey, MessagePattern

from llm.core.models import RunStatus, MessageRole
from ...backend.history_values import HistoryCommand, HistoryRow, HistoryRun
from ...backend.history_deletion import TurnDeletionItem, TurnDeletionReview, TurnDeletionResult
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
    lines = [t(MessagePattern.activity_provider(row['scope'])), str(row["time"])]
    if row.get("log_path"):
        lines.append(t(MessageKey.ACTIVITY_PROVIDER_FILE, path=row["log_path"]))
    if not isinstance(exceptions, (list, tuple)) or not exceptions:
        lines.append(json.dumps(value, ensure_ascii=False, indent=2))
        return "\n".join(lines)
    # Retain other diagnostic fields (stage, elapsed, attempt, chunk count, ...).
    summary = {**value, "details": {key: data for key, data in details.items() if key != "exceptions"}}
    lines.append(json.dumps(summary, ensure_ascii=False, indent=2))
    lines.append(t(MessageKey.ACTIVITY_PROVIDER_TRACEBACK))
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
                 t(MessageKey.ACTIVITY_SOURCE, session_id=source.get("session_id", "—"), run_id=source.get("run_id", "—"))]
        if row.get("provider"):
            lines.append(provider_detail(row, t))
        if source.get("step_id"):
            lines.append(t(MessageKey.ACTIVITY_STEP, step_id=source["step_id"]))
        if row.get("code"):
            lines.append(t(MessageKey.ACTIVITY_CODE, code=row["code"]))
        if row.get("provider_diagnostics"):
            lines.append(t(MessageKey.ACTIVITY_PROVIDER_RUN_DETAILS))
            lines.extend(provider_detail(record, t) for record in row["provider_diagnostics"])
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) or t(MessageKey.ACTIVITY_EMPTY)


class HistoryUI:

    RETRY_TURN = HistoryCommand.RETRY

    def __init__(self, controller):
        self.controller = controller
        self.view = controller.view

    def open(self):
        view = self.view
        project_id = view.project_id
        session_id = view.sessions[view.selected].id
        view.open_dialog(view.t(MessageKey.HISTORY_TITLE), Label(view.t(MessageKey.SETTINGS_LOADING)))
        pending = view._dialog
        def ready(rows):
            if view._dialog is not pending:
                return
            view.close_dialog()
            if rows is None or view.project_id != project_id or view.sessions[view.selected].id != session_id:
                return
            self.show(session_id, rows)
        self.controller._call(HistoryCommand.TURNS, session_id, completed=ready)

    def show(self, session_id, rows):
        from ...widget.dialog import Dialog
        from ...widget.forms.request_history import RequestHistoryDialog
        view, t = self.view, self.view.t
        project_id = view.project_id
        if not rows:
            button = Button(t(MessageKey.HISTORY_CLOSE), handler=view.close_dialog)
            popup = Dialog(title=t(MessageKey.HISTORY_TITLE), body=Label(t(MessageKey.HISTORY_EMPTY)), buttons=[button])
            view.dialogs.show(popup, button)
            return

        def current():
            return (view.project_id == project_id and bool(view.sessions)
                    and view.sessions[view.selected].id == session_id)

        def execute(operation):
            identifiers = popup.selected()
            if len(identifiers) != 1:
                view.notice = t(MessageKey.HISTORY_SELECT_ONE)
                return
            identifier = identifiers[0]
            row = popup.rows[identifier]
            text = popup.drafts[identifier]
            if operation == HistoryCommand.EDIT and not text.strip():
                view.notice = t(MessageKey.EMPTY_MESSAGE)
                return
            view.close_dialog()
            if operation == HistoryUI.RETRY_TURN:
                from .resume import resume_turn
                candidates = [run for run in HistoryRow(row).runs if HistoryRun(run).run_id
                              and HistoryRun(run).status in (RunStatus.INTERRUPTED, RunStatus.FAILED)]
                if not candidates:
                    view.notice = t(MessageKey.HISTORY_RETRY_UNAVAILABLE)
                elif len(candidates) == 1:
                    resume_turn(self.controller, session_id, HistoryRun(candidates[0]).run_id, identifier)
                else:
                    from ...widget.controls import RadioList
                    choices = RadioList([(HistoryRun(run).run_id, HistoryRun(run).agent_id + " · " + t.status(HistoryRun(run).status))
                                         for run in candidates], select_on_focus=True)
                    view.open_dialog(t(MessageKey.CHOOSE_TARGET), choices,
                        lambda: resume_turn(self.controller, session_id, choices.current_value, identifier), choices)
                return
            args = (session_id, identifier, text, HistoryRow(row).request_id(identifier))
            self.controller._call(operation, *args, completed=lambda result:
                result is not None and self.controller._call("snapshot", completed=self.controller._snapshot))

        def delete():
            identifiers = popup.selected()
            if not identifiers:
                view.notice = t(MessageKey.HISTORY_SELECT_ITEMS)
                return
            view.close_dialog()
            view.open_dialog(t(MessageKey.HISTORY_DELETE), Label(t(MessageKey.SETTINGS_LOADING)))
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
                    request = view.deleting.start(key, len(TurnDeletionReview(plan).optional_items()))
                    def removed(result):
                        if result is None:
                            def failed_refresh(snapshot):
                                self.controller._snapshot(snapshot)
                                view.deleting.finish(request)
                            self.controller._call("snapshot", completed=failed_refresh)
                            return
                        from dataclasses import replace
                        deleted = set(TurnDeletionResult(result).removed)
                        submitted = getattr(view, "_submitted_messages", {}).get(key, {})
                        for identifier in deleted:
                            submitted.pop(identifier, None)
                        if view.project_id == project_id:
                            texts = {HistoryRow(row).text for row in rows
                                     if HistoryRow(row).id in deleted or HistoryRow(row).request_id() in deleted}
                            view.input_history.discard(session_id, texts)
                            # Forget locally bridged receipts and cached turns before
                            # the next authoritative snapshot, including after errors.
                            for index, session in enumerate(view.sessions):
                                if session.id != session_id:
                                    continue
                                kept, removing = [], False
                                for message in session.messages:
                                    if message.role == MessageRole.USER:
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
                                if TurnDeletionResult(result).errors:
                                    for error in TurnDeletionResult(result).errors:
                                        view.operation_diagnostics.record(project_id, error)
                                    view.notice = "\n".join(dict.fromkeys(
                                        operation_notice(error, t) for error in TurnDeletionResult(result).errors))
                        self.controller._call("snapshot", completed=refreshed)
                    self.controller._call(HistoryCommand.DELETE_MANY, session_id, identifiers, TurnDeletionReview(plan).revision, completed=removed,
                        progress=lambda done, total: view.deleting.update(request, done, total))
                summary = t(MessageKey.HISTORY_DELETE_SELECTED, count=len(identifiers))
                if any(not TurnDeletionItem(item).queued for item in TurnDeletionReview(plan).optional_items()):
                    summary += "\n" + t(MessageKey.HISTORY_DELETE_CONFIRM)
                if TurnDeletionReview(plan).blockers:
                    summary += "\n" + t(MessageKey.HISTORY_ABANDON_CONFIRM, count=len(TurnDeletionReview(plan).blockers))
                    summary += "\n" + "\n".join(
                        f"{item['source']['run_id']} · {item['details']['engine']} · {t.status(item['details']['status'])}"
                        for item in TurnDeletionReview(plan).blockers)
                body = TextArea(text=summary, read_only=True, scrollbar=True, wrap_lines=True,
                                height=Dimension(min=1, max=8))
                view.open_dialog(t(MessageKey.HISTORY_DELETE), body, confirmed,
                    accept_text=t(MessageKey.HISTORY_ABANDON_DELETE) if TurnDeletionReview(plan).blockers else t(MessageKey.HISTORY_DELETE))
            self.controller._call(HistoryCommand.DELETE_PLAN, session_id, identifiers, completed=ready)

        popup = RequestHistoryDialog(view, rows, delete=delete, edit=lambda: execute(HistoryCommand.EDIT),
                                     retry=lambda: execute(HistoryUI.RETRY_TURN), close=view.close_dialog)
        view.dialogs.show(popup, popup.choices)

    def activity(self):
        view, t = self.view, self.view.t
        project_id = view.project_id
        self._activity_dialog(t(MessageKey.SETTINGS_LOADING))
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
        popup = ReadOnlyDialog(view.t(MessageKey.PROJECT_ACTIVITY), text, view._rows, view._columns,
                               view.close_dialog, view.t, render_worker=view.render_worker)
        view.dialogs.show(popup, popup.receiver)
