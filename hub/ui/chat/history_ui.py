"""Session turn management and project execution activity dialogs."""

from hub.locales.keys import MessageKey, MessagePattern

from llm.core.models import RunStatus, MessageRole
from ...backend.history_values import HistoryCommand, HistoryRow, HistoryRun
from ...backend.history_deletion import (
    TurnDeletionItem,
    TurnDeletionReview,
    TurnDeletionResult,
)
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Label
from ...widget.controls import TextArea
from ...widget.output.reader import ReadOnlyDialog
import json
from llm.core.contracts import Diagnostic, DiagnosticSeverity
from llm.services.lifecycle.lifecycle_records import ResumeTurnDetails
from llm.providers.runtime_values import (
    ProviderTraceObservation,
    ProviderExceptionRecord,
)
from ...backend.observability.activity_values import ActivityRecord
from ...backend.runtime import HubRuntime
from ...config.notifications import NotificationKind
from ..submitted import SubmittedMessages

from ...widget.controls import Button
from ..error_presentation import operation_notice


class ActivityDisplay:
    LINE = "\n"
    PARAGRAPH = "\n\n"
    EMPTY = ""
    ABSENT = None
    ENSURE_ASCII = False
    JSON_INDENT = 2
    UNKNOWN_EXCEPTION = "Exception"
    UNKNOWN_FRAME = "?"
    HTTP = " • HTTP {status}"
    DETAIL = "  {key}: {value}"
    FRAME = "  {file}:{line} • {function}()"
    ROW = "{icon} {time} • {status} • {event}"
    CRITICAL = "critical"
    LEVELS = {
        RunStatus.FAILED.value: NotificationKind.ERROR.value,
        DiagnosticSeverity.ERROR.value: NotificationKind.ERROR.value,
        CRITICAL: NotificationKind.ERROR.value,
        DiagnosticSeverity.WARNING.value: NotificationKind.WARNING.value,
        RunStatus.INTERRUPTED.value: NotificationKind.WARNING.value,
        RunStatus.PAUSED.value: NotificationKind.WARNING.value,
        RunStatus.COMPLETED.value: NotificationKind.SUCCESS.value,
    }


def provider_detail(row, t):
    """Display stored frames, without inventing unavailable exception messages."""
    row = ActivityRecord(row)
    value = row.details
    trace = ProviderTraceObservation(value)
    exceptions = trace.exceptions
    lines = [t(MessagePattern.activity_provider(row.scope)), str(row.time)]
    if row.log_path:
        lines.append(t(MessageKey.ACTIVITY_PROVIDER_FILE, path=row.log_path))
    if not isinstance(exceptions, (list, tuple)) or not exceptions:
        lines.append(
            json.dumps(
                value,
                ensure_ascii=ActivityDisplay.ENSURE_ASCII,
                indent=ActivityDisplay.JSON_INDENT,
            )
        )
        return ActivityDisplay.LINE.join(lines)
    lines.append(
        json.dumps(
            trace.summary(),
            ensure_ascii=ActivityDisplay.ENSURE_ASCII,
            indent=ActivityDisplay.JSON_INDENT,
        )
    )
    lines.append(t(MessageKey.ACTIVITY_PROVIDER_TRACEBACK))
    for exception in exceptions:
        if not isinstance(exception, dict):
            lines.append(str(exception))
            continue
        label = str(
            exception.get(
                ProviderExceptionRecord.TYPE, ActivityDisplay.UNKNOWN_EXCEPTION
            )
        )
        if (
            exception.get(ProviderExceptionRecord.STATUS_CODE)
            is not ActivityDisplay.ABSENT
        ):
            label += ActivityDisplay.HTTP.format(
                status=exception[ProviderExceptionRecord.STATUS_CODE]
            )
        lines.append(label)
        for key, data in exception.items():
            if key not in (
                ProviderExceptionRecord.TYPE,
                ProviderExceptionRecord.STATUS_CODE,
                ProviderExceptionRecord.FRAMES,
            ):
                lines.append(ActivityDisplay.DETAIL.format(key=key, value=data))
        for frame in exception.get(ProviderExceptionRecord.FRAMES, ()):
            if isinstance(frame, dict):
                lines.append(
                    ActivityDisplay.FRAME.format(
                        file=frame.get(
                            ProviderExceptionRecord.FILE, ActivityDisplay.UNKNOWN_FRAME
                        ),
                        line=frame.get(
                            ProviderExceptionRecord.LINE, ActivityDisplay.UNKNOWN_FRAME
                        ),
                        function=frame.get(
                            ProviderExceptionRecord.FUNCTION,
                            ActivityDisplay.UNKNOWN_FRAME,
                        ),
                    )
                )
    return ActivityDisplay.LINE.join(lines)


