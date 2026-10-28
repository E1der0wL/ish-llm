"""Popup lifecycle, validation and focus restoration on the UI thread."""

from hub.ui.output.worker import RenderSlot


from hub.locales.keys import MessageKey

from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.containers import to_container
from prompt_toolkit.layout.controls import UIControl
from prompt_toolkit.layout.layout import walk
from prompt_toolkit.utils import get_cwidth

from ..widget.controls import Button
from ..widget.dialog import Dialog, PopupSize
from ..widget.focus import focus_control


class DialogController:
    ABSENT = None
    ACCEPTED = True
    NOT_ACCEPTED = False
    RESTORE_FOCUS = True
    FORWARD = False
    SKIP_HIDDEN = True
    MARKDOWN_HELP = True
    BUTTON_PADDING = 2
    PRIMARY_BUTTON = 0
    NO_FOCUS = -1
    REVERSE_STEP = -1
    FORWARD_STEP = 1

    def clear_state(self):
        self.current = DialogController.ABSENT
        self.previous_focus = DialogController.ABSENT
        self._focus = DialogController.ABSENT

    def __init__(self, view):
        self.view = view
        self.clear_state()

    @staticmethod
    def _controls(container) -> list[UIControl]:
        return [item.content for item in walk(to_container(container), skip_hidden=DialogController.SKIP_HIDDEN)
                if isinstance(item, Window) and item.content.is_focusable()]

    def suspend(self, app: Application) -> None:
        """Hide with Hub without closing, cancelling or disposing the popup."""
        if self.current is not None and app.layout.current_control in self._controls(self.current):
            self._focus = app.layout.current_control

    def resume(self, app: Application) -> bool:
        if self.current is None:
            return False
        app.layout.update_parents_relations()
        controls = self._controls(self.current)
        if controls:
            # Resuming must retain the cursor and selection, unlike entering a
            # new input through focus_control(). Fall back if a loading body changed.
            app.layout.focus(self._focus if self._focus in controls else controls[0])
        return True

    def dispose(self) -> None:
        """Release the retained popup when the owning Hub is shut down."""
        self._cancel_reader()
        self.clear_state()

    def show(self, dialog, focus):
        app = get_app()
        self._cancel_reader()
        if self.current is DialogController.ABSENT:
            self.previous_focus = app.layout.current_control if self.view.visible else None
        self.current = dialog
        if isinstance(dialog, Dialog) and dialog.status_bar is None:
            dialog.status_bar = self.view.progress.container(lambda: self.view.notice, surface=dialog)
        app.layout.update_parents_relations()
        if self.view.visible:
            focus_control(focus)
            self._focus = app.layout.current_control
        else:
            # Async loading may replace the popup while the user is in the shell.
            controls = [focus] if isinstance(focus, UIControl) else self._controls(focus)
            self._focus = controls[0] if controls else None
        app.invalidate()

    def open(
        self,
        title,
        body,
        accept=ABSENT,
        focus=ABSENT,
        *,
        validate=ABSENT,
        size=PopupSize.MEDIUM,
        accept_text=ABSENT,
        on_cancel=ABSENT,
        allow_small_input=False,
    ):
        def confirm():
            if validate and not validate():
                return
            self.close(accepted=DialogController.ACCEPTED)
            if accept:
                accept()

        label = accept_text or self.view.t(MessageKey.CONFIRM)
        button = (
            Button(
                label,
                handler=confirm,
                width=get_cwidth(accept_text) + DialogController.BUTTON_PADDING,
            )
            if accept_text
            else Button(label, handler=confirm)
        )
        buttons = [button]
        if accept:
            buttons.append(Button(self.view.t(MessageKey.CANCEL), handler=self.close))
        dialog = Dialog(
            title=title,
            body=body,
            buttons=buttons,
            size=size,
            allow_small_input=allow_small_input,
        )
        dialog.on_cancel = on_cancel
        self.show(dialog, focus or buttons[DialogController.PRIMARY_BUTTON])

    def loading(self, title, *, size=PopupSize.MEDIUM):
        self.open(title, Window(), size=size)
        popup = self.current
        popup.status_bar = self.view.progress.container(
            lambda: self.view.t(MessageKey.SETTINGS_LOADING), surface=popup
        )

    def close(self, *, accepted=NOT_ACCEPTED, restore_focus=RESTORE_FOCUS):
        app, focus = get_app(), self.previous_focus
        on_close = Dialog.close_handler(self.current)
        if on_close is not DialogController.ABSENT:
            on_close()
            return
        on_cancel = Dialog.cancel_handler(self.current)
        self._cancel_reader()
        self.clear_state()
        if restore_focus and self.view.visible:
            if focus in list(app.layout.find_all_controls()):
                focus_control(focus)
            else:
                self.view.active_page.focus_main()
        app.invalidate()
        if on_cancel and not accepted:
            on_cancel()

    def _cancel_reader(self):
        if self.current is DialogController.ABSENT:
            return
        Dialog.dispose_resources(self.current)
        self.view.render_worker.cancel(RenderSlot.POPUP)

    def cycle(self, reverse=FORWARD):
        controls = self._controls(self.current)
        if controls:
            app = get_app()
            current = app.layout.current_control
            index = (
                controls.index(current)
                if current in controls
                else DialogController.NO_FOCUS
            )
            focus_control(
                controls[
                    (
                        index
                        + (
                            DialogController.REVERSE_STEP
                            if reverse
                            else DialogController.FORWARD_STEP
                        )
                    )
                    % len(controls)
                ]
            )

    def help(self):
        from ..widget.output.reader import ReadOnlyDialog

        view = self.view
        popup = ReadOnlyDialog(
            view.t(MessageKey.HELP_TITLE),
            view.t(MessageKey.HELP),
            view._rows,
            view._columns,
            self.close,
            view.t,
            markdown=DialogController.MARKDOWN_HELP,
            theme=lambda: view.theme,
            render_worker=view.render_worker,
        )
        self.show(popup, popup.receiver)
