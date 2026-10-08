"""UI-thread controller; backend handles never cross into PTK callbacks."""

import asyncio
from dataclasses import replace

from .mockup import HubMockup, SampleSession
from ..backend.runtime import HubConfig
from ..model import HubSnapshot, SubmissionResult
from .presentation import present, present_message
from ..backend.worker import BackendWorker
from ..config.view_state import ViewStateStore, ViewStateWriter
from ..config.preferences import PreferencesStore
from ..config.profile import UserProfile
from ..config.general import GeneralSettings
from ..config.theme import HubTheme
from ..backend.component_commands import component_commands
from .chat.component_ui import ComponentCommands


class LiveHubView(HubMockup):
    def __init__(self, theme=None, language="ko", language_packs=None):
        super().__init__(theme, language, language_packs)
        self.sessions = [SampleSession(self.t("connecting"), "", (), "")]
        self.transcript.set_messages(())
        self.project_title = self.t("connecting")
        self.project_id = ""
        self.model = ""
        self.engine = ""
        self.connected = False
        self.notice = self.t("connecting_notice")
        self._submitted_messages = {}

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
                    self.transcript.control.messages = messages
                return

    def apply_snapshot(self, snapshot: HubSnapshot, *, select_id=None) -> None:
        self._last_snapshot = snapshot
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
        display = present(snapshot, self.t)
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
        self.sessions = [SampleSession(s.title, s.status,
                                      cached[s.id].messages if s.id in cached else (),
                                      cached[s.id].detail if s.id in cached else "", s.id)
                         for s in display.sessions]
        self.no_sessions = not bool(self.sessions)
        if self.no_sessions:
            self.sessions = [SampleSession(self.t("welcome_title"), "", (), "", "")]
        for index, session in enumerate(self.sessions):
            if session.id == snapshot.selected_id:
                self.sessions[index] = replace(session, messages=display.messages, detail=display.detail)
        target = select_id or (snapshot.selected_id if changing_project else current_id) or snapshot.selected_id
        self.selected = next((i for i, s in enumerate(self.sessions) if s.id == target), 0)
        changed = current_id != self.sessions[self.selected].id
        if changed:
            if self.search.active:
                self.close_dialog()
            self.composer.text = self._drafts.get(self._draft_key(self.selected), "")
            if empty_draft_key and not self.no_sessions and not changing_project:
                self.composer.text = self._drafts.pop(empty_draft_key, "")
            position = self._positions.get(self._draft_key(self.selected))
            self.transcript.set_messages(self.sessions[self.selected].messages, position)
            if position is None and self.general.auto_scroll:
                self.transcript.control.follow_tail = True
        else:
            # Preserve a reader's scroll position. New/updated output follows the
            # bottom only if the reader was already at the bottom.
            control = self.transcript.control
            info = self.transcript.window.render_info
            messages = self.sessions[self.selected].messages
            following = not control.restoring and (
                not control.messages or (info is not None and
                    control.top_line + control._height >= info.content_height - 1))
            changed_messages = control.messages != messages
            control.messages = messages
            if self.general.auto_scroll and following and changed_messages and not control.search_query:
                control.follow_tail = True
            elif not self.general.auto_scroll:
                control.follow_tail = False
        self.connected = True
        self.project_title = snapshot.project_title
        self.project_id = snapshot.project_id
        self.model = snapshot.model
        commands = component_commands(snapshot.components)
        if commands != self.component_commands:
            self.composer.buffer.cancel_completion()
            self.component_commands = commands
        self.engines = snapshot.engines
        if self.engine not in self.engines:
            self.engine = next(iter(self.engines), "")
        if snapshot.file_root:
            self.file_root = snapshot.file_root
            self.transcript.control.file_root = snapshot.file_root
        if self.sessions[self.selected].id == snapshot.selected_id:
            self.question.sync(snapshot.project_id, snapshot.selected_id, snapshot.questions)
            self.notice = display.notice
            self.activity = display.activity

    def set_theme(self, theme):
        super().set_theme(theme)
        callback = getattr(self, "on_output_interval", None)
        if callback:
            callback(theme.output_refresh_interval)
        if getattr(self, "_last_snapshot", None) is not None:
            self.apply_snapshot(self._last_snapshot)


