"""Session creation, clone-boundary loading and naming workflows."""

from hub.locales.keys import MessageKey
from hub.backend.session_contracts import SessionMode
from hub.model import HubSnapshot, SessionIdentity
from ..input.keys import KeyName
from prompt_toolkit.application.current import get_app
from prompt_toolkit.widgets import Label
from ...widget.controls import TextArea
from ...widget.forms.session_form import SessionForm


class SessionActions:
    CREATE_KEYS = (KeyName.C, KeyName.R)
    EMPTY_TITLE = ""
    ABSENT = None
    READY = True
    VALID = True
    INVALID = False
    INPUT_HANDLED = True
    NAME_HEIGHT = 1
    NAME_MULTILINE = False

    def __init__(self, view):
        self.view = view

    def perform(self, key):
        view = self.view
        if key in self.CREATE_KEYS:
            self.create(
                SessionMode.NEW.value if key == KeyName.C else SessionMode.CLONE.value
            )
        elif key == KeyName.EDIT:
            self.rename()
        elif key == KeyName.CLEAR and not view.no_sessions:
            session = view.sessions[view.selected]
            view.open_dialog(
                view.t(MessageKey.SESSION_DELETE),
                Label(view.t(MessageKey.SESSION_DELETE_CONFIRM, title=session.title)),
                lambda: view.on_delete and view.on_delete(session.id),
            )

    def create(
        self,
        mode=SessionMode.NEW.value,
        title=EMPTY_TITLE,
        *,
        turns=ABSENT,
        through_message_id=ABSENT
    ):
        view = self.view
        if view.no_sessions and mode == SessionMode.CLONE:
            view.notice = view.t(MessageKey.SESSION_REQUIRED)
            return
        source_id = view.sessions[view.selected].id
        ready = (
            view.no_sessions or turns is not self.ABSENT or view.on_turns is self.ABSENT
        )
        from ...widget.summary import turn_label

        form = SessionForm(
            view.t,
            mode=mode,
            title=title,
            allow_clone=not view.no_sessions,
            turns=turns,
            boundary=through_message_id,
            turn_label=turn_label,
        )
        choices, name, boundary = form.mode, form.name, form.boundary
        identity = (HubSnapshot.project_identifier(view), source_id)

        def create():
            if view.on_new:
                args = (choices.current_value, name.text.strip(), source_id)
                if (
                    choices.current_value == SessionMode.CLONE
                    and boundary.current_value is not self.ABSENT
                ):
                    view.on_new(*args, boundary.current_value)
                else:
                    view.on_new(*args)

        def accept_name(buffer):
            if validate():
                view.close_dialog()
                create()
            return self.INPUT_HANDLED

        def validate():
            if choices.current_value == SessionMode.CLONE and not ready:
                view.notice = view.t(MessageKey.SETTINGS_LOADING)
                return self.INVALID
            return self.VALID

        name.buffer.accept_handler = accept_name
        view.open_dialog(
            view.t(MessageKey.CREATE_DIALOG), form, create, name, validate=validate
        )
        pending = view._dialog
        if not ready:

            def loaded(rows):
                nonlocal ready
                if (
                    rows is not self.ABSENT
                    and view._dialog is pending
                    and identity == SessionIdentity.current(view).to_tuple()
                ):
                    ready = self.READY
                    form.set_turns(rows, through_message_id)
                    get_app().invalidate()

            view.on_turns(source_id, loaded)

    def rename(self):
        view = self.view
        if view.no_sessions:
            return
        session = view.sessions[view.selected]
        name = TextArea(
            text=session.title, height=self.NAME_HEIGHT, multiline=self.NAME_MULTILINE
        )

        def rename():
            if view.on_rename:
                view.on_rename(session.id, name.text.strip())

        def validate():
            if name.text.strip():
                return self.VALID
            view.notice = view.t(MessageKey.SESSION_NAME_REQUIRED)
            return self.INVALID

        view.open_dialog(
            view.t(MessageKey.SESSION_RENAME), name, rename, name, validate=validate
        )

        def accept(_):
            if validate():
                view.close_dialog()
                rename()
            return self.VALID

        name.buffer.accept_handler = accept
