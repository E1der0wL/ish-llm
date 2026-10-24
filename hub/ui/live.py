"""UI-thread controller; backend handles never cross into PTK callbacks."""

from hub.locales.codes import LanguageCode

from hub.locales.keys import MessageKey

from llm.core.models import MessageStatus, RunStatus
import asyncio
from dataclasses import replace

from .view import HubView
from ..backend.runtime import HubConfig
from ..model import HubSnapshot, SubmissionResult, SessionView, SessionSummary
from .presentation import present, present_message, MessagePresentationCache
from .snapshot_inbox import SnapshotInbox
from .requests import UIRequests
from .error_presentation import operation_notice
from ..backend.worker import BackendWorker
from ..config.view_state import ViewStateStore, ViewStateWriter
from ..config.preferences import PreferencesStore
from ..config.profile import UserProfile
from ..config.general import GeneralSettings
from ..config.theme import HubTheme
from ..backend.project.component_commands import component_commands
from .chat.component_ui import ComponentCommands


class LiveHubView(HubView):
    def __init__(self, theme=None, language=LanguageCode.DEFAULT, language_packs=None):
        super().__init__(theme=theme, language=language, language_packs=language_packs)
        self.sessions = [SessionView(self.t(MessageKey.CONNECTING))]
        self.output.set_messages(())
        self.project_title = self.t(MessageKey.CONNECTING)
        self.project_id = HubSnapshot.NO_PROJECT
        self.model = ""
        self.agent_id = ""
        self.connected = False
        self.notice = self.t(MessageKey.CONNECTING_NOTICE)
        self._submitted_messages = {}
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
                self._submitted_messages.setdefault(key, {})[message.id] = message
                messages = (*session.messages, present_message(message, self.t))
                self.sessions[index] = replace(session, messages=messages)
                if index == self.selected:
                    self.output.update_messages(self.preparing.project(key, messages))
                return

    def apply_snapshot(self, snapshot: HubSnapshot, *, select_id=None) -> None:
        HubSnapshot.observe_in(self, snapshot)
        self.interrupting.observe(snapshot)
        self.toasts.reconcile_waiting(snapshot.project_id, snapshot.sessions)
        self.preparing.observe(snapshot)
        key = (snapshot.project_id, snapshot.selected_id)
        known = {message.id for message in snapshot.messages}
        pending = self._submitted_messages.get(key, {})
        pending = {identifier: message for identifier, message in pending.items() if identifier not in known}
        if pending:
            self._submitted_messages[key] = pending
            snapshot = replace(snapshot, messages=(*snapshot.messages, *pending.values()))
        else:
            self._submitted_messages.pop(key, None)
        sessions = {session.id for session in snapshot.sessions}
        self._submitted_messages = {key: value for key, value in self._submitted_messages.items()
                                    if key[0] == snapshot.project_id and key[1] in sessions}
        snapshot = replace(snapshot, messages=tuple(replace(item, failure=self.failure_diagnoses.get(
            (snapshot.project_id, snapshot.selected_id, item.run_id))) if item.status == MessageStatus.FAILED else item
            for item in snapshot.messages))
        display = present(snapshot, self.t, self._presentation_cache)
        current_id = self.sessions[self.selected].id
        empty_draft_key = self._draft_key(self.selected) if self.no_sessions else None
        changing_project = bool(self.project_id) and self.project_id != snapshot.project_id
        if not changing_project:
            self.remember_position()
        if changing_project or (select_id and select_id != current_id) or current_id not in sessions:
            self.question.leave()
        if self.question.item is None:
            self._drafts[self._draft_key(self.selected)] = self.composer.text
        cached = {session.id: session for session in self.sessions}
        self.sessions = [SessionView(s.title, s.status,
                                      cached[s.id].messages if s.id in cached else (),
                                      s.id)
                         for s in display.sessions]
        self.no_sessions = not bool(self.sessions)
        if self.no_sessions:
            self.sessions = [SessionView(self.t(MessageKey.WELCOME_TITLE))]
        for index, session in enumerate(self.sessions):
            if session.id == snapshot.selected_id:
                self.sessions[index] = replace(session, messages=display.messages)
        target = select_id or (snapshot.selected_id if changing_project else current_id) or snapshot.selected_id
        self.selected = next((i for i, s in enumerate(self.sessions) if s.id == target), 0)
        changed = current_id != self.sessions[self.selected].id
        if changed:
            self.output.deactivate()
            self.composer.text = self._drafts.get(self._draft_key(self.selected), "")
            if empty_draft_key and not self.no_sessions and not changing_project:
                self.composer.text = self._drafts.pop(empty_draft_key, "")
            position = self._positions.get(self._draft_key(self.selected))
            self.output.set_messages(self.sessions[self.selected].messages, position)
            if position is None and self.general.auto_scroll:
                self.output.set_follow_tail(True)
        else:
            # Preserve a reader's scroll position. New/updated output follows the
            # bottom only if the reader was already at the bottom.
            messages = self.sessions[self.selected].messages
            self.output.update_messages(messages, auto_scroll=self.general.auto_scroll)
        self.connected = True
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
        if self.agent_id not in self.agents:
            self.agent_id = next(iter(self.agents), "")
        if snapshot.file_root:
            self.file_root = snapshot.file_root
            self.output.set_file_root(snapshot.file_root)
        if self.sessions[self.selected].id == snapshot.selected_id:
            self.question.sync(snapshot.project_id, snapshot.selected_id, snapshot.questions)
            self.notice = display.notice
        self.preparing.paint()
        if self.sessions[self.selected].id == snapshot.selected_id:
            self.followup.refresh((snapshot.project_id, snapshot.selected_id), snapshot.messages)

    def set_theme(self, theme):
        super().set_theme(theme)
        callback = getattr(self, "on_output_interval", None)
        if callback:
            callback(theme.output_refresh_interval)
        if HubSnapshot.last_observed(self) is not None:
            self.apply_snapshot(HubSnapshot.require_observed(self))