class LiveController:
    def __init__(self, view: LiveHubView, config: HubConfig, *, worker_factory=BackendWorker):
        preferences = PreferencesStore(config.workspace).load()
        if "profile" in preferences:
            config = replace(config, user_profile=UserProfile(**preferences["profile"]))
        if "appearance" in preferences:
            view.set_theme(HubTheme(**preferences["appearance"]))
        view.general = GeneralSettings(**preferences.get("general", {}))
        self.view = view
        self.config = config
        self.worker_factory = worker_factory
        self.worker = None
        self.view.on_output_interval = self._set_output_interval
        self.app = None
        self.loop = None
        self.closed = False
        self._submitting = set()
        self._choosing_submission = False
        self._select_new = None
        self._view_state = ViewStateStore(config.workspace)
        self._view_writer = ViewStateWriter(self._view_state, lambda error: self._dispatch(self._error, error))
        self._state_project = None
        self._save_timer = None
        self._last_notification = 0
        self.view.engine = config.engine
        self.view.on_open = self.open
        self.view.on_submit = self.submit
        self.view.on_answer = self.answer_question
        self.view.on_select = self.select
        self.view.on_new = self.new_session
        self.view.on_delete = self.delete_session
        self.view.on_rename = self.rename_session
        self.component_commands = ComponentCommands(self)
        self.view.on_component = self.component_commands.open
        self.view.on_interrupt = self.interrupt
        self.view.on_steer = self.steer
        self.view.on_choose_engine = self.choose_engine
        from .chat.history_ui import HistoryUI
        self.history_ui = HistoryUI(self)
        self.view.on_history = self.history_ui.open
        self.view.on_activity = self.history_ui.activity
        self.view.on_turns = lambda session_id, ready: self._call("turns", session_id, completed=ready)
        self.view.on_save_view = self._save_view
        self.view.settings.request = self._settings_request
        self.view.transcript.control.on_position_changed = self._reading_changed
        self.view.file_root = config.file_root or config.workspace
        if config.engine_factories:
            self.view.engines = tuple(config.engine_factories)

    def _dispatch(self, callback, *args):
        if not self.closed and self.loop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(callback, *args)

    def _error(self, error):
        if self.closed:
            return
        self.view.notice = self.view.t("error", error=error)
        self.view.toasts.alert(self.view.sessions[self.view.selected].title, str(error), "error", "backend-error")
        self.app.invalidate()

    def _snapshot(self, snapshot):
        if self.closed or snapshot is None:
            return
        restore_selection = None
        changing_project = self._state_project != snapshot.project_id
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
        current = (self.view.project_id, self.view.sessions[self.view.selected].id)
        for notification in snapshot.notifications:
            if notification.sequence <= self._last_notification:
                continue
            self._last_notification = notification.sequence
            if not self.view.visible or (notification.project_id, notification.session_id) != current:
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
        if not self.worker or (not self.view.connected and operation != "catalog"):
            completed(None, self.view.t("not_connected"))
            return
        try:
            future = self.worker.call("settings", operation, *args)
        except Exception as error:
            completed(None, error)
            return
        def finished(future):
            try:
                value = future.result()
            except Exception as error:
                self._dispatch(completed, None, error)
            else:
                def apply():
                    if operation == "activate_project":
                        self._snapshot(value)
                    completed(value)
                self._dispatch(apply)
        future.add_done_callback(finished)

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
            self.view.notice = self.view.t("view_state_error", error=error)

    def _reading_changed(self):
        self.view.remember_position()
        if self._save_timer:
            self._save_timer.cancel()
        if self.loop is not None and not self.closed:
            self._save_timer = self.loop.call_later(0.25, self._save_view)

    def _call(self, operation, *args, completed=None):
        if not self.worker or not self.view.connected:
            self._error(self.view.t("not_connected"))
            if completed:
                completed(None)
            return
        try:
            future = self.worker.call(operation, *args)
        except Exception as error:
            self._error(error)
            if completed:
                completed(None)
            return
        def finished(result):
            failure = None
            try:
                value = result.result()
            except Exception as error:
                failure = error
                value = None
            def apply():
                if failure is not None:
                    self._error(failure)
                if completed:
                    completed(value)
            self._dispatch(apply)
        future.add_done_callback(finished)

    def _set_output_interval(self, seconds):
        if self.worker:
            self.worker.set_output_interval(seconds)

    def open(self, app):
        self.app = app
        self.view.toasts.app = app
        self.loop = asyncio.get_running_loop()
        if self.worker is None:
            self.worker = self.worker_factory(
                self.config, lambda snapshot: self._dispatch(self._snapshot, snapshot),
                lambda error: self._dispatch(self._error, error))
            self._set_output_interval(self.view.theme.output_refresh_interval)
        else:
            self._call("select", self.view.sessions[self.view.selected].id)

    def bind_app(self, app):
        """Rebind callbacks when ish starts its next prompt event loop."""
        loop = asyncio.get_running_loop()
        if self.loop is not loop and not self.closed:
            self.app, self.loop = app, loop
            self.view.toasts.app = app
            if self.worker:
                self._call("snapshot", completed=self._snapshot)

    def _accept_input(self, project_id, session_id, text, result, operation):
        if result is None or self.closed:
            return
        view = self.view
        if isinstance(result, SubmissionResult):
            if result.message is None:
                return
            view.show_submitted(project_id, session_id, result.message)
            view.preparing.accepted((project_id, session_id), result.message.id)
        if view._drafts.get(session_id) == text:
            view._drafts[session_id] = ""
        if view.project_id != project_id:
            return
        current_id = view.sessions[view.selected].id
        if current_id == session_id and view.composer.text == text:
            view.composer.text = ""
        if current_id == session_id:
            view.notice = view.t("instruction_saved" if operation == "steer" else "saved")
            if view.general.auto_scroll:
                view.transcript.control.restore_position(None)
                view.transcript.control.follow_tail = True
        self.app.invalidate()

    def _send(self, operation, session_id, text, *args):
        if not text.strip() or session_id in self._submitting or not self.view.connected:
            return
        self._submitting.add(session_id)
        project_id = self.view.project_id
        if operation == "submit_input":
            self.view.preparing.start((project_id, session_id))
        def accepted(request_id):
            self._submitting.discard(session_id)
            if request_id is None:
                self.view.preparing.finish((project_id, session_id))
            self._accept_input(project_id, session_id, text, request_id, operation)
        self._call(operation, session_id, *args, completed=accepted)

    def answer_question(self, item, option_id, value):
        question = self.view.question
        if question.busy:
            return
        identity = question.identity
        question.busy = True
        def answered(result):
            if question.identity == identity:
                question.busy = False
                if result:
                    question.leave()
                    question.drafts.pop(identity, None)
                    self._call("snapshot", completed=self._snapshot)
        self._call("answer_question", identity[0], identity[1], item["run_id"], item["request"],
                   option_id, value, completed=answered)

    def submit(self, session_id: str, text: str):
        if not session_id:
            self.view.notice = self.view.t("session_required")
            self.view.session_dialog("new")
            return
        if not text.strip() or session_id in self._submitting or self._choosing_submission:
            return
        view = self.view
        identity = (view.project_id, session_id)
        engine, options = view.engine, dict(view.engine_options)
        self._choosing_submission = True
        self._submitting.add(session_id)
        view.preparing.start(identity)

        def ready(state):
            self._choosing_submission = False
            self._submitting.discard(session_id)
            if state is not None and state.message is not None:
                self._accept_input(identity[0], session_id, text, state, "submit")
                return
            view.preparing.finish(identity)
            if (state is None or self.closed or not view.visible or view.settings_open or view._dialog is not None
                    or identity != (view.project_id, view.sessions[view.selected].id)
                    or view.composer.text != text):
                return
            def follow_up():
                self._send("submit_input", session_id, text, text, engine, options, False)
            from ..widget.controls import RadioList
            choices = RadioList([("steer", view.t("send_steering")), ("follow_up", view.t("send_follow_up"))],
                                default="follow_up", select_on_focus=True)
            def accept():
                if choices.current_value == "steer":
                    self.steer(session_id, text, expected_run_id=state.run_id)
                else:
                    follow_up()
            view.open_dialog(view.t("send_while_running"), choices, accept, choices)
            # This override and its hint share the choice widget's registry.
            def confirm(event):
                view.close_dialog()
                accept()
            choices.control.hub_shortcuts.add(["enter"], "", "confirm", confirm)
        self._call("submit_input", session_id, text, engine, options, completed=ready)

    def choose_engine(self, name=""):
        from prompt_toolkit.widgets import Label
        from ..widget.execution import ExecutionChooser
        view = self.view
        identity = (view.project_id, view.sessions[view.selected].id)
        view.open_dialog(view.t("execution_title"), Label(view.t("settings_loading")))
        pending = view._dialog
        def ready(catalog):
            if view._dialog is not pending or identity != (view.project_id, view.sessions[view.selected].id):
                return
            view.close_dialog()
            if catalog is None:
                return
            if name in catalog["engines"] and catalog["engines"][name]["kind"] == "plain":
                view.set_execution(name, {})
            else:
                ExecutionChooser(view, catalog, name).open()
        self._call("execution_catalog", identity[1], completed=ready)

    def steer(self, session_id: str, text: str, *, expected_run_id=None):
        if not text.strip() or session_id in self._submitting:
            return
        def ready(value):
            if value is None:
                return
            run_id, targets = value
            if expected_run_id is not None and run_id != expected_run_id:
                self._error(self.view.t("instruction_run_changed"))
                return
            if len(targets) == 1:
                self._send("steer", session_id, text, run_id, text, [targets[0][0]])
            else:
                from ..widget.controls import RadioList
                choices = RadioList(list(targets), select_on_focus=True)
                self.view.open_dialog(self.view.t("choose_target"), choices,
                    lambda: self._send("steer", session_id, text, run_id, text, [choices.current_value]), choices)
        self._call("instruction_targets", session_id, completed=ready)

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
        self._call("interrupt", session_id)

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

    def close(self):
        if self.closed:
            return
        self.view.output_renderers.close()
        self.view.progress.close()
        self.view.remember_position()
        self._save_view()
        self.closed = True
        self._view_writer.close()
        if self.worker:
            self.worker.close()
