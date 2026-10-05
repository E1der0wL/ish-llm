"""Session creation, clone-boundary loading and naming workflows."""
from prompt_toolkit.application.current import get_app
from prompt_toolkit.widgets import Label
from ...widget.controls import TextArea
from ...widget.session_form import SessionForm

class SessionActions:
    def __init__(self, view):
        self.view = view

    def perform(self, key):
        view = self.view
        if key in ("c", "r"):
            self.create("new" if key == "c" else "clone")
        elif key == "e":
            self.rename()
        elif key == "d" and not view.no_sessions:
            session = view.sessions[view.selected]
            view.open_dialog(view.t("session_delete"), Label(view.t("session_delete_confirm", title=session.title)),
                             lambda: view.on_delete and view.on_delete(session.id))

    def create(self, mode="new", title="", *, turns=None, through_message_id=None):
        view = self.view
        if view.no_sessions and mode == "clone":
            view.notice = view.t("session_required")
            return
        source_id = view.sessions[view.selected].id
        ready = view.no_sessions or turns is not None or view.on_turns is None
        from .history_ui import turn_label
        form = SessionForm(view.t, mode=mode, title=title, allow_clone=not view.no_sessions,
                           turns=turns, boundary=through_message_id, turn_label=turn_label)
        choices, name, boundary = form.mode, form.name, form.boundary
        identity = (getattr(view, "project_id", ""), source_id)
        def create():
            if view.on_new:
                args = (choices.current_value, name.text.strip(), source_id)
                if choices.current_value == "clone" and boundary.current_value is not None:
                    view.on_new(*args, boundary.current_value)
                else:
                    view.on_new(*args)
        def accept_name(buffer):
            if validate():
                view.close_dialog()
                create()
            return True
        def validate():
            if choices.current_value == "clone" and not ready:
                view.notice = view.t("settings_loading")
                return False
            return True
        name.buffer.accept_handler = accept_name
        view.open_dialog(view.t("create_dialog"), form, create, name, validate=validate)
        pending = view._dialog
        if not ready:
            def loaded(rows):
                nonlocal ready
                if (rows is not None and view._dialog is pending and identity ==
                        (getattr(view, "project_id", ""), view.sessions[view.selected].id)):
                    ready = True
                    form.set_turns(rows, through_message_id)
                    get_app().invalidate()
            view.on_turns(source_id, loaded)

    def rename(self):
        view = self.view
        if view.no_sessions:
            return
        session = view.sessions[view.selected]
        name = TextArea(text=session.title, height=1, multiline=False)
        def rename():
            if view.on_rename:
                view.on_rename(session.id, name.text.strip())
        def validate():
            if name.text.strip():
                return True
            view.notice = view.t("session_name_required")
            return False
        view.open_dialog(view.t("session_rename"), name, rename, name, validate=validate)
        def accept(_):
            if validate():
                view.close_dialog()
                rename()
            return True
        name.buffer.accept_handler = accept

