"""Hub actions and their footer hints share these context predicates."""

from hub.ui.input.keys import KeyName, ShortcutAction, ShortcutLabel
from hub.widget.dialog import DialogContext, Dialog
from hub.widget.forms.path_input import PathInput


from hub.locales.keys import MessageKey

from prompt_toolkit.application.current import get_app
from prompt_toolkit.key_binding import DynamicKeyBindings, merge_key_bindings
from prompt_toolkit.layout.controls import BufferControl
from .context import InputContext
from .registry import ShortcutRegistry, legend
from ...widget.text_editing import TextEditing


class HubBindings:
    GLOBAL = True
    DEFERRED = False
    HIDDEN = False
    ABSENT = None
    NO_ACTION = None
    PREVIOUS = -1
    NEXT = 1
    SELECT_COMPLETION = False

    def __init__(self, view):
        self.view = view
        self.context = c = InputContext(view)
        self.registry = r = ShortcutRegistry()
        self.common_registry = common = ShortcutRegistry()
        self.editing = TextEditing(lambda: view.visible)
        self.keys = merge_key_bindings(
            [
                r.bindings,
                common.bindings,
                self.editing.bindings,
                DynamicKeyBindings(lambda: view.output.shortcuts.bindings),
            ]
        )
        common.add(
            [KeyName.CONTROL_Q],
            ShortcutLabel.CONTROL_Q,
            ShortcutAction.MINIMIZE,
            self.minimize,
            when=lambda: view.visible,
            global_=self.GLOBAL,
        )
        common.add(
            [KeyName.CONTROL_W],
            ShortcutLabel.CONTROL_W,
            ShortcutAction.RESTART,
            lambda e: view.restart(),
            when=lambda: view.visible,
            global_=self.GLOBAL,
        )
        common.add(
            [KeyName.CONTROL_P],
            ShortcutLabel.CONTROL_P,
            ShortcutAction.PROFILE,
            lambda e: view.profile_bar.focus(),
            when=lambda: c.available,
            global_=self.GLOBAL,
        )
        common.add(
            [KeyName.CONTROL_C],
            ShortcutLabel.CONTROL_C,
            ShortcutAction.COPY,
            self.copy,
            when=lambda: view.visible and bool(self.selection()),
            global_=self.GLOBAL,
        )
        common.add(
            [KeyName.CONTROL_V],
            ShortcutLabel.HIDDEN,
            ShortcutAction.PASTE,
            self.paste,
            when=lambda: view.visible
            and isinstance(c.control, BufferControl)
            and not get_app().current_buffer.read_only(),
            global_=self.GLOBAL,
        )
        r.add(
            [KeyName.ESCAPE],
            ShortcutLabel.ESCAPE,
            ShortcutAction.SIDEBAR,
            self.escape,
            when=lambda: c.available
            and not c.sidebar
            and not c.profile
            and not c.auxiliary
            and not (c.settings and view.settings.tabs.content_focused),
        )
        r.add(
            [KeyName.ESCAPE],
            ShortcutLabel.HIDDEN,
            ShortcutAction.NONE,
            lambda e: self.NO_ACTION,
            when=lambda: c.sidebar,
        )
        r.add(
            [KeyName.ESCAPE],
            ShortcutLabel.ESCAPE,
            lambda: (Dialog.navigation_for(view._dialog).escape_action if Dialog.navigation_for(view._dialog)
                     else ShortcutAction.CLOSE),
            lambda e: (Dialog.navigation_for(view._dialog).escape() if Dialog.navigation_for(view._dialog)
                       else view.close_dialog()),
            when=lambda: c.dialog,
        )
        r.add(
            [KeyName.TAB, KeyName.SHIFT_TAB],
            ShortcutLabel.TAB_SHIFT_TAB,
            ShortcutAction.MOVE,
            self.dialog_tab,
            when=lambda: c.dialog,
        )
        r.add(
            [KeyName.CONTROL_S],
            ShortcutLabel.CONTROL_S,
            ShortcutAction.AUXILIARY,
            lambda e: (
                view.settings.auxiliary if view.settings_open else view.auxiliary
            ).focus(),
            when=lambda: c.composing or (c.settings and view.settings.actions_focused),
        )
        r.add(
            [KeyName.CONTROL_F],
            ShortcutLabel.CONTROL_F,
            ShortcutAction.FIND,
            lambda e: view.settings.search(),
            when=lambda: c.settings,
        )
        # Retired Home shortcuts must not fall through to PTK's transpose/redraw
        # bindings and unexpectedly modify a draft.
        r.add(
            [
                KeyName.CONTROL_F,
                KeyName.CONTROL_G,
                KeyName.CONTROL_L,
                KeyName.CONTROL_T,
            ],
            ShortcutLabel.HIDDEN,
            ShortcutAction.NONE,
            lambda e: self.NO_ACTION,
            when=lambda: c.chat,
        )
        r.add(
            [KeyName.CONTROL_D],
            ShortcutLabel.CONTROL_D,
            ShortcutAction.STOP_RESUME,
            lambda e: view.on_interrupt(view.sessions[view.selected].id),
            when=lambda: c.composing and view.on_interrupt is not self.ABSENT,
        )
        r.add(
            [KeyName.TAB, KeyName.SHIFT_TAB],
            ShortcutLabel.TAB_SHIFT_TAB,
            ShortcutAction.REGIONS,
            lambda e: view.settings.focus(e.key_sequence[0].key == KeyName.SHIFT_TAB),
            when=lambda: c.settings and not c.sidebar,
        )
        r.add(
            [KeyName.LEFT],
            ShortcutLabel.LEFT,
            ShortcutAction.SIDEBAR,
            lambda e: view.settings.switch_panel(),
            when=lambda: c.settings
            and not c.sidebar
            and not view.settings.editing
            and not view.settings.actions_focused
            and not view.settings.tabs.tabs_focused,
        )
        r.add(
            [KeyName.UP, KeyName.DOWN],
            ShortcutLabel.VERTICAL,
            ShortcutAction.ITEMS,
            lambda e: view.settings.move(e.key_sequence[-1].key),
            when=lambda: c.settings
            and not c.sidebar
            and not view.settings.editing
            and not view.settings.actions_focused
            and not view.settings.tabs.tabs_focused,
        )
        r.add(
            [KeyName.RIGHT],
            ShortcutLabel.HIDDEN,
            ShortcutAction.NONE,
            lambda e: self.NO_ACTION,
            when=lambda: c.settings
            and not c.sidebar
            and not view.settings.editing
            and not view.settings.actions_focused
            and not view.settings.tabs.tabs_focused,
        )
        r.add(
            [KeyName.UP, KeyName.DOWN],
            ShortcutLabel.VERTICAL,
            ShortcutAction.SELECT,
            self.select,
            when=lambda: c.sidebar,
        )
        r.add(
            [KeyName.CONTROL_LEFT, KeyName.CONTROL_RIGHT],
            ShortcutLabel.CONTROL_HORIZONTAL,
            ShortcutAction.WIDTH,
            self.resize,
            when=lambda: c.sidebar,
        )
        for key, action in (
            (KeyName.C, ShortcutAction.CREATE),
            (KeyName.CLEAR, ShortcutAction.DELETE),
            (KeyName.EDIT, ShortcutAction.RENAME),
            (KeyName.R, ShortcutAction.CLONE),
        ):

            def enabled(key=key):
                return (
                    c.sidebar
                    and c.chat
                    and (
                        c.control is view.projects.control
                        or key == KeyName.C
                        or not view.no_sessions
                    )
                )

            r.add([key], key, action, self.session_action, when=enabled)
        r.add(
            [KeyName.TAB, KeyName.SHIFT_TAB],
            ShortcutLabel.TAB_SHIFT_TAB,
            ShortcutAction.CATEGORIES,
            lambda e: view.active_page.cycle_category(
                e.key_sequence[-1].key == KeyName.SHIFT_TAB
            ),
            when=lambda: c.sidebar,
        )
        r.add(
            [KeyName.SPACE, KeyName.ENTER, KeyName.RIGHT],
            ShortcutLabel.SPACE_ENTER_RIGHT,
            ShortcutAction.MAIN,
            lambda e: view.active_page.focus_main(),
            when=lambda: c.sidebar,
        )
        r.add(
            [KeyName.TAB],
            ShortcutLabel.HIDDEN,
            ShortcutAction.CONFIRM,
            self.complete,
            when=lambda: c.composing,
        )
        r.add(
            [KeyName.SHIFT_TAB],
            ShortcutLabel.HIDDEN,
            ShortcutAction.NONE,
            lambda e: self.NO_ACTION,
            when=lambda: c.composing,
        )
        r.add(
            [KeyName.RIGHT],
            ShortcutLabel.HIDDEN,
            ShortcutAction.NONE,
            self.suggest_or_move,
            when=lambda: c.composing,
        )
        r.add(
            [KeyName.UP, KeyName.DOWN],
            ShortcutLabel.HIDDEN,
            ShortcutAction.NONE,
            lambda e: view.input_history.move(
                self.PREVIOUS if e.key_sequence[-1].key == KeyName.UP else self.NEXT
            ),
            when=lambda: c.composing and view.input_history.available,
        )
        r.scroll(self.scroll, when=lambda: c.chat)
        r.scroll(self.scroll_settings, when=lambda: c.settings and not c.sidebar)
        r.scroll(
            lambda e: self.NO_ACTION,
            when=lambda: c.settings and c.sidebar,
            visible=self.HIDDEN,
        )
        # The composer's own hint row already shows send/completion/newline.
        r.add(
            [KeyName.ENTER],
            ShortcutLabel.HIDDEN,
            ShortcutAction.SEND,
            self.submit,
            when=lambda: c.composing,
            eager=self.DEFERRED,
        )
        r.add(
            [KeyName.CONTROL_SPACE],
            ShortcutLabel.HIDDEN,
            ShortcutAction.NEWLINE,
            self.newline,
            when=lambda: c.composing,
        )
        r.add(
            [KeyName.CONTROL_O],
            ShortcutLabel.CONTROL_O,
            ShortcutAction.APPROVAL_DETAILS,
            lambda e: view.question.review(),
            when=lambda: c.composing and bool(view.question.item),
        )
        r.add(
            [KeyName.CONTROL_C],
            ShortcutLabel.CONTROL_C,
            ShortcutAction.CLOSE,
            self.close,
            when=lambda: view.visible
            and not view.settings_open
            and not c.composing
            and not c.auxiliary
            and not self.selection(),
            global_=self.GLOBAL,
            eager=self.DEFERRED,
        )
        r.add(
            [KeyName.CONTROL_C],
            ShortcutLabel.HIDDEN,
            ShortcutAction.NONE,
            lambda e: self.NO_ACTION,
            when=lambda: view.visible
            and (c.composing or c.auxiliary or view.settings_open)
            and not self.selection(),
            global_=self.GLOBAL,
        )

    def selection(self):
        from ...widget.clipboard import selected_text

        return selected_text(get_app(), self.view)

    def copy(self, event):
        from ...widget.clipboard import copy_text

        text = self.selection()

        async def copied():
            await copy_text(text)
            event.app.invalidate()

        event.app.create_background_task(copied())

    def paste(self, event):
        from ...widget.clipboard import SystemClipboard

        buffer = event.app.current_buffer
        document = buffer.document

        async def paste():
            register = event.app.clipboard
            data = (
                await register.read()
                if isinstance(register, SystemClipboard)
                else register.get_data()
            )
            if (
                data is not self.ABSENT
                and self.view.visible
                and event.app.current_buffer is buffer
                and not buffer.read_only()
                and buffer.document == document
            ):
                buffer.save_to_undo_stack()
                if buffer.selection_state:
                    buffer.cut_selection()
                buffer.paste_clipboard_data(data)

        event.app.create_background_task(paste())

    def dialog_tab(self, event):
        navigation = Dialog.navigation_for(self.view._dialog)
        if navigation is not None:
            navigation.cycle(event.key_sequence[-1].key == KeyName.SHIFT_TAB)
            return
        control = event.app.layout.current_control
        if event.key_sequence[-1].key == KeyName.TAB and PathInput.accepts(control):
            buffer = control.buffer
            if (
                buffer.text.strip()
                and buffer.complete_state
                and buffer.complete_state.completions
            ):
                buffer.apply_completion(
                    buffer.complete_state.current_completion
                    or buffer.complete_state.completions[0]
                )
                return
        self.view.dialogs.cycle(event.key_sequence[-1].key == KeyName.SHIFT_TAB)

    def hints(self):
        local = ShortcutRegistry.for_control(self.context.control)
        # Readers replace page actions, but keep application-wide shortcuts.
        only_local = bool(
            self.view._dialog
            and self.view._dialog.shortcut_context == DialogContext.READER
        )
        actions = self.common_registry.active()
        if not only_local:
            actions += self.registry.active()
            actions += self.view.output.shortcuts.active()
        if local:
            actions += local.active()
        if self.context.settings:
            actions += self.view.settings.tabs.shortcuts.active()
        return legend(actions, self.view.t)

    def escape(self, event):
        self.view.active_page.focus_sidebar()

    def minimize(self, event):
        self.view.minimize()

    def select(self, event):
        self.context.control.move(
            self.PREVIOUS if event.key_sequence[-1].key == KeyName.UP else self.NEXT
        )

    def resize(self, event):
        self.view.settings.resize(
            self.PREVIOUS
            if event.key_sequence[-1].key == KeyName.CONTROL_LEFT
            else self.NEXT
        )
        event.app.invalidate()

    def session_action(self, event):
        key = event.key_sequence[-1].key
        if self.context.control is self.view.projects.control:
            self.view.projects.perform(key)
        else:
            self.view.session_actions.perform(key)

    def complete(self, event):
        if self.view.question.item:
            return
        buffer = self.view.composer.buffer
        if buffer.complete_state:
            completion = buffer.complete_state.current_completion
            if completion is self.ABSENT and buffer.complete_state.completions:
                completion = buffer.complete_state.completions[0]
            if completion is not self.ABSENT:
                buffer.apply_completion(completion)
        else:
            buffer.start_completion(select_first=self.SELECT_COMPLETION)

    def suggest_or_move(self, event):
        if not self.view.followup.accept():
            buffer = self.view.composer.buffer
            buffer.suggestion = self.ABSENT
            buffer.cursor_right(count=event.arg)

    def scroll(self, event):
        self.view.output.scroll(event.key_sequence[-1].key)
        event.app.invalidate()

    def scroll_settings(self, event):
        self.view.settings.main.scroll(event.key_sequence[-1].key)
        event.app.invalidate()

    def submit(self, event):
        view, buffer = self.view, self.view.composer.buffer
        if view.question.item:
            view.question.answer()
            return
        if view.question.busy:
            # Keep a newly typed draft while its previous decision is being
            # admitted; a second Enter must not submit another request.
            return
        if buffer.complete_state and buffer.complete_state.current_completion:
            buffer.apply_completion(buffer.complete_state.current_completion)
            return
        buffer.cancel_completion()
        if view.commands.execute(view.composer.text):
            return
        if view.on_submit:
            view.on_submit(view.sessions[view.selected].id, view.composer.text)
        else:
            view.notice = view.t(MessageKey.PREVIEW_SUBMIT)

    def newline(self, event):
        self.view.composer.buffer.cancel_completion()
        self.view.composer.insert_text(TextEditing.NEWLINE)

    def close(self, event):
        view = self.view
        if view._dialog:
            view.close_dialog()
        elif view.output.modal_container is not self.ABSENT:
            view.output.deactivate()
            view._focus_composer()
        else:
            view.hide(event.app)
