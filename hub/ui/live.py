"""UI-thread controller; backend handles never cross into PTK callbacks."""

from hub.ui.input.registry import ShortcutRegistry

from hub.backend.session_contracts import SessionMode


from hub.locales.codes import LanguageCode

from hub.locales.keys import MessageKey

from llm.core.models import MessageStatus, RunStatus, MessageRole
import asyncio
from dataclasses import replace

from .view import HubView
from ..backend.runtime import HubConfig, HubRuntime
from ..backend.project.settings_contracts import SettingsOperation
from ..config.preference_records import PreferenceSnapshot
from ..config.sections import PreferenceSection
from ..config.notifications import NotificationKind
from .input.keys import KeyName, ShortcutAction, ShortcutLabel
from llm.core.interactions import InteractionRequestObservation
from ..model import (
    HubSnapshot,
    SubmissionResult,
    SessionView,
    SessionSummary,
    SessionIdentity,
    QuestionPresentation,
    InstructionTargetChoice,
)
from .presentation import present, present_message, MessagePresentationCache
from .snapshot_inbox import SnapshotInbox
from .requests import UIRequests
from .error_presentation import operation_notice
from ..backend.worker import BackendWorker
from ..config.view_state import ViewStateStore, ViewStateWriter
from ..config.preferences import PreferencesStore
from ..backend.project.component_commands import component_commands
from .chat.component_ui import ComponentCommands
from .submitted import SubmittedMessages
from ..widget.feedback.progress import ProgressCompletion


class RunningSubmissionChoice:
    STEER = HubRuntime.steer.__name__
    FOLLOW_UP = "follow_up"
    SELECT_ON_FOCUS = True
    ACCEPTED = True