def format_activity(rows, t):
    """Render the public activity contract, preserving its commit order."""
    blocks = []
    for record in rows:
        row = ActivityRecord(record)
        source = row.source
        lines = [
            ActivityDisplay.ROW.format(
                icon=t.icons.alerts[
                    ActivityDisplay.LEVELS.get(row.status, NotificationKind.INFO.value)
                ],
                time=row.time,
                status=t.status(row.status),
                event=row.event,
            ),
            t(
                MessageKey.ACTIVITY_SOURCE,
                session_id=source.session_id,
                run_id=source.run_id,
            ),
        ]
        if row.is_provider:
            lines.append(provider_detail(record, t))
        if source.step_id:
            lines.append(t(MessageKey.ACTIVITY_STEP, step_id=source.step_id))
        if row.code:
            lines.append(t(MessageKey.ACTIVITY_CODE, code=row.code))
        if row.provider_diagnostics:
            lines.append(t(MessageKey.ACTIVITY_PROVIDER_RUN_DETAILS))
            lines.extend(provider_detail(item, t) for item in row.provider_diagnostics)
        blocks.append(ActivityDisplay.LINE.join(lines))
    return ActivityDisplay.PARAGRAPH.join(blocks) or t(MessageKey.ACTIVITY_EMPTY)