class LiveController:
    def __init__(self, view: LiveHubView, config: HubConfig, *, worker_factory=BackendWorker):
        from ..config.preferences import configured_workspace
        config = configured_workspace(config)
        preferences = PreferencesStore(config._workspace_anchor).load()
        if "profile" in preferences:
            config = replace(config, user_profile=UserProfile(**preferences["profile"]))
        if "appearance" in preferences:
            view.set_theme(HubTheme(**preferences["appearance"]))
        view.general = GeneralSettings(**preferences.get("general", {}))
        view.user_profile = config.user_profile
        self.view = view
        self.config = config
        self.worker_factory = worker_factory
        self.worker = None
        self._sidebar_width_pending = None
        HubView.bind_sidebar_width(self.view, self._save_sidebar_width)
        self.view.on_output_interval = self._set_output_interval
        self.app = None
        self.loop = None
        self.closed = False
        self.requests = UIRequests(lambda: self.worker, self._dispatch,
            active=lambda: not self.closed, connected=lambda: self.view.connected,
            unavailable=lambda: self.view.t(MessageKey.NOT_CONNECTED))
        self._snapshot_inbox = SnapshotInbox()
        self._submitting = set()
        self._choosing_submission = False
        self._select_new = None
        self._view_state = ViewStateStore(config.workspace)
        self._view_writer = ViewStateWriter(self._view_state, lambda error: self._dispatch(self._error, error))
        self._state_project = None
        self._restoring_snapshot = None
        self._save_timer = None
        self._last_notification = 0
        self._diagnosing = set()
        self.view._default_agent_id = config.agent_id
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
        self.view.on_interrupt = self.interrupt
        from .chat.history_ui import HistoryUI
        self.history_ui = HistoryUI(self)
        self.view.on_history = self.history_ui.open
        self.view.on_activity = self.history_ui.activity
        self.view.on_turns = lambda session_id, ready: self._call("turns", session_id, completed=ready)
        self.view.on_save_view = self._save_view
        self.view.settings.request = self._settings_request
        from ..config.restart import startup_settings
        self.view.settings.drafts.applied_preferences = startup_settings(config, view.general)
        self.view.settings.drafts.workspace_anchor = config._workspace_anchor
        self.view.on_output_position_changed = self._reading_changed
        self.view.file_root = config.workspace if config.file_root is None else config.file_root
        self.environment_provider = None
        if config.engine_factories:
            self.view.engines = tuple(config.engine_factories)

    def _dispatch(self, callback, *args):
        if not self.closed and self.loop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(callback, *args)

    def _publish(self, snapshot):
        available = not self.closed and self.loop is not None and not self.loop.is_closed()
        if self._snapshot_inbox.offer(snapshot, can_schedule=available):
            self._dispatch(self._consume_snapshot)

    def _consume_snapshot(self):
        self._snapshot(self._snapshot_inbox.take())

    def _error(self, error):
        if self.closed:
            return
        info = self.view.operation_diagnostics.record(self.view.project_id, error)
        message = operation_notice(error if isinstance(error, str) else info, self.view.t)
        self.view.notice = self.view.t(MessageKey.ERROR, error=message)
        self.view.toasts.alert(self.view.sessions[self.view.selected].title, message, "error", "backend-error")
        self.app.invalidate()

    def _snapshot(self, snapshot):
        if self.closed or snapshot is None:
            return
        restore_selection = None
        changing_project = self._state_project != snapshot.project_id
        if changing_project and not self._view_writer.is_loaded(snapshot.project_id):
            self._restoring_snapshot = snapshot
            def loaded(error):
                def apply():
                    if error:
                        self._error(error)
                    latest = self._restoring_snapshot
                    if latest and latest.project_id == snapshot.project_id:
                        self._restoring_snapshot = None
                        self._snapshot(latest)
                self._dispatch(apply)
            self._view_writer.load_background(snapshot.project_id, loaded)
            return
        self._restoring_snapshot = None
        if changing_project:
            if self.view.project_id:
                self.view.remember_position()
                self._save_view()
                self.view._positions.clear()
            self._state_project = snapshot.project_id
            try:
                selected, positions = self._view_writer.load(snapshot.project_id)
                self.view._positions.update(positions)
                explicit = self.config.session_id and self.config.project_id == snapshot.project_id
                if self.view.general.restore_last_session and not explicit and any(session.id == selected for session in snapshot.sessions):
                    self._select_new = selected
                    if selected != snapshot.selected_id:
                        restore_selection = selected
            except (OSError, ValueError, TypeError) as error:
                self._error(error)
        target = self._select_new
        self.view.apply_snapshot(snapshot, select_id=target)
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
            if (notification.status in (SessionSummary.WAITING_INPUT, SessionSummary.WAITING_APPROVAL)
                    and notification.project_id == snapshot.project_id
                    and displayed_status.get(notification.session_id) != notification.status):
                continue  # A coalesced snapshot can already contain the answer.
            if (notification.status in (SessionSummary.WAITING_INPUT, SessionSummary.WAITING_APPROVAL) or not self.view.visible
                    or (notification.project_id, notification.session_id) != current):
                self.view.toasts.push(notification.title, notification.status,
                    (notification.project_id, notification.session_id))
        if target and any(s.id == target for s in snapshot.sessions):
            self._select_new = None
        if changing_project:
            self._save_view()
        self.app.invalidate()
        if restore_selection is not None:
            self._call("select", restore_selection)

    def _settings_request(self, operation, args, completed):
        def received(result):
            if result.error is not None:
                completed(None, result.error)
                return
            if operation == "activate_project":
                self._snapshot(result.value)
            elif operation == "delete_project":
                self._call("snapshot", completed=self._snapshot)
            completed(result.value)
        self.requests.call("settings", operation, *args, completed=received,
                           allow_disconnected=operation == "catalog")

    def _diagnose(self, snapshot):
        identity = snapshot.project_id, snapshot.selected_id
        candidates = [item.run_id for item in snapshot.messages if item.role == "assistant"
                      and item.status == MessageStatus.FAILED and item.run_id][-256:]
        missing = [rid for rid in candidates
                   if (*identity, rid) not in self.view.failure_diagnoses
                   and (*identity, rid) not in self._diagnosing][-64:]
        if not missing:
            return
        self._diagnosing.update((*identity, rid) for rid in missing)
        def received(result):
            from ..errors.contracts import ErrorInfo
            for rid in missing:
                key = (*identity, rid)
                self._diagnosing.discard(key)
                self.view.failure_diagnoses[key] = (result.value or {}).get(rid, ErrorInfo())
            while len(self.view.failure_diagnoses) > 256:
                self.view.failure_diagnoses.pop(next(iter(self.view.failure_diagnoses)))
            latest = HubSnapshot.last_observed(self.view)
            if latest and (latest.project_id, latest.selected_id) == identity:
                self.view.apply_snapshot(latest)
                self.app.invalidate()
        self.requests.call("diagnose_failures", *identity, missing, completed=received)

    def _save_view(self):
        if self._save_timer:
            self._save_timer.cancel()
            self._save_timer = None
        if self.closed or not self.view.project_id:
            return
        try:
            self._view_writer.save(self.view.project_id, self.view.sessions[self.view.selected].id,
                                  {key: value for key, value in self.view._positions.items() if not key.startswith("sample:")})
        except (OSError, ValueError, TypeError) as error:
            self.view.notice = self.view.t(MessageKey.VIEW_STATE_ERROR, error=error)

    def _reading_changed(self):
        self.view.remember_position()
        if self._save_timer:
            self._save_timer.cancel()
        if self.loop is not None and not self.closed:
            self._save_timer = self.loop.call_later(0.25, self._save_view)

    def _call(self, operation, *args, completed=None, acknowledged=None, progress=None):
        def received(result):
            if result.error is not None:
                self._error(result.error)
            if completed:
                completed(result.value)
        self.requests.call(operation, *args, completed=received,
                           acknowledged=acknowledged, progress=progress)

    def _set_output_interval(self, seconds):
        if self.worker:
            self.worker.set_output_interval(seconds)

    def _save_sidebar_width(self, width):
        self._sidebar_width_pending = width
        if self.worker:
            self.worker.set_sidebar_width(width)
            self._sidebar_width_pending = None

    def open(self, app):
        self.app = app
        self.refresh_shell_root()
        self.view.toasts.app = app
        self.loop = asyncio.get_running_loop()
        if self.worker is None:
            self.worker = self.worker_factory(
                self.config, self._publish,
                lambda error: self._dispatch(self._error, error))
            self._set_output_interval(self.view.theme.output_refresh_interval)
            if self._sidebar_width_pending is not None:
                self._save_sidebar_width(self._sidebar_width_pending)
        else:
            self._call("select", self.view.sessions[self.view.selected].id)

    def bind_app(self, app):
        """Rebind callbacks when ish starts its next prompt event loop."""
        loop = asyncio.get_running_loop()
        self.refresh_shell_root()
        if self.loop is not loop and not self.closed:
            self.app, self.loop = app, loop
            self._snapshot_inbox.rebind()
            self.view.toasts.app = app
            if self.worker:
                self._call("snapshot", completed=self._snapshot)

    def refresh_shell_root(self):
        if self.environment_provider is not None:
            try:
                environment = self.environment_provider()
            except ValueError:
                return  # Shell context can arrive after plugin installation.
            self.view.file_root = environment.cwd
            self.view.output.set_file_root(environment.cwd)

    def _accept_input(self, project_id, session_id, text, result, operation):
        if result is None or self.closed:
            return
        view = self.view
        if isinstance(result, SubmissionResult):
            if result.message is None:
                return
            view.preparing.accepted((project_id, session_id), result.message.id)
            view.show_submitted(project_id, session_id, result.message)
        if view.project_id != project_id:
            return
        current_id = view.sessions[view.selected].id
        if current_id == session_id:
            view.notice = view.t(MessageKey.INSTRUCTION_SAVED if operation == "steer" else MessageKey.SAVED)
            if view.general.auto_scroll:
                view.output.restore_position(None)
                view.output.set_follow_tail(True)
        self.app.invalidate()

    def _send(self, operation, session_id, text, *args, draft=None):
        if not text.strip() or session_id in self._submitting or not self.view.connected:
            if draft:
                draft.restore(self.view)
            return
        self._submitting.add(session_id)
        project_id = self.view.project_id
        from .input.submission import SubmittedDraft
        if draft is None:
            draft = SubmittedDraft(project_id, session_id, text)
            draft.consume(self.view)
        if operation == "submit_input":
            self.view.preparing.start((project_id, session_id), text)
        def accepted(request_id):
            self._submitting.discard(session_id)
            if request_id is None:
                self.view.preparing.finish((project_id, session_id))
                draft.restore(self.view)
            self._accept_input(project_id, session_id, text, request_id, operation)
        self._call(operation, session_id, *args, completed=accepted)

    def review_approval(self, request):
        view = self.view
        identity = view.question.identity
        def ready(text):
            if text is None or identity != view.question.identity or view._dialog is not None or not view.visible:
                return
            from ..widget.output.reader import ReadOnlyDialog
            popup = ReadOnlyDialog(request["title"], text, view._rows, view._columns,
                view.dialogs.close, view.t, markdown=True, theme=lambda: view.theme, render_worker=view.render_worker)
            view.dialogs.show(popup, popup.receiver)
        self._call("approval_details", request, completed=ready)

    def answer_question(self, item, option_id, value):
        question = self.view.question
        if question.busy:
            return
        response = question.begin_answer(option_id)
        identity = response.identity
        self.app.invalidate()
        def acknowledged():
            response.acknowledged = True
            self.view.preparing.paint()
        def answered(result):
            question.finish_answer(response, bool(result))
        self._call("answer_question", identity[0], identity[1], item["run_id"], item["request"],
                   option_id, value, completed=answered, acknowledged=acknowledged)

    def submit(self, session_id: str, text: str):
        if not session_id:
            self.view.notice = self.view.t(MessageKey.SESSION_REQUIRED)
            self.view.session_dialog("new")
            return
        if not text.strip() or session_id in self._submitting or self._choosing_submission:
            return
        view = self.view
        identity = (view.project_id, session_id)
        agent_id, options = view.agent_id, {}
        if self.environment_provider is not None:
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
        draft = SubmittedDraft(identity[0], session_id, text)
        draft.consume(view)
        self._choosing_submission = True
        self._submitting.add(session_id)
        view.preparing.start(identity, text)

        def ready(state):
            self._choosing_submission = False
            self._submitting.discard(session_id)
            if state is not None and state.message is not None:
                self._accept_input(identity[0], session_id, text, state, "submit")
                return
            view.preparing.finish(identity)
            if (state is None or self.closed or not view.visible or view.settings_open or view._dialog is not None
                    or identity != (view.project_id, view.sessions[view.selected].id)):
                draft.restore(view)
                return
            def follow_up():
                self._send("submit_input", session_id, text, text, agent_id, options, False, draft=draft)
            from ..widget.controls import RadioList
            choices = RadioList([("steer", view.t(MessageKey.SEND_STEERING)), ("follow_up", view.t(MessageKey.SEND_FOLLOW_UP))],
                                default="follow_up", select_on_focus=True)
            def accept():
                if choices.current_value == "steer":
                    self.steer(session_id, text, expected_run_id=state.run_id, draft=draft)
                else:
                    follow_up()
            view.open_dialog(view.t(MessageKey.SEND_WHILE_RUNNING), choices, accept, choices,
                             on_cancel=lambda: draft.restore(view))
            # This override and its hint share the choice widget's registry.
            def confirm(event):
                view.dialogs.close(accepted=True)
                accept()
            choices.control.hub_shortcuts.add(["enter"], "", "confirm", confirm)
        self._call("submit_input", session_id, text, agent_id, options, completed=ready)

    def steer(self, session_id: str, text: str, *, expected_run_id=None, draft=None):
        if not text.strip() or session_id in self._submitting:
            if draft:
                draft.restore(self.view)
            return
        def ready(value):
            if value is None:
                if draft:
                    draft.restore(self.view)
                return
            run_id, targets = value
            if expected_run_id is not None and run_id != expected_run_id:
                self._error(self.view.t(MessageKey.INSTRUCTION_RUN_CHANGED))
                if draft:
                    draft.restore(self.view)
                return
            if len(targets) == 1:
                self._send("steer", session_id, text, run_id, text, [targets[0][0]], draft=draft)
            else:
                from ..widget.controls import RadioList
                choices = RadioList(list(targets), select_on_focus=True)
                self.view.open_dialog(self.view.t(MessageKey.CHOOSE_TARGET), choices,
                    lambda: self._send("steer", session_id, text, run_id, text, [choices.current_value], draft=draft), choices,
                    on_cancel=lambda: draft and draft.restore(self.view))
        self._call("instruction_targets", session_id, expected_run_id, completed=ready)

    def select(self, session_id: str):
        if session_id:
            self._call("select", session_id)

    def new_session(self, mode="new", title="", source_id=None, through_message_id=None):
        def created(session_id):
            if session_id is None:
                return
            self._select_new = session_id
            self._call("snapshot", completed=self._snapshot)
        self._call("new_session", mode, title, source_id, through_message_id, completed=created)

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
        if (snapshot and snapshot.selected_id == session_id and snapshot.run
                and not snapshot.run.active and snapshot.run.status == RunStatus.INTERRUPTED
                and not snapshot.queued_count and key not in self.view.preparing.pending):
            from .chat.resume import resume_turn
            resume_turn(self, session_id, snapshot.run.id)
            return
        request = self.view.interrupting.start(key)
        if request:
            self.view.question.leave()
        waiting = self.view.t(MessageKey.CANCEL_WAIT)
        def explain_delay():
            if not self.closed and request and self.view.interrupting.pending.get(key) is request:
                self.view.notice = waiting
                self.app.invalidate()
        monitor = self.loop.call_later(5, explain_delay) if request else None
        def interrupted(result):
            if monitor:
                monitor.cancel()
            if self.view.notice == waiting:
                self.view.notice = ""
            self.view.interrupting.finish(request, result)
            self._call("snapshot", completed=self._snapshot)
        self._call("interrupt", session_id, completed=interrupted)

    def rename_session(self, session_id: str, title: str):
        self._call("rename_session", session_id, title,
                   completed=lambda result: result is not None and self._call("snapshot", completed=self._snapshot))

    def delete_session(self, session_id: str):
        if not session_id:
            return
        def deleted(selected_id):
            if selected_id is None:
                return
            self.view._drafts.pop(session_id, None)
            self.view._positions.pop(session_id, None)
            self._select_new = selected_id
            self._call("snapshot", completed=self._snapshot)
        self._call("delete_session", session_id, completed=deleted)

    def _prepare_close(self):
        if self.closed:
            return False
        self.view.close()
        self.view.remember_position()
        self._save_view()
        self.closed = True
        return True

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