class LiveHubView(HubView):
    ABSENT = HubView.ABSENT
    DISCONNECTED = False
    CONNECTED = True

    def __init__(
        self, theme=ABSENT, language=LanguageCode.DEFAULT, language_packs=ABSENT
    ):
        super().__init__(theme=theme, language=language, language_packs=language_packs)
        self._default_participants = ()
        self.sessions = [SessionView(self.t(MessageKey.CONNECTING))]
        self.output.set_messages(())
        self.project_title = self.t(MessageKey.CONNECTING)
        self.project_id = HubSnapshot.NO_PROJECT
        self.model = HubView.NO_MODEL
        self.agent_id = HubView.NO_AGENT
        self.connected = LiveHubView.DISCONNECTED
        self.notice = self.t(MessageKey.CONNECTING_NOTICE)
        SubmittedMessages.initialize(self)
        self._presentation_cache = MessagePresentationCache()
        self.failure_diagnoses = {}

    def show_submitted(self, project_id, session_id, message):
        """Bridge a durable receipt to the next authoritative conversation read."""
        key = (project_id, session_id)
        if project_id != self.project_id:
            return
        for index, session in enumerate(self.sessions):
            if session.id == session_id:
                if any(item.id == message.id for item in session.messages):
                    return
                SubmittedMessages.remember(self, key, message)
                messages = (*session.messages, present_message(message, self.t))
                self.sessions[index] = replace(session, messages=messages)
                if index == self.selected:
                    self.output.update_messages(self.preparing.project(key, messages))
                return

    def apply_snapshot(self, snapshot: HubSnapshot, *, select_id=ABSENT) -> None:
        HubSnapshot.observe_in(self, snapshot)
        self.interrupting.observe(snapshot)
        self.toasts.reconcile_waiting(snapshot.project_id, snapshot.sessions)
        self.preparing.observe(snapshot)
        key = (snapshot.project_id, snapshot.selected_id)
        known = {message.id for message in snapshot.messages}
        pending = SubmittedMessages.unobserved(self, key, known)
        if pending:
            snapshot = replace(
                snapshot, messages=(*snapshot.messages, *pending.values())
            )
        sessions = {session.id for session in snapshot.sessions}
        SubmittedMessages.retain(self, snapshot.project_id, sessions)
        snapshot = replace(
            snapshot,
            messages=tuple(
                (
                    replace(
                        item,
                        failure=self.failure_diagnoses.get(
                            (snapshot.project_id, snapshot.selected_id, item.run_id)
                        ),
                    )
                    if item.status == MessageStatus.FAILED
                    else item
                )
                for item in snapshot.messages
            ),
        )
        display = present(snapshot, self.t, self._presentation_cache)
        current_id = self.sessions[self.selected].id
        empty_draft_key = (
            self._draft_key(self.selected) if self.no_sessions else LiveHubView.ABSENT
        )
        changing_project = (
            bool(self.project_id) and self.project_id != snapshot.project_id
        )
        if not changing_project:
            self.remember_position()
        if (
            changing_project
            or (select_id and select_id != current_id)
            or current_id not in sessions
        ):
            self.question.leave()
        if self.question.item is LiveHubView.ABSENT:
            self._drafts[self._draft_key(self.selected)] = self.composer.text
        cached = {session.id: session for session in self.sessions}
        self.sessions = [
            SessionView(
                s.title, s.status, cached[s.id].messages if s.id in cached else (), s.id
            )
            for s in display.sessions
        ]
        self.no_sessions = not bool(self.sessions)
        if self.no_sessions:
            self.sessions = [SessionView(self.t(MessageKey.WELCOME_TITLE))]
        for index, session in enumerate(self.sessions):
            if session.id == snapshot.selected_id:
                self.sessions[index] = replace(session, messages=display.messages)
        target = (
            select_id
            or (snapshot.selected_id if changing_project else current_id)
            or snapshot.selected_id
        )
        self.selected = next(
            (i for i, s in enumerate(self.sessions) if s.id == target),
            HubView.INITIAL_SELECTION,
        )
        changed = current_id != self.sessions[self.selected].id
        if changed:
            self.output.deactivate()
            self.composer.text = self._drafts.get(
                self._draft_key(self.selected), HubView.EMPTY_DRAFT
            )
            if empty_draft_key and not self.no_sessions and not changing_project:
                self.composer.text = self._drafts.pop(
                    empty_draft_key, HubView.EMPTY_DRAFT
                )
            position = self._positions.get(self._draft_key(self.selected))
            self.output.set_messages(self.sessions[self.selected].messages, position)
            if position is LiveHubView.ABSENT and self.general.auto_scroll:
                self.output.set_follow_tail(HubView.FOLLOW_TAIL)
        else:
            # Preserve a reader's scroll position. New/updated output follows the
            # bottom only if the reader was already at the bottom.
            messages = self.sessions[self.selected].messages
            self.output.update_messages(messages, auto_scroll=self.general.auto_scroll)
        self.connected = LiveHubView.CONNECTED
        self.project_title = snapshot.project_title
        self.project_id = snapshot.project_id
        self.model = snapshot.model
        commands = component_commands(snapshot.components)
        if commands != self.component_commands:
            self.composer.buffer.cancel_completion()
            self.component_commands = commands
        self.engines = snapshot.engines
        self.agents = tuple(agent.id for agent in snapshot.agents)
        self.agent_engines = {agent.id: agent.engine for agent in snapshot.agents}
        self.agent_titles = {agent.id: agent.title or agent.id for agent in snapshot.agents}
        self._participant_sets.update({item.id: item.participant_ids for item in snapshot.sessions})
        self._participant_groups.update({item.id: item.participant_groups for item in snapshot.sessions})
        if self.sessions[self.selected].id == snapshot.selected_id:
            self.participant_ids = snapshot.participant_ids
        if self.recipient_id not in (*self.participant_ids, *(group.id for group in self.participant_groups)):
            self.recipient_id = self.NO_AGENT
        if self.agent_id not in self.agents:
            self.agent_id = next(iter(self.agents), HubView.NO_AGENT)
        if snapshot.file_root:
            self.file_root = snapshot.file_root
            self.output.set_file_root(snapshot.file_root)
        if self.sessions[self.selected].id == snapshot.selected_id:
            self.question.sync(
                snapshot.project_id, snapshot.selected_id, snapshot.questions
            )
            self.notice = display.notice
        self.preparing.paint()
        if self.sessions[self.selected].id == snapshot.selected_id:
            self.followup.refresh(
                (snapshot.project_id, snapshot.selected_id), snapshot.messages
            )

    def set_theme(self, theme):
        super().set_theme(theme)
        callback = HubView.output_interval_callback(self)
        if callback:
            callback(theme.output_refresh_interval)
        if HubSnapshot.last_observed(self) is not LiveHubView.ABSENT:
            self.apply_snapshot(HubSnapshot.require_observed(self))