class HistoryUI:

    RETRY_TURN = HistoryCommand.RETRY
    SNAPSHOT = HubRuntime.snapshot.__name__
    PROJECT_ACTIVITY = HubRuntime.project_activity.__name__
    ABSENT = None
    SINGLE_SELECTION = 1
    SELECT_ON_FOCUS = True
    ROW_CHOICE = "{agent} • {status}"
    BLOCKER = "{run} • {engine} • {status}"
    READ_ONLY = True
    SCROLLBAR = True
    WRAP_LINES = True
    MINIMUM_HEIGHT = 1
    MAXIMUM_HEIGHT = 8
    KEEPING_MESSAGES = False

    def __init__(self, controller):
        self.controller = controller
        self.view = controller.view

    def open(self):
        from ...widget.forms.request_history import RequestHistoryDialog

        view = self.view
        project_id = view.project_id
        session_id = view.sessions[view.selected].id
        view.dialogs.loading(
            view.t(MessageKey.HISTORY_TITLE),
            size=RequestHistoryDialog.SIZE,
        )
        pending = view._dialog

        def ready(rows):
            if view._dialog is not pending:
                return
            view.close_dialog()
            if (
                rows is self.ABSENT
                or view.project_id != project_id
                or view.sessions[view.selected].id != session_id
            ):
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
            popup = Dialog(
                title=t(MessageKey.HISTORY_TITLE),
                body=Label(t(MessageKey.HISTORY_EMPTY)),
                buttons=[button],
                size=RequestHistoryDialog.SIZE,
            )
            view.dialogs.show(popup, button)
            return

        def current():
            return (
                view.project_id == project_id
                and bool(view.sessions)
                and view.sessions[view.selected].id == session_id
            )

        def execute(operation):
            identifiers = popup.selected()
            if len(identifiers) != self.SINGLE_SELECTION:
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

                candidates = [
                    run
                    for run in HistoryRow(row).runs
                    if HistoryRun(run).run_id
                    and HistoryRun(run).status
                    in (RunStatus.INTERRUPTED, RunStatus.FAILED)
                ]
                if not candidates:
                    view.notice = t(MessageKey.HISTORY_RETRY_UNAVAILABLE)
                elif len(candidates) == self.SINGLE_SELECTION:
                    resume_turn(
                        self.controller,
                        session_id,
                        HistoryRun(candidates[0]).run_id,
                        identifier,
                    )
                else:
                    from ...widget.controls import RadioList

                    choices = RadioList(
                        [
                            (
                                HistoryRun(run).run_id,
                                self.ROW_CHOICE.format(
                                    agent=HistoryRun(run).agent_id,
                                    status=t.status(HistoryRun(run).status),
                                ),
                            )
                            for run in candidates
                        ],
                        select_on_focus=self.SELECT_ON_FOCUS,
                    )
                    view.open_dialog(
                        t(MessageKey.CHOOSE_TARGET),
                        choices,
                        lambda: resume_turn(
                            self.controller,
                            session_id,
                            choices.current_value,
                            identifier,
                        ),
                        choices,
                    )
                return
            args = (
                session_id,
                identifier,
                text,
                HistoryRow(row).request_id(identifier),
            )
            self.controller._call(
                operation,
                *args,
                completed=lambda result: result is not self.ABSENT
                and self.controller._call(
                    self.SNAPSHOT, completed=self.controller._snapshot
                )
            )

        def delete():
            identifiers = popup.selected()
            if not identifiers:
                view.notice = t(MessageKey.HISTORY_SELECT_ITEMS)
                return
            view.close_dialog()
            view.dialogs.loading(t(MessageKey.HISTORY_DELETE))
            pending = view._dialog

            def ready(plan):
                if view._dialog is not pending or not current():
                    return
                view.close_dialog()
                if plan is self.ABSENT:
                    return

                def confirmed():
                    if not current():
                        return
                    key = (project_id, session_id)
                    if key in view.deleting.pending:
                        return
                    request = view.deleting.start(
                        key, len(TurnDeletionReview(plan).optional_items())
                    )

                    def removed(result):
                        if result is self.ABSENT:

                            def failed_refresh(snapshot):
                                self.controller._snapshot(snapshot)
                                view.deleting.finish(request)

                            self.controller._call(
                                self.SNAPSHOT, completed=failed_refresh
                            )
                            return
                        from dataclasses import replace

                        deleted = set(TurnDeletionResult(result).removed)
                        SubmittedMessages.discard(view, key, deleted)
                        if view.project_id == project_id:
                            texts = {
                                HistoryRow(row).text
                                for row in rows
                                if HistoryRow(row).id in deleted
                                or HistoryRow(row).request_id() in deleted
                            }
                            view.input_history.discard(session_id, texts)
                            # Forget locally bridged receipts and cached turns before
                            # the next authoritative snapshot, including after errors.
                            for index, session in enumerate(view.sessions):
                                if session.id != session_id:
                                    continue
                                kept, removing = [], self.KEEPING_MESSAGES
                                for message in session.messages:
                                    if message.role == MessageRole.USER:
                                        removing = message.id in deleted
                                    if not removing:
                                        kept.append(message)
                                view.sessions[index] = replace(
                                    session, messages=tuple(kept)
                                )

                        def refreshed(snapshot):
                            self.controller._snapshot(snapshot)
                            view.deleting.finish(request)
                            if current():
                                if view.visible and view._dialog is self.ABSENT:
                                    self.open()
                                if TurnDeletionResult(result).errors:
                                    for error in TurnDeletionResult(result).errors:
                                        view.operation_diagnostics.record(
                                            project_id, error
                                        )
                                    view.notice = ActivityDisplay.LINE.join(
                                        dict.fromkeys(
                                            operation_notice(error, t)
                                            for error in TurnDeletionResult(
                                                result
                                            ).errors
                                        )
                                    )

                        self.controller._call(self.SNAPSHOT, completed=refreshed)

                    self.controller._call(
                        HistoryCommand.DELETE_MANY,
                        session_id,
                        identifiers,
                        TurnDeletionReview(plan).revision,
                        completed=removed,
                        progress=lambda done, total: view.deleting.update(
                            request, done, total
                        ),
                    )

                summary = t(MessageKey.HISTORY_DELETE_SELECTED, count=len(identifiers))
                if any(
                    not TurnDeletionItem(item).queued
                    for item in TurnDeletionReview(plan).optional_items()
                ):
                    summary += ActivityDisplay.LINE + t(
                        MessageKey.HISTORY_DELETE_CONFIRM
                    )
                if TurnDeletionReview(plan).blockers:
                    summary += ActivityDisplay.LINE + t(
                        MessageKey.HISTORY_ABANDON_CONFIRM,
                        count=len(TurnDeletionReview(plan).blockers),
                    )
                    summary += ActivityDisplay.LINE + ActivityDisplay.LINE.join(
                        self.BLOCKER.format(
                            run=TurnDeletionReview.blocker_run_id(item),
                            engine=item[Diagnostic.details.__name__][
                                ResumeTurnDetails.engine.__name__
                            ],
                            status=t.status(
                                item[Diagnostic.details.__name__][
                                    ResumeTurnDetails.status.__name__
                                ]
                            ),
                        )
                        for item in TurnDeletionReview(plan).blockers
                    )
                body = TextArea(
                    text=summary,
                    read_only=self.READ_ONLY,
                    scrollbar=self.SCROLLBAR,
                    wrap_lines=self.WRAP_LINES,
                    height=Dimension(min=self.MINIMUM_HEIGHT, max=self.MAXIMUM_HEIGHT),
                )
                view.open_dialog(
                    t(MessageKey.HISTORY_DELETE),
                    body,
                    confirmed,
                    accept_text=(
                        t(MessageKey.HISTORY_ABANDON_DELETE)
                        if TurnDeletionReview(plan).blockers
                        else t(MessageKey.HISTORY_DELETE)
                    ),
                )

            self.controller._call(
                HistoryCommand.DELETE_PLAN, session_id, identifiers, completed=ready
            )

        popup = RequestHistoryDialog(
            view,
            rows,
            delete=delete,
            edit=lambda: execute(HistoryCommand.EDIT),
            retry=lambda: execute(HistoryUI.RETRY_TURN),
            close=view.close_dialog,
        )
        view.dialogs.show(popup, popup.choices)

    def activity(self):
        view, t = self.view, self.view.t
        project_id = view.project_id
        view.dialogs.loading(t(MessageKey.PROJECT_ACTIVITY))
        pending = view._dialog

        def ready(rows):
            if view._dialog is not pending:
                return
            view.close_dialog()
            if view.project_id != project_id:
                return
            if rows is self.ABSENT and not view.operation_diagnostics.text(
                project_id, t
            ):
                return
            self._activity_dialog(
                lambda: ActivityDisplay.PARAGRAPH.join(
                    filter(
                        None,
                        (
                            (
                                format_activity(rows, t)
                                if rows is not self.ABSENT
                                else ActivityDisplay.EMPTY
                            ),
                            view.operation_diagnostics.text(project_id, t),
                        ),
                    )
                )
            )

        self.controller._call(self.PROJECT_ACTIVITY, completed=ready)

    def _activity_dialog(self, text):
        view = self.view
        popup = ReadOnlyDialog(
            view.t(MessageKey.PROJECT_ACTIVITY),
            text,
            view._rows,
            view._columns,
            view.close_dialog,
            view.t,
            render_worker=view.render_worker,
        )
        view.dialogs.show(popup, popup.receiver)
