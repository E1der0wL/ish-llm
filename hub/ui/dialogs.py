"""Popup lifecycle, validation and focus restoration on the UI thread."""

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.containers import to_container
from prompt_toolkit.layout.layout import walk
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.utils import get_cwidth

from ..widget.controls import Button
from ..widget.dialog import Dialog


class DialogController:
    def __init__(self, view):
        self.view = view
        self.current = None
        self.previous_focus = None

    def show(self, dialog, focus):
        app = get_app()
        self._cancel_reader()
        if self.current is None:
            self.previous_focus = app.layout.current_control
        self.current = dialog
        app.layout.update_parents_relations()
        app.layout.focus(focus)
        app.invalidate()

    def open(self, title, body, accept=None, focus=None, *, validate=None, width=None, accept_text=None, on_cancel=None):
        def confirm():
            if validate and not validate():
                return
            self.close(accepted=True)
            if accept:
                accept()
        buttons = [Button(accept_text or self.view.t("confirm"), handler=confirm,
                          **({"width": get_cwidth(accept_text) + 2} if accept_text else {}))]
        if accept:
            buttons.append(Button(self.view.t("cancel"), handler=self.close))
        dialog = Dialog(title=title, body=body, buttons=buttons,
                        width=width or (lambda: Dimension(preferred=64, max=max(1, min(80, self.view._columns() - 4)))))
        dialog.on_cancel = on_cancel
        self.show(dialog, focus or buttons[0])

    def close(self, *, accepted=False, restore_focus=True):
        app, focus = get_app(), self.previous_focus
        on_close = getattr(self.current, "on_close", None)
        if on_close is not None:
            on_close()
            return
        on_cancel = getattr(self.current, "on_cancel", None)
        self._cancel_reader()
        self.current = None
        self.previous_focus = None
        if restore_focus:
            if focus in list(app.layout.find_all_controls()):
                app.layout.focus(focus)
            else:
                self.view.active_page.focus_main()
        app.invalidate()
        if on_cancel and not accepted:
            on_cancel()

    def _cancel_reader(self):
        if self.current is None:
            return
        dispose = getattr(self.current, "dispose", None)
        if dispose:
            dispose()
        output = getattr(self.current, "output", None)
        timer = getattr(output, "_resize_timer", None)
        if timer:
            timer.cancel()
            output._resize_timer = None
        self.view.render_worker.cancel("popup")

    def cycle(self, reverse=False):
        controls = [item.content for item in walk(to_container(self.current), skip_hidden=True)
                    if isinstance(item, Window) and item.content.is_focusable()]
        if controls:
            app = get_app()
            current = app.layout.current_control
            index = controls.index(current) if current in controls else -1
            app.layout.focus(controls[(index + (-1 if reverse else 1)) % len(controls)])

    def help(self):
        from ..widget.output.reader import ReadOnlyDialog
        view = self.view
        popup = ReadOnlyDialog(view.t("help_title"), view.t("help"), view._rows, view._columns,
                               self.close, view.t, markdown=True, theme=lambda: view.theme, render_worker=view.render_worker)
        self.show(popup, popup.receiver)
