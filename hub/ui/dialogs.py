"""Popup lifecycle, validation and focus restoration on the UI thread."""

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.containers import to_container
from prompt_toolkit.layout.layout import walk
from prompt_toolkit.layout.dimension import Dimension

from ..widget.controls import Button, RadioList
from ..widget.dialog import Dialog


class DialogController:
    def __init__(self, view):
        self.view = view
        self.current = None
        self.previous_focus = None

    def show(self, dialog, focus):
        app = get_app()
        if self.current is None:
            self.previous_focus = app.layout.current_control
        self.current = dialog
        app.layout.update_parents_relations()
        app.layout.focus(focus)
        app.invalidate()

    def open(self, title, body, accept=None, focus=None, *, validate=None, width=None):
        def confirm():
            if validate and not validate():
                return
            self.close()
            if accept:
                accept()
        buttons = [Button(self.view.t("confirm"), handler=confirm)]
        if accept:
            buttons.append(Button(self.view.t("cancel"), handler=self.close))
        dialog = Dialog(title=title, body=body, buttons=buttons,
                        width=width or (lambda: Dimension(preferred=64, max=max(1, min(80, self.view._columns() - 4)))))
        self.show(dialog, focus or buttons[0])

    def close(self):
        app, focus = get_app(), self.previous_focus
        on_close = getattr(self.current, "on_close", None)
        if on_close is not None:
            on_close()
            return
        if self.view.search.active:
            self.view.transcript.control.set_search("")
        self.current = None
        self.previous_focus = None
        if focus in list(app.layout.find_all_controls()):
            app.layout.focus(focus)
        else:
            self.view.active_page.focus_main()
        app.invalidate()

    def cycle(self, reverse=False):
        controls = [item.content for item in walk(to_container(self.current), skip_hidden=True)
                    if isinstance(item, Window) and item.content.is_focusable()]
        if controls:
            app = get_app()
            current = app.layout.current_control
            index = controls.index(current) if current in controls else -1
            app.layout.focus(controls[(index + (-1 if reverse else 1)) % len(controls)])

    def help(self):
        from ..widget.reader import ReadOnlyDialog
        view = self.view
        popup = ReadOnlyDialog(view.t("help_title"), view.t("help"), view._rows, view._columns,
                               self.close, view.t, markdown=True, theme=lambda: view.theme)
        self.show(popup, popup.receiver)

    def engine(self, name=""):
        view = self.view
        if view.on_choose_engine:
            view.on_choose_engine(name)
        elif name in view.engines:
            view.engine = name
        else:
            choices = RadioList([(engine, engine) for engine in view.engines], default=view.engine, select_on_focus=True)
            self.open(view.t("choose_engine"), choices,
                      lambda: setattr(view, "engine", choices.current_value), choices)
