"""Hub actions and their footer hints share these context predicates."""

from prompt_toolkit.application.current import get_app
from .context import InputContext
from .registry import ShortcutRegistry, legend


class HubBindings:
    def __init__(self, view):
        self.view = view
        self.context = c = InputContext(view)
        self.registry = r = ShortcutRegistry()
        self.keys = r.bindings

        r.add(["escape"], "ESC", lambda: "minimize" if c.sidebar else "sidebar", self.escape, when=lambda: c.available)
        r.add(["escape"], "ESC", "close", lambda e: view.close_dialog(), when=lambda: c.dialog)
        r.add(["tab", "s-tab"], "Tab/Shift+Tab", "move",
              lambda e: view.dialogs.cycle(e.key_sequence[0].key == "s-tab"), when=lambda: c.dialog)
        r.add(["c-s"], "Ctrl+S", lambda: "save_chat" if c.settings else "settings",
              self.settings, when=lambda: c.available)
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
        r.add(["up", "down", "left", "right"], "↑↓←→", "items",
              lambda e: view.settings.move(e.key_sequence[-1].key), when=lambda: c.settings and not c.sidebar and not view.settings.editing)
        r.add(["up", "down"], "↑↓", "select", self.select, when=lambda: c.sidebar)
        r.add(["c-left", "c-right"], "Ctrl+←→", "width", self.resize, when=lambda: c.sidebar)
        for key, action in (("c", "create"), ("d", "delete"), ("e", "rename"), ("r", "clone")):
            def enabled(key=key):
                return c.sidebar and (not c.settings or view.settings.project_focused) and (
                    key == "c" or (view.settings._focused_key() != "new" if c.settings else not view.no_sessions))
            r.add([key], key, action, self.session_action, when=enabled)
        r.add(["tab", " ", "enter", "right"], "Tab/Space/Enter/→", "main", lambda e: view.active_page.focus_main(), when=lambda: c.sidebar)
        r.add(["tab"], "", "confirm", self.complete, when=lambda: c.composing)
        r.add(["s-tab"], "", "", lambda e: None, when=lambda: c.composing)
        r.add(["up", "down"], "", "", lambda e: view.input_history.move(
            -1 if e.key_sequence[-1].key == "up" else 1),
            when=lambda: c.composing and view.input_history.available)
        r.scroll(self.scroll, when=lambda: c.chat)
        # Settings uses its item navigator. Never let an Alt prefix escape into
        # the chat page or trigger the standalone ESC panel action.
        r.scroll(lambda e: None, when=lambda: c.settings, visible=False)
        for key, label, callback in (
            ("c-e", "engine", view.engine_dialog),
            ("c-r", "preview", lambda: setattr(view, "show_preview", not view.show_preview)),
            ("c-t", "tags", lambda: view.tags.open())):
            r.add([key], "Ctrl+" + key[-1].upper(), label, lambda e, callback=callback: callback(), when=lambda: c.chat)
        # The composer's own hint row already shows send/completion/newline.
        r.add(["enter"], "", "send", self.submit, when=lambda: c.composing, eager=False)
        r.add(["c-space"], "", "newline", self.newline, when=lambda: c.composing)
        r.add(["c-c"], "Ctrl+C", "close", self.close, when=lambda: view.visible and not c.composing, global_=True, eager=False)
        r.add(["c-c"], "", "", lambda e: None, when=lambda: c.composing, global_=True)

    def hints(self):
        local = getattr(self.context.control, "hub_shortcuts", None)
        # A modal read-only receiver owns all keys, including ESC and Enter.
        only_local = bool(self.view._dialog and self.view._dialog.shortcut_context == "reader")
        actions = [] if only_local else self.registry.active()
        if local:
            actions += local.active()
        return legend(actions, self.view.t)

    def escape(self, event):
        if self.context.sidebar:
            self.view.settings.finish_edit()
            self.view.hide(event.app)
        else:
            self.view.active_page.focus_sidebar()

    def settings(self, event):
        view = self.view
        if self.context.settings:
            view.settings.save_and_close()
        elif self.context.composing:
            view.settings.open_engine()
        else:
            view.toggle_settings()

    def select(self, event):
        if self.context.settings:
            self.view.settings.move(event.key_sequence[-1].key)
        else:
            self.view.select((self.view.selected + (-1 if event.key_sequence[-1].key == "up" else 1)) % len(self.view.sessions))

    def resize(self, event):
        self.view.settings.resize(-1 if event.key_sequence[-1].key == "c-left" else 1)
        event.app.invalidate()

    def session_action(self, event):
        key = event.key_sequence[-1].key
        if self.context.settings:
            self.view.settings.project_action(key)
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

    def scroll(self, event):
        self.view.transcript.control.scroll(event.key_sequence[-1].key)
        event.app.invalidate()

    def submit(self, event):
        view, buffer = self.view, self.view.composer.buffer
        if view.question.item:
            view.question.answer()
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
        elif view.settings_open:
            view.settings.discard_and_close()
        elif view.tags.visible:
            view.tags.close()
        else:
            view.hide(event.app)