class LiveController:
    ABSENT = None
    OPEN = False
    CLOSED = True
    CHOOSING = True
    NOT_CHOOSING = False
    INITIAL_NOTIFICATION = 0
    ERROR_TOAST = "backend-error"
    DIAGNOSTIC_WINDOW = 256
    DIAGNOSTIC_BATCH = 64
    DIAGNOSTIC_CACHE = 256
    SAVE_DEBOUNCE = 0.25
    MARKDOWN_APPROVAL = True
    FOLLOWUP_CHECK_RUNNING = False
    SINGLE_TARGET = 1
    UNTITLED = ""
    CANCEL_NOTICE_DELAY = 5
    NO_NOTICE = ""
    CLOSE_ALREADY_PREPARED = False
    CLOSE_PREPARED = True
    FOREGROUND_OPERATIONS = frozenset({
        HubRuntime.select.__name__, HubRuntime.new_session.__name__,
        HubRuntime.delete_session.__name__, HubRuntime.rename_session.__name__,
        HubRuntime.turns.__name__, HubRuntime.delete_turn_plan.__name__,
        HubRuntime.delete_turns_plan.__name__, HubRuntime.delete_turn.__name__,
        HubRuntime.cancel_request.__name__, HubRuntime.project_activity.__name__,
        HubRuntime.execution_catalog.__name__, HubRuntime.approval_details.__name__,
        HubRuntime.snapshot.__name__,
        HubRuntime.invite_agent.__name__,
        HubRuntime.change_participants.__name__,
    })

    def __init__(
        self, view: LiveHubView, config: HubConfig, *, worker_factory=BackendWorker
    ):
        from ..config.preferences import configured_workspace

        config = configured_workspace(config)
        preferences = PreferenceSnapshot(
            PreferencesStore(config._workspace_anchor).load()
        )
        if preferences.has_section(PreferenceSection.PROFILE.value):
            config = replace(config, user_profile=preferences.required_profile())
        if preferences.has_section(PreferenceSection.APPEARANCE.value):
            view.set_theme(preferences.required_theme())
        view.general = preferences.general()
        view.user_profile = config.user_profile
        self.view = view
        self.config = config
        self.worker_factory = worker_factory
        self.worker = LiveController.ABSENT
        self._sidebar_width_pending = LiveController.ABSENT
        HubView.bind_sidebar_width(self.view, self._save_sidebar_width)
        HubView.bind_output_interval(self.view, self._set_output_interval)
        self.app = LiveController.ABSENT
        self.loop = LiveController.ABSENT
        self.closed = LiveController.OPEN
        self._startup_task = None
        self.requests = UIRequests(
            lambda: self.worker,
            self._dispatch,
            active=lambda: not self.closed,
            connected=lambda: self.view.connected,
            unavailable=lambda: self.view.t(MessageKey.NOT_CONNECTED),
        )
        self._snapshot_inbox = SnapshotInbox()
        self._submitting = set()
        self._choosing_submission = LiveController.NOT_CHOOSING
        self._select_new = LiveController.ABSENT
        self._view_state = ViewStateStore(config.workspace)
        self._view_writer = ViewStateWriter(
            self._view_state, lambda error: self._dispatch(self._error, error)
        )
        self._state_project = LiveController.ABSENT
        self._restoring_snapshot = LiveController.ABSENT
        self._save_timer = LiveController.ABSENT
        self._last_notification = LiveController.INITIAL_NOTIFICATION
        self._diagnosing = set()
        HubView.bind_default_agent(self.view, config.agent_id)
        self.view.agent_id = config.agent_id
        self.view.on_open = self.open
        self.view.on_submit = self.submit
        self.view.on_answer = self.answer_question
        self.view.on_review = self.review_approval
        self.view.on_select = self.select
        self.view.on_new = self.new_session
        self.view.on_delete = self.delete_session
        self.view.on_rename = self.rename_session
        self.component_commands = ComponentCommands(self)
        self.view.on_component = self.component_commands.open
        self.view.on_invite_agent = self.invite_agent
        self.view.on_change_participants = self.change_participants
        self.view.on_interrupt = self.interrupt
        from .chat.history_ui import HistoryUI

        self.history_ui = HistoryUI(self)
        self.view.on_history = self.history_ui.open
        self.view.on_activity = self.history_ui.activity
        self.view.on_turns = lambda session_id, ready: self._call(
            HubRuntime.turns.__name__, session_id, completed=ready
        )
        self.view.on_save_view = self._save_view
        self.view.settings.request = self._settings_request
        from ..config.restart import startup_settings

        self.view.settings.drafts.applied_preferences = startup_settings(
            config, view.general
        )
        self.view.settings.drafts.workspace_anchor = config._workspace_anchor
        self.view.on_output_position_changed = self._reading_changed
        self.view.file_root = (
            config.workspace
            if config.file_root is LiveController.ABSENT
            else config.file_root
        )
        self.environment_provider = LiveController.ABSENT
        if config.engine_factories:
            self.view.engines = tuple(config.engine_factories)

    def _dispatch(self, callback, *args):
        if not self.closed and self.loop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(callback, *args)

    def _publish(self, snapshot):
        available = (
            not self.closed
            and self.loop is not LiveController.ABSENT
            and not self.loop.is_closed()
        )
        if self._snapshot_inbox.offer(snapshot, can_schedule=available):
            self._dispatch(self._consume_snapshot)

    def _consume_snapshot(self):
        self._snapshot(self._snapshot_inbox.take())

    def _error(self, error):
        if self.closed:
            return
        info = self.view.operation_diagnostics.record(self.view.project_id, error)
        message = operation_notice(
            error if isinstance(error, str) else info, self.view.t
        )
        self.view.notice = self.view.t(MessageKey.ERROR, error=message)
        self.view.toasts.alert(
            self.view.sessions[self.view.selected].title,
            message,
            NotificationKind.ERROR.value,
            LiveController.ERROR_TOAST,
        )
        self.app.invalidate()

    def _snapshot(self, snapshot):
        if self.closed or snapshot is LiveController.ABSENT:
            return
        restore_selection = LiveController.ABSENT
        changing_project = self._state_project != snapshot.project_id
        if changing_project and not self._view_writer.is_loaded(snapshot.project_id):
            self._restoring_snapshot = snapshot

            def loaded(error):
                def apply():
                    if error:
                        self._error(error)
                    latest = self._restoring_snapshot
                    if latest and latest.project_id == snapshot.project_id:
                        self._restoring_snapshot = LiveController.ABSENT
                        self._snapshot(latest)

                self._dispatch(apply)

            self._view_writer.load_background(snapshot.project_id, loaded)
            return
        self._restoring_snapshot = LiveController.ABSENT
        if changing_project:
            if self.view.project_id:
                self.view.remember_position()
                self._save_view()
                HubView.clear_positions(self.view)
            self._state_project = snapshot.project_id
            try:
                selected, positions = self._view_writer.load(snapshot.project_id)
                HubView.restore_positions(self.view, positions)
                explicit = (
                    self.config.session_id
                    and self.config.project_id == snapshot.project_id
                )
                if (
                    self.view.general.restore_last_session
                    and not explicit
                    and any(session.id == selected for session in snapshot.sessions)
                ):
                    self._select_new = selected
                    if selected != snapshot.selected_id:
                        restore_selection = selected
            except (OSError, ValueError, TypeError) as error:
                self._error(error)
        target = self._select_new
        self.view.apply_snapshot(snapshot, select_id=target)
        self._finish_startup()
        presets = self.view.profile_bar.preset_ui
        if not presets.loaded and not presets.loading and not self.view.settings.busy:
            presets.refresh()
        self._diagnose(snapshot)
        if not self.view.projects.loaded and not self.view.projects.busy:
            self.view.projects.refresh()
        if not self.view.projects.busy:
            self.view.projects.selected = snapshot.project_id
        self.view.projects.rename_observed(snapshot.project_id, snapshot.project_title)
        self.refresh_shell_root()
        current = (self.view.project_id, self.view.sessions[self.view.selected].id)
        displayed_status = {session.id: session.status for session in snapshot.sessions}
        for notification in snapshot.notifications:
            if notification.sequence <= self._last_notification:
                continue
            self._last_notification = notification.sequence
            if (
                notification.status
                in (SessionSummary.WAITING_INPUT, SessionSummary.WAITING_APPROVAL)
                and notification.project_id == snapshot.project_id
                and displayed_status.get(notification.session_id) != notification.status
            ):
                continue  # A coalesced snapshot can already contain the answer.
            if (
                notification.status
                in (SessionSummary.WAITING_INPUT, SessionSummary.WAITING_APPROVAL)
                or not self.view.visible
                or (notification.project_id, notification.session_id) != current
            ):
                self.view.toasts.push(
                    notification.title,
                    notification.status,
                    (notification.project_id, notification.session_id),
                )
        if target and any(s.id == target for s in snapshot.sessions):
            self._select_new = LiveController.ABSENT
        if changing_project:
            self._save_view()
        self.app.invalidate()
        if restore_selection is not LiveController.ABSENT:
            self._call(HubRuntime.select.__name__, restore_selection)

    def _settings_request(self, operation, args, completed):
        def received(result):
            if result.error is not LiveController.ABSENT:
                completed(LiveController.ABSENT, result.error)
                return
            if operation == SettingsOperation.ACTIVATE_PROJECT.value:
                self._snapshot(result.value)
            elif operation == SettingsOperation.DELETE_PROJECT.value:
                self._call(HubRuntime.snapshot.__name__, completed=self._snapshot)
            completed(result.value)

        self.requests.call(
            HubRuntime.settings.__name__,
            operation,
            *args,
            completed=received,
            allow_disconnected=operation == SettingsOperation.CATALOG.value,
            stage=completed.stage if isinstance(completed, ProgressCompletion) else None,
        )

    def _diagnose(self, snapshot):
        identity = snapshot.project_id, snapshot.selected_id
        candidates = [
            item.run_id
            for item in snapshot.messages
            if item.role == MessageRole.ASSISTANT.value
            and item.status == MessageStatus.FAILED
            and item.run_id
        ][-LiveController.DIAGNOSTIC_WINDOW :]
        missing = [
            rid
            for rid in candidates
            if (*identity, rid) not in self.view.failure_diagnoses
            and (*identity, rid) not in self._diagnosing
        ][-LiveController.DIAGNOSTIC_BATCH :]
        if not missing:
            return
        self._diagnosing.update((*identity, rid) for rid in missing)

        def received(result):
            from ..errors.contracts import ErrorInfo

            for rid in missing:
                key = (*identity, rid)
                self._diagnosing.discard(key)
                self.view.failure_diagnoses[key] = (result.value or {}).get(
                    rid, ErrorInfo()
                )
            while len(self.view.failure_diagnoses) > LiveController.DIAGNOSTIC_CACHE:
                self.view.failure_diagnoses.pop(next(iter(self.view.failure_diagnoses)))
            latest = HubSnapshot.last_observed(self.view)
            if latest and (latest.project_id, latest.selected_id) == identity:
                self.view.apply_snapshot(latest)
                self.app.invalidate()

        self.requests.call(
            HubRuntime.diagnose_failures.__name__,
            *identity,
            missing,
            completed=received
        )

    def _save_view(self):
        if self._save_timer:
            self._save_timer.cancel()
            self._save_timer = LiveController.ABSENT
        if self.closed or not self.view.project_id:
            return
        try:
            self._view_writer.save(
                self.view.project_id,
                self.view.sessions[self.view.selected].id,
                HubView.saved_positions(self.view),
            )
        except (OSError, ValueError, TypeError) as error:
            self.view.notice = self.view.t(MessageKey.VIEW_STATE_ERROR, error=error)

    def _reading_changed(self):
        self.view.remember_position()
        if self._save_timer:
            self._save_timer.cancel()
        if self.loop is not LiveController.ABSENT and not self.closed:
            self._save_timer = self.loop.call_later(
                LiveController.SAVE_DEBOUNCE, self._save_view
            )

    def _call(
        self, operation, *args, completed=ABSENT, acknowledged=ABSENT, progress=ABSENT,
        allow_disconnected=UIRequests.REQUIRE_CONNECTION,
    ):
        stage = completed.stage if isinstance(completed, ProgressCompletion) else None
        token = None
        if stage is None and operation in self.FOREGROUND_OPERATIONS:
            token = self.view.progress.start(self.view.t(MessageKey.PROGRESS_BACKEND_READ))
            stage = lambda update: self.view.progress.observe(token, update)

        def received(result):
            if token is not None:
                self.view.progress.finish(token)
            if result.error is not LiveController.ABSENT:
                self._error(result.error)
            if completed:
                completed(result.value)

        self.requests.call(
            operation,
            *args,
            completed=received,
            acknowledged=acknowledged,
            progress=progress,
            stage=stage,
            allow_disconnected=allow_disconnected,
        )

    def _startup_stage(self, update):
        if not self.closed and self._startup_task is not None:
            self.view.progress.observe(self._startup_task, update)

    def _worker_error(self, error):
        self._finish_startup()
        self._error(error)

    def _finish_startup(self):
        if self._startup_task is not None:
            self.view.progress.finish(self._startup_task)
            self._startup_task = None

    def _set_output_interval(self, seconds):
        if self.worker:
            self.worker.set_output_interval(seconds)

    def _save_sidebar_width(self, width):
        self._sidebar_width_pending = width
        if self.worker:
            self.worker.set_sidebar_width(width)
            self._sidebar_width_pending = LiveController.ABSENT

    def open(self, app):
        self.app = app
        self.refresh_shell_root()
        self.view.toasts.app = app
        self.loop = asyncio.get_running_loop()
        if self.worker is LiveController.ABSENT:
            self._startup_task = self.view.progress.start(self.view.t(MessageKey.PROGRESS_BACKEND_PREPARE))
            self.worker = self.worker_factory(
                self.config,
                self._publish,
                lambda error: self._dispatch(self._worker_error, error),
            )
            self.worker.observe_startup(lambda update: self._dispatch(self._startup_stage, update))
            self._set_output_interval(self.view.theme.output_refresh_interval)
            if self._sidebar_width_pending is not LiveController.ABSENT:
                self._save_sidebar_width(self._sidebar_width_pending)
        else:
            self._call(
                HubRuntime.select.__name__, self.view.sessions[self.view.selected].id
            )

    def bind_app(self, app):
        """Rebind callbacks when ish starts its next prompt event loop."""
        loop = asyncio.get_running_loop()
        self.refresh_shell_root()
        if self.loop is not loop and not self.closed:
            self.app, self.loop = app, loop
            self._snapshot_inbox.rebind()
            self.view.toasts.app = app
            if self.worker:
                self._call(HubRuntime.snapshot.__name__, completed=self._snapshot,
                           allow_disconnected=UIRequests.ALLOW_DISCONNECTED)

    def refresh_shell_root(self):
        if self.environment_provider is not LiveController.ABSENT:
            try:
                environment = self.environment_provider()
            except ValueError:
                return  # Shell context can arrive after plugin installation.
            self.view.file_root = environment.cwd
            self.view.output.set_file_root(environment.cwd)

    def _accept_input(self, project_id, session_id, text, result, operation):
        if result is LiveController.ABSENT or self.closed:
            return
        view = self.view
        if isinstance(result, SubmissionResult):
            if result.message is LiveController.ABSENT:
                return
            view.preparing.accepted((project_id, session_id), result.message.id)
            view.show_submitted(project_id, session_id, result.message)
        if view.project_id != project_id:
            return
        current_id = view.sessions[view.selected].id
        if current_id == session_id:
            view.notice = view.t(
                MessageKey.INSTRUCTION_SAVED
                if operation == HubRuntime.steer.__name__
                else MessageKey.SAVED
            )
            if view.general.auto_scroll:
                view.output.restore_position(LiveController.ABSENT)
                view.output.set_follow_tail(HubView.FOLLOW_TAIL)
        self.app.invalidate()

    def _send(self, operation, session_id, text, *args, draft=ABSENT):
        if (
            not text.strip()
            or session_id in self._submitting
            or not self.view.connected
        ):
            if draft:
                draft.restore(self.view)
            return
        self._submitting.add(session_id)
        project_id = self.view.project_id
        from .input.submission import SubmittedDraft

        if draft is LiveController.ABSENT:
            draft = SubmittedDraft(project_id, session_id, text)
            draft.consume(self.view)
        if operation == HubRuntime.submit_input.__name__:
            self.view.preparing.start((project_id, session_id), text)

        def accepted(request_id):
            self._submitting.discard(session_id)
            if request_id is LiveController.ABSENT:
                self.view.preparing.finish((project_id, session_id))
                draft.restore(self.view)
            self._accept_input(project_id, session_id, text, request_id, operation)

        self._call(operation, session_id, *args, completed=accepted)

    def review_approval(self, request):
        view = self.view
        identity = view.question.identity

        def ready(text):
            if (
                text is LiveController.ABSENT
                or identity != view.question.identity
                or view._dialog is not LiveController.ABSENT
                or not view.visible
            ):
                return
            from ..widget.output.reader import ReadOnlyDialog

            popup = ReadOnlyDialog(
                InteractionRequestObservation(request).title,
                text,
                view._rows,
                view._columns,
                view.dialogs.close,
                view.t,
                markdown=LiveController.MARKDOWN_APPROVAL,
                theme=lambda: view.theme,
                render_worker=view.render_worker,
            )
            view.dialogs.show(popup, popup.receiver)

        self._call(HubRuntime.approval_details.__name__, request, completed=ready)

    def answer_question(self, item, option_id, value):
        question = self.view.question
        if question.busy:
            return
        response = question.begin_answer(option_id)
        identity = response.identity
        self.app.invalidate()

        def acknowledged():
            response.acknowledge()
            self.view.preparing.paint()

        def answered(result):
            question.finish_answer(response, bool(result))

        self._call(
            HubRuntime.answer_question.__name__,
            SessionIdentity(identity).project_id,
            SessionIdentity(identity).session_id,
            QuestionPresentation.observe(item).run_id,
            QuestionPresentation.observe(item).request.to_record(),
            option_id,
            value,
            completed=answered,
            acknowledged=acknowledged,
        )

    def submit(self, session_id: str, text: str):
        if not session_id:
            self.view.notice = self.view.t(MessageKey.SESSION_REQUIRED)
            self.view.session_dialog(SessionMode.NEW.value)
            return
        if (
            not text.strip()
            or session_id in self._submitting
            or self._choosing_submission
        ):
            return
        view = self.view
        identity = (view.project_id, session_id)
        agent_id, options = view.recipients, {}
        if self.environment_provider is not LiveController.ABSENT:
            from ..config.environment import ENVIRONMENT_OPTION

            try:
                options[ENVIRONMENT_OPTION] = self.environment_provider().to_dict()
            except ValueError as error:
                self._error(error)
                return
        if not options:
            from llm.core.configuration import UNSET

            options = UNSET
        from .input.submission import SubmittedDraft

        draft = SubmittedDraft(SessionIdentity(identity).project_id, session_id, text)
        draft.consume(view)
        self._choosing_submission = LiveController.CHOOSING
        self._submitting.add(session_id)
        view.preparing.start(identity, text)

        def ready(state):
            self._choosing_submission = LiveController.NOT_CHOOSING
            self._submitting.discard(session_id)
            if (
                state is not LiveController.ABSENT
                and state.message is not LiveController.ABSENT
            ):
                self._accept_input(
                    SessionIdentity(identity).project_id,
                    session_id,
                    text,
                    state,
                    HubRuntime.submit.__name__,
                )
                return
            view.preparing.finish(identity)
            if (
                state is LiveController.ABSENT
                or self.closed
                or not view.visible
                or view.settings_open
                or view._dialog is not LiveController.ABSENT
                or identity != (view.project_id, view.sessions[view.selected].id)
            ):
                draft.restore(view)
                return

            def follow_up():
                self._send(
                    HubRuntime.submit_input.__name__,
                    session_id,
                    text,
                    text,
                    agent_id,
                    options,
                    LiveController.FOLLOWUP_CHECK_RUNNING,
                    draft=draft,
                )

            from ..widget.controls import RadioList
            from prompt_toolkit.layout import HSplit
            from prompt_toolkit.widgets import Label

            choices = RadioList(
                ([
                    (RunningSubmissionChoice.STEER, view.t(MessageKey.SEND_STEERING)),
                ] if len(agent_id) == self.SINGLE_TARGET else []) + [
                    (
                        RunningSubmissionChoice.FOLLOW_UP,
                        view.t(MessageKey.SEND_FOLLOW_UP),
                    ),
                ],
                default=RunningSubmissionChoice.FOLLOW_UP,
                select_on_focus=RunningSubmissionChoice.SELECT_ON_FOCUS,
            )

            def accept():
                if choices.current_value == RunningSubmissionChoice.STEER:
                    self.steer(
                        session_id, text, expected_run_id=state.run_id, draft=draft
                    )
                else:
                    follow_up()

            view.open_dialog(
                view.t(MessageKey.SEND_WHILE_RUNNING),
                choices if len(agent_id) == self.SINGLE_TARGET else HSplit([
                    Label(view.t(MessageKey.PARTICIPANTS_STEERING_HINT)), choices,
                ]),
                accept,
                choices,
                on_cancel=lambda: draft.restore(view),
            )

            # This override and its hint share the choice widget's registry.
            def confirm(event):
                view.dialogs.close(accepted=RunningSubmissionChoice.ACCEPTED)
                accept()

            ShortcutRegistry.require(choices.control).add(
                [KeyName.ENTER], ShortcutLabel.HIDDEN, ShortcutAction.CONFIRM, confirm
            )

        self._call(
            HubRuntime.submit_input.__name__,
            session_id,
            text,
            agent_id,
            options,
            completed=ready,
        )

    def steer(
        self, session_id: str, text: str, *, expected_run_id=ABSENT, draft=ABSENT
    ):
        if not text.strip() or session_id in self._submitting:
            if draft:
                draft.restore(self.view)
            return

        def ready(value):
            if value is LiveController.ABSENT:
                if draft:
                    draft.restore(self.view)
                return
            run_id, targets = value
            if (
                expected_run_id is not LiveController.ABSENT
                and run_id != expected_run_id
            ):
                self._error(self.view.t(MessageKey.INSTRUCTION_RUN_CHANGED))
                if draft:
                    draft.restore(self.view)
                return
            if len(targets) == LiveController.SINGLE_TARGET:
                self._send(
                    HubRuntime.steer.__name__,
                    session_id,
                    text,
                    run_id,
                    text,
                    [InstructionTargetChoice.first_id(targets)],
                    draft=draft,
                )
            else:
                from ..widget.controls import RadioList

                choices = RadioList(
                    list(targets),
                    select_on_focus=RunningSubmissionChoice.SELECT_ON_FOCUS,
                )
                self.view.open_dialog(
                    self.view.t(MessageKey.CHOOSE_TARGET),
                    choices,
                    lambda: self._send(
                        HubRuntime.steer.__name__,
                        session_id,
                        text,
                        run_id,
                        text,
                        [choices.current_value],
                        draft=draft,
                    ),
                    choices,
                    on_cancel=lambda: draft and draft.restore(self.view),
                )

        self._call(
            HubRuntime.instruction_targets.__name__,
            session_id,
            expected_run_id,
            completed=ready,
        )

    def select(self, session_id: str):
        if session_id:
            self._call(HubRuntime.select.__name__, session_id,
                       completed=lambda _: self._call(HubRuntime.snapshot.__name__, completed=self._snapshot))

    def invite_agent(self, identifier, completed):
        view = self.view
        project_id, session_id = view.project_id, view.sessions[view.selected].id
        def invited(result):
            if result is not None:
                if (view.project_id, view.sessions[view.selected].id) == (project_id, session_id):
                    view.participant_ids = tuple(result)
                self._call(HubRuntime.snapshot.__name__, completed=self._snapshot)
            completed(result)
        self._call(HubRuntime.invite_agent.__name__, project_id, session_id, identifier,
                   completed=invited)

    def change_participants(self, action, identifier, value, completed):
        view = self.view
        project_id, session_id = view.project_id, view.sessions[view.selected].id
        def changed(result):
            if result is not None:
                self._call(HubRuntime.snapshot.__name__, completed=self._snapshot)
            completed(result)
        self._call(HubRuntime.change_participants.__name__, project_id, session_id,
                   action, identifier, value, completed=changed)

    def new_session(
        self,
        mode=SessionMode.NEW.value,
        title=UNTITLED,
        source_id=ABSENT,
        through_message_id=ABSENT,
    ):
        def created(session_id):
            if session_id is LiveController.ABSENT:
                return
            self._select_new = session_id
            self._call(HubRuntime.snapshot.__name__, completed=self._snapshot)

        self._call(
            HubRuntime.new_session.__name__,
            mode,
            title,
            source_id,
            through_message_id,
            completed=created,
        )

    def interrupt(self, session_id: str):
        if not session_id:
            return
        key = (self.view.project_id, session_id)
        if key in self.view.preparing.resuming:
            return
        if key in self.view.interrupting.pending:
            self.view.notice = self.view.t(MessageKey.CANCEL_WAIT)
            self.app.invalidate()
            return
        snapshot = HubSnapshot.last_observed(self.view)
        if (
            snapshot
            and snapshot.selected_id == session_id
            and snapshot.run
            and not snapshot.run.active
            and not snapshot.active_run_ids
            and snapshot.run.status == RunStatus.INTERRUPTED
            and not snapshot.queued_count
            and key not in self.view.preparing.pending
        ):
            from .chat.resume import resume_turn

            resume_turn(self, session_id, snapshot.run.id)
            return
        request = self.view.interrupting.start(key)
        if request:
            self.view.question.leave()
        waiting = self.view.t(MessageKey.CANCEL_WAIT)

        def explain_delay():
            if (
                not self.closed
                and request
                and self.view.interrupting.pending.get(key) is request
            ):
                self.view.notice = waiting
                self.app.invalidate()

        monitor = (
            self.loop.call_later(LiveController.CANCEL_NOTICE_DELAY, explain_delay)
            if request
            else LiveController.ABSENT
        )

        def interrupted(result):
            if monitor:
                monitor.cancel()
            if self.view.notice == waiting:
                self.view.notice = LiveController.NO_NOTICE
            self.view.interrupting.finish(request, result)
            self._call(HubRuntime.snapshot.__name__, completed=self._snapshot)

        self._call(HubRuntime.interrupt.__name__, session_id, completed=interrupted)

    def rename_session(self, session_id: str, title: str):
        self._call(
            HubRuntime.rename_session.__name__,
            session_id,
            title,
            completed=lambda result: result is not LiveController.ABSENT
            and self._call(HubRuntime.snapshot.__name__, completed=self._snapshot),
        )

    def delete_session(self, session_id: str):
        if not session_id:
            return

        def deleted(selected_id):
            if selected_id is LiveController.ABSENT:
                return
            HubView.drop_session_state(self.view, session_id)
            self._select_new = selected_id
            self._call(HubRuntime.snapshot.__name__, completed=self._snapshot)

        self._call(HubRuntime.delete_session.__name__, session_id, completed=deleted)

    def _prepare_close(self):
        if self.closed:
            return LiveController.CLOSE_ALREADY_PREPARED
        self.view.close()
        self.view.remember_position()
        self._save_view()
        self.closed = LiveController.CLOSED
        return LiveController.CLOSE_PREPARED

    def _finish_close(self):
        self._view_writer.close()
        if self.worker:
            self.worker.close()

    async def aclose(self):
        if self._prepare_close():
            await asyncio.to_thread(self._finish_close)

    def close(self):
        if self._prepare_close():
            self._finish_close()
