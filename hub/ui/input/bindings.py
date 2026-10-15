"""Hub actions and their footer hints share these context predicates."""

from prompt_toolkit.application.current import get_app
from prompt_toolkit.key_binding import merge_key_bindings
from prompt_toolkit.layout.controls import BufferControl
from .context import InputContext
from .registry import ShortcutRegistry, legend


class HubBindings:
    def __init__(self, view):
        self.view = view
        self.context = c = InputContext(view)
        self.registry = r = ShortcutRegistry()
        self.common_registry = common = ShortcutRegistry()
        self.keys = merge_key_bindings([r.bindings, common.bindings])
        common.add(["c-q"], "Ctrl+Q", "minimize", self.minimize,
                   when=lambda: view.visible, global_=True)
        common.add(["c-p"], "Ctrl+P", "profile", lambda e: view.profile_bar.focus(),
                   when=lambda: c.available, global_=True)
        common.add(["c-c"], "Ctrl+C", "copy", self.copy,
                   when=lambda: view.visible and bool(self.selection()), global_=True)
        common.add(["c-v", "c-y"], "Ctrl+V/Ctrl+Y", "paste", self.paste,
                   when=lambda: view.visible and isinstance(c.control, BufferControl)
                                and not get_app().current_buffer.read_only(), global_=True)
        r.add(["escape"], "ESC", "sidebar", self.escape, when=lambda: c.available and not c.sidebar and not c.profile)
        r.add(["escape"], "", "", lambda e: None, when=lambda: c.sidebar)
        r.add(["escape"], "ESC", "close", lambda e: view.close_dialog(), when=lambda: c.dialog)
        r.add(["tab", "s-tab"], "Tab/Shift+Tab", "move",
              self.dialog_tab, when=lambda: c.dialog)
        r.add(["c-s"], "Ctrl+S", "settings", self.settings, when=lambda: c.chat)
        r.add(["c-s"], "", "", lambda e: None,
              when=lambda: view.visible and view.settings_open, global_=True)
        r.add(["c-f"], "Ctrl+F", "find", lambda e: view.search.open(),
              when=lambda: view.visible and not view.settings_open and not view.tags.visible
                           and (view._dialog is None or view.search.active))
        r.add(["c-f"], "Ctrl+F", "find", lambda e: view.settings.search(), when=lambda: c.settings)
        r.add(["enter"], "Enter", "next_match", lambda e: view.search.move(),
              when=lambda: view.visible and view.search.active and c.control is view.search.input.control)
        r.add([("escape", "p")], "Alt+P", "previous_match", lambda e: view.search.move(-1),
              when=lambda: view.visible and view.search.active)
        for key, label, callback in (("c-g", "history", lambda: view.on_history), ("c-l", "logs", lambda: view.on_activity),
                                     ("c-x", "stop", lambda: view.on_interrupt)):
            def invoke(e, callback=callback, key=key):
                action = callback()
                if action:
                    action(view.sessions[view.selected].id) if key == "c-x" else action()
            r.add([key], "Ctrl+" + key[-1].upper(), label, invoke, when=lambda callback=callback: c.chat and callback() is not None)
        r.add(["tab", "s-tab"], "Tab/Shift+Tab", "regions",
              lambda e: view.settings.focus(e.key_sequence[0].key == "s-tab"), when=lambda: c.settings and not c.sidebar)
        r.add(["left"], "←", "sidebar", lambda e: view.settings.switch_panel(),
              when=lambda: c.settings and not c.sidebar and not view.settings.editing and not view.settings.actions_focused)
        r.add(["up", "down"], "↑↓", "items",
              lambda e: view.settings.move(e.key_sequence[-1].key),
              when=lambda: c.settings and not c.sidebar and not view.settings.editing and not view.settings.actions_focused)
        r.add(["right"], "", "", lambda e: None,
              when=lambda: c.settings and not c.sidebar and not view.settings.editing and not view.settings.actions_focused)
        r.add(["left", "right"], "←→", "select",
              lambda e: view.settings.move(e.key_sequence[-1].key),
              when=lambda: c.settings and view.settings.actions_focused)
        r.add(["up", "down"], "", "", lambda e: None,
              when=lambda: c.settings and view.settings.actions_focused)
        r.add(["up", "down"], "↑↓", "select", self.select, when=lambda: c.sidebar)
        r.add(["c-left", "c-right"], "Ctrl+←→", "width", self.resize, when=lambda: c.sidebar)
        for key, action in (("c", "create"), ("d", "delete"), ("e", "rename"), ("r", "clone")):
            def enabled(key=key):
                return c.sidebar and c.chat and (
                    c.control is view.projects.control or key == "c" or not view.no_sessions)
            r.add([key], key, action, self.session_action, when=enabled)
        r.add(["tab", " ", "enter", "right"], "Tab/Space/Enter/→", "main", lambda e: view.active_page.focus_main(), when=lambda: c.sidebar)
        r.add(["tab"], "", "confirm", self.complete, when=lambda: c.composing)
        r.add(["s-tab"], "", "", lambda e: None, when=lambda: c.composing)
        r.add(["right"], "", "", self.suggest_or_move, when=lambda: c.composing)
        r.add(["up", "down"], "", "", lambda e: view.input_history.move(
            -1 if e.key_sequence[-1].key == "up" else 1),
            when=lambda: c.composing and view.input_history.available)
        r.scroll(self.scroll, when=lambda: c.chat)
        r.scroll(self.scroll_settings, when=lambda: c.settings and not c.sidebar)
        r.scroll(lambda e: None, when=lambda: c.settings and c.sidebar, visible=False)
        r.add(["c-t"], "Ctrl+T", "tags", lambda e: view.tags.open(), when=lambda: c.chat)
        # The composer's own hint row already shows send/completion/newline.
        r.add(["enter"], "", "send", self.submit, when=lambda: c.composing, eager=False)
        r.add(["c-space"], "", "newline", self.newline, when=lambda: c.composing)
        r.add(["c-o"], "Ctrl+O", "approval_details", lambda e: view.question.review(),
              when=lambda: c.composing and bool(view.question.item))
        r.add(["c-c"], "Ctrl+C", "close", self.close,
              when=lambda: view.visible and not view.settings_open and not c.composing and not self.selection(), global_=True, eager=False)
        r.add(["c-c"], "", "", lambda e: None,
              when=lambda: view.visible and (c.composing or view.settings_open) and not self.selection(), global_=True)

    def selection(self):
        from ...widget.clipboard import selected_text
        return selected_text(get_app(), self.view)

    def copy(self, event):
        from ...widget.clipboard import copy_text
        text = self.selection()
        async def copied():
            self.view.notice = self.view.t(await copy_text(text))
            event.app.invalidate()
        event.app.create_background_task(copied())

    def paste(self, event):
        from ...widget.clipboard import SystemClipboard
        buffer = event.app.current_buffer
        async def paste():
            register = event.app.clipboard
            data = await register.read() if isinstance(register, SystemClipboard) else register.get_data()
            if self.view.visible and event.app.current_buffer is buffer and not buffer.read_only():
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
        if local:
            actions += local.active()
        return legend(actions, self.view.t)

    def escape(self, event):
        self.view.active_page.focus_sidebar()

    def minimize(self, event):
        self.view.settings.finish_edit()
        self.view.hide(event.app)

    def settings(self, event):
        view = self.view
        if self.context.composing:
            view.settings.open_engine()
        else:
            view.toggle_settings()

    def select(self, event):
        if self.context.settings:
            self.view.settings.move(event.key_sequence[-1].key)
        else:
            self.view.projects.move(-1 if event.key_sequence[-1].key == "up" else 1)

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
        self.view.transcript.control.scroll(event.key_sequence[-1].key)
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
        self.view.composer.buffer.insert_text("\n")

    def close(self, event):
        view = self.view
        if view._dialog:
            view.close_dialog()
        elif view.tags.visible:
            view.tags.close()
        else:
            view.hide(event.app)
