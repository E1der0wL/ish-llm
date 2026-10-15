"""Hub actions and their footer hints share these context predicates."""

from prompt_toolkit.application.current import get_app
from prompt_toolkit.key_binding import DynamicKeyBindings, merge_key_bindings
from prompt_toolkit.layout.controls import BufferControl
from .context import InputContext
from .registry import ShortcutRegistry, legend
from ...widget.text_editing import TextEditing


class HubBindings:
    def __init__(self, view):
        self.view = view
        self.context = c = InputContext(view)
        self.registry = r = ShortcutRegistry()
        self.common_registry = common = ShortcutRegistry()
        self.editing = TextEditing(lambda: view.visible)
        self.keys = merge_key_bindings([r.bindings, common.bindings, self.editing.bindings,
                                       DynamicKeyBindings(lambda: view.output.shortcuts.bindings)])
        common.add(["c-q"], "Ctrl+Q", "minimize", self.minimize,
                   when=lambda: view.visible, global_=True)
        common.add(["c-p"], "Ctrl+P", "profile", lambda e: view.profile_bar.focus(),
                   when=lambda: c.available, global_=True)
        common.add(["c-c"], "Ctrl+C", "copy", self.copy,
                   when=lambda: view.visible and bool(self.selection()), global_=True)
        common.add(["c-v"], "", "paste", self.paste,
                   when=lambda: view.visible and isinstance(c.control, BufferControl)
                                and not get_app().current_buffer.read_only(), global_=True)
        r.add(["escape"], "ESC", "sidebar", self.escape,
              when=lambda: c.available and not c.sidebar and not c.profile and not c.auxiliary)
        r.add(["escape"], "", "", lambda e: None, when=lambda: c.sidebar)
        r.add(["escape"], "ESC", "close", lambda e: view.close_dialog(), when=lambda: c.dialog)
        r.add(["tab", "s-tab"], "Tab/Shift+Tab", "move",
              self.dialog_tab, when=lambda: c.dialog)
        r.add(["c-s"], "Ctrl+S", "auxiliary", lambda e:
              (view.settings.auxiliary if view.settings_open else view.auxiliary).focus(),
              when=lambda: c.composing or (c.settings and view.settings.actions_focused))
        r.add(["c-f"], "Ctrl+F", "find", lambda e: view.settings.search(), when=lambda: c.settings)
        # Retired Home shortcuts must not fall through to PTK's transpose/redraw
        # bindings and unexpectedly modify a draft.
        r.add(["c-f", "c-g", "c-l", "c-t"], "", "", lambda e: None, when=lambda: c.chat)
        r.add(["c-w"], "Ctrl+W", "stop", lambda e: view.on_interrupt(view.sessions[view.selected].id),
              when=lambda: c.chat and view.on_interrupt is not None)
        r.add(["tab", "s-tab"], "Tab/Shift+Tab", "regions",
              lambda e: view.settings.focus(e.key_sequence[0].key == "s-tab"), when=lambda: c.settings and not c.sidebar)
        r.add(["left"], "←", "sidebar", lambda e: view.settings.switch_panel(),
              when=lambda: c.settings and not c.sidebar and not view.settings.editing and not view.settings.actions_focused)
        r.add(["up", "down"], "↑↓", "items",
              lambda e: view.settings.move(e.key_sequence[-1].key),
              when=lambda: c.settings and not c.sidebar and not view.settings.editing and not view.settings.actions_focused)
        r.add(["right"], "", "", lambda e: None,
              when=lambda: c.settings and not c.sidebar and not view.settings.editing and not view.settings.actions_focused)
        r.add(["up", "down"], "↑↓", "select", self.select, when=lambda: c.sidebar)
        r.add(["c-left", "c-right"], "Ctrl+←→", "width", self.resize, when=lambda: c.sidebar)
        for key, action in (("c", "create"), ("d", "delete"), ("e", "rename"), ("r", "clone")):
            def enabled(key=key):
                return c.sidebar and c.chat and (
                    c.control is view.projects.control or key == "c" or not view.no_sessions)
            r.add([key], key, action, self.session_action, when=enabled)
        r.add(["tab", "s-tab"], "Tab/Shift+Tab", "categories", lambda e: view.active_page.cycle_category(
            e.key_sequence[-1].key == "s-tab"), when=lambda: c.sidebar)
        r.add([" ", "enter", "right"], "Space/Enter/→", "main", lambda e: view.active_page.focus_main(), when=lambda: c.sidebar)
        r.add(["tab"], "", "confirm", self.complete, when=lambda: c.composing)
        r.add(["s-tab"], "", "", lambda e: None, when=lambda: c.composing)
        r.add(["right"], "", "", self.suggest_or_move, when=lambda: c.composing)
        r.add(["up", "down"], "", "", lambda e: view.input_history.move(
            -1 if e.key_sequence[-1].key == "up" else 1),
            when=lambda: c.composing and view.input_history.available)
        r.scroll(self.scroll, when=lambda: c.chat)
        r.scroll(self.scroll_settings, when=lambda: c.settings and not c.sidebar)
        r.scroll(lambda e: None, when=lambda: c.settings and c.sidebar, visible=False)
        # The composer's own hint row already shows send/completion/newline.
        r.add(["enter"], "", "send", self.submit, when=lambda: c.composing, eager=False)
        r.add(["c-space"], "", "newline", self.newline, when=lambda: c.composing)
        r.add(["c-o"], "Ctrl+O", "approval_details", lambda e: view.question.review(),
              when=lambda: c.composing and bool(view.question.item))
        r.add(["c-c"], "Ctrl+C", "close", self.close,
              when=lambda: view.visible and not view.settings_open and not c.composing and not c.auxiliary and not self.selection(), global_=True, eager=False)
        r.add(["c-c"], "", "", lambda e: None,
              when=lambda: view.visible and (c.composing or c.auxiliary or view.settings_open) and not self.selection(), global_=True)

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
            data = await register.read() if isinstance(register, SystemClipboard) else register.get_data()
            if (self.view.visible and event.app.current_buffer is buffer and not buffer.read_only()
                    and buffer.document == document):
                buffer.save_to_undo_stack()
                if buffer.selection_state:
                    buffer.cut_selection()
                buffer.paste_clipboard_data(data)
        event.app.create_background_task(paste())

    def dialog_tab(self, event):
        control = event.app.layout.current_control
        if event.key_sequence[-1].key == "tab" and getattr(control, "hub_complete_path", False):
            buffer = control.buffer
            if buffer.text.strip() and buffer.complete_state and buffer.complete_state.completions:
                buffer.apply_completion(buffer.complete_state.current_completion or buffer.complete_state.completions[0])
                return
        self.view.dialogs.cycle(event.key_sequence[-1].key == "s-tab")

    def hints(self):
        local = getattr(self.context.control, "hub_shortcuts", None)
        # Readers replace page actions, but keep application-wide shortcuts.
        only_local = bool(self.view._dialog and self.view._dialog.shortcut_context == "reader")
        actions = self.common_registry.active()
        if not only_local:
            actions += self.registry.active()
            actions += self.view.output.shortcuts.active()
        if local:
            actions += local.active()
        return legend(actions, self.view.t)

    def escape(self, event):
        self.view.active_page.focus_sidebar()

    def minimize(self, event):
        self.view.minimize()

    def select(self, event):
        self.context.control.move(-1 if event.key_sequence[-1].key == "up" else 1)

    def resize(self, event):
        self.view.settings.resize(-1 if event.key_sequence[-1].key == "c-left" else 1)
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
            if completion is None and buffer.complete_state.completions:
                completion = buffer.complete_state.completions[0]
            if completion is not None:
                buffer.apply_completion(completion)
        else:
            buffer.start_completion(select_first=False)

    def suggest_or_move(self, event):
        if not self.view.followup.accept():
            buffer = self.view.composer.buffer
            buffer.suggestion = None
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
            view.notice = view.t("preview_submit")

    def newline(self, event):
        self.view.composer.buffer.cancel_completion()
        self.view.composer.insert_text("\n")

    def close(self, event):
        view = self.view
        if view._dialog:
            view.close_dialog()
        elif view.output.modal_container is not None:
            view.output.deactivate()
            view._focus_composer()
        else:
            view.hide(event.app)
