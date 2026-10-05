"""Localized PTK layout and input behavior shared by preview and live mode."""

from pathlib import Path

from prompt_toolkit.application.current import get_app
from prompt_toolkit.completion import ThreadedCompleter
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.layout import (ConditionalContainer, DynamicContainer, Float,
                                   FloatContainer, HSplit, VSplit, Window)
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.containers import to_container
from prompt_toolkit.layout.layout import walk
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.widgets import Dialog, Frame, Label

from .chat.completion import HubCompleter
from .chat.conversation import ChatMessage, ConversationView
from ..locales import Language
from ..config.theme import HubTheme
from .widgets import Button, RadioList, TextArea
from ..asset import icon
from ..config.general import GeneralSettings
from .sidebar import SidebarItem, SidebarList
from .layout import TwoPanelPage


class HubView:
    def __init__(self, sessions, theme=None, language="ko", language_packs=None) -> None:
        self.t = Language(language, language_packs)
        self.general = GeneralSettings()
        self.theme = theme or HubTheme()
        self.style = self.theme.style()
        self.sessions = list(sessions)
        self.on_open = self.on_submit = self.on_select = self.on_new = None
        self.on_interrupt = self.on_steer = None
        self.on_choose_engine = None
        self.on_history = self.on_activity = None
        self.on_turns = None
        self.on_delete = self.on_rename = self.on_component = None
        self.component_commands = {}
        self._execution_options = {}
        self.visible = self.show_details = False
        self.show_preview = False
        self.selected = 0
        self._session_focus = False
        self.engine = "loop"
        self.engines = ("loop",)
        self.file_root = Path.cwd()
        self.project_title = "demo-project"
        self.model = "sample/model"
        self.notice = self.t("preview_notice")
        self.activity = ""
        self._previous_focus = None
        self._input_timeouts = None
        self._drafts = {}
        self._positions = {}
        self.on_save_view = None
        self._dialog = None
        self._dialog_focus = None
        self.settings_open = False
        self.no_sessions = False
        from .progress import TaskProgress
        self.progress = TaskProgress(self)
        from .settings.screen import SettingsScreen
        self.settings = SettingsScreen(self)
        from .chat.search import SessionSearch
        from .chat.notifications import SessionToasts
        self.search = SessionSearch(self)
        self.toasts = SessionToasts(self)
        completer = HubCompleter(lambda: self.engines, lambda: self.file_root, self.t,
                                 components=lambda: self.component_commands)
        self.composer = TextArea(
            multiline=True, height=Dimension(min=1, max=8), dont_extend_height=True,
            wrap_lines=True, prompt=f" {icon.PROMPT} ", style="class:hub.composer",
            completer=ThreadedCompleter(completer), complete_while_typing=True,
        )
        self.transcript = ConversationView(self.sessions[0].messages, self.theme, language=self.t)
        self.output_renderers = self.transcript.control.renderers
        self.image_renderer = self.transcript.control.images
        from .chat.welcome import welcome_window
        self.welcome = welcome_window(self)
        self.transcript.window.height = Dimension(min=5, weight=1)
        self.draft_preview = ConversationView((), self.theme, language=self.t)
        self.composer.buffer.on_text_changed += self._update_preview
        self._session_control = SidebarList(
            lambda: [] if self.no_sessions else [SidebarItem(self._draft_key(i), session.title, session.status) for i, session in enumerate(self.sessions)],
            lambda: self._draft_key(self.selected), self._select_sidebar_session, key_bindings=self._session_keys())
        self._sidebar = ConditionalContainer(HSplit([
            self._line(lambda: self._focus_heading(self._session_control, self.t("sessions")), "hub.muted", height=2),
            Window(self._session_control, style="class:hub.sidebar"),
            self._line(lambda: self.t("session_hint"), "hub.muted", height=2),
        ], width=self.sidebar_width, style="class:hub.sidebar"), Condition(lambda: self._columns() >= 88 or self._session_focus))
        self._details = ConditionalContainer(VSplit([
            Window(width=1, char="│", style="class:hub.divider"),
            Window(FormattedTextControl(lambda: self.sessions[self.selected].detail),
                   width=25, style="class:hub.detail", wrap_lines=True),
        ]), Condition(lambda: self.show_details and self._columns() >= 116))
        self._preview_container = ConditionalContainer(Frame(
            self.draft_preview,
            title=lambda: self._focus_heading(self.draft_preview.control, self.t("preview")),
            style="class:hub.preview-frame", height=lambda: 3 if self._rows() < 32 else 5),
            Condition(lambda: self.show_preview and bool(self.composer.text) and self._rows() >= 24))
        self._composer_frame = Frame(HSplit([
            self.composer,
            ConditionalContainer(self._line(lambda: " " + self.t("input_hint"), "hub.muted"),
                                 Condition(lambda: self._rows() >= 18)),
        ]), title=lambda: self._focus_heading(self.composer.control, self.t("model_line", model=self.model)),
            style="class:hub.input-frame")
        conversation = HSplit([
            self._line(self._conversation_header, "hub.title"),
            DynamicContainer(lambda: self.welcome if self.no_sessions else self.transcript),
            ConditionalContainer(self._line(lambda: " " + self.activity, "hub.muted", height=2),
                                 Condition(lambda: bool(self.activity) and self._rows() >= 18)),
            self._preview_container,
            self._composer_frame,
        ])
        self.chat_page = TwoPanelPage(
            header=self._line(self._header, "hub.header"), footer=self._line(self._footer, "hub.footer"),
            sidebar=self._sidebar, main=conversation, progress=self.progress, notice=lambda: self.notice,
            sidebar_visible=self._sidebar.filter, extra=(self._details,),
            sidebar_controls=lambda: (self._session_control,),
            focus_sidebar=self._focus_sessions, focus_main=self._focus_composer)
        panel = self.chat_page.container
        # PTK defers cursor-positioned floats with an absolute drawing priority.
        # An enclosing host Float can inherit a later priority than our popups.
        # Paint the Hub body at this container's own base, then draw its overlays.
        root = FloatContainer(DynamicContainer(lambda: self.settings.container if self.settings_open else panel),
                              style="class:hub", z_index=0, floats=[
            Float(CompletionsMenu(max_height=8, extra_filter=Condition(
                lambda: self._dialog is None and get_app().layout.current_control == self.composer.control)),
                xcursor=True, ycursor=True, attach_to_window=self.composer.window, z_index=200),
            Float(ConditionalContainer(DynamicContainer(lambda: self._dialog or Window()),
                                       Condition(lambda: self._dialog is not None and not self.search.active)), z_index=300),
            Float(ConditionalContainer(DynamicContainer(lambda: self.search.dialog or Window()),
                                       Condition(lambda: self.search.active)), bottom=1, right=1, z_index=300),
            Float(ConditionalContainer(self.toasts.container,
                    Condition(lambda: self.toasts.active() and self._dialog is None)),
                  bottom=1, right=1, width=self.toasts.width, height=self.toasts.height, z_index=250),
        ])
        # Register chat bindings at the application level too: PTK's cached
        # parent chain can still describe settings immediately after switching.
        self.navigation_keys = merge_key_bindings([self._navigation_keys(), self._panel_keys()])
        modal = HSplit([root], modal=True, key_bindings=self.navigation_keys)
        self.container = ConditionalContainer(modal, Condition(lambda: self.visible))

    @staticmethod
    def _columns():
        return get_app().output.get_size().columns

    @staticmethod
    def _rows():
        return get_app().output.get_size().rows

    def sidebar_width(self):
        return min(self.theme.sidebar_width, max(18, self._columns() // 2))

    @staticmethod
    def _line(text, style, height=1):
        return Window(FormattedTextControl(text), height=height, style="class:" + style)

    def _header(self):
        return [("class:hub.header.project", "  " + self.project_title)]

    def _footer(self):
        return " " + self.t("footer_short" if self._columns() < 116 else "footer")

    def _conversation_header(self):
        session = self.sessions[self.selected]
        return self._focus_heading(self.transcript.control, session.title + "  ·  " + session.status)

    def _focus_heading(self, control, text):
        focused = self.visible and get_app().layout.current_control == control
        return [("class:hub.focused" if focused else "", (icon.SELECTED + " " if focused else "  ") + text)]

    def _navigation_keys(self):
        keys = KeyBindings()
        # Keep shell return available while DynamicContainer parents are rebuilt.
        @keys.add("c-q", filter=Condition(lambda: self.visible), eager=True)
        def return_to_shell(event):
            if self._dialog and not self.settings_open:
                self.close_dialog()
                return
            self.settings.finish_edit()
            self.hide(event.app)
        available = Condition(lambda: self.visible and self._dialog is None)
        dialog = Condition(lambda: self.visible and self._dialog is not None)

        @keys.add("c-f", filter=Condition(lambda: self.visible and not self.settings_open
                  and (self._dialog is None or self.search.active)), eager=True)
        def search(event):
            self.search.open()

        @keys.add("enter", filter=Condition(lambda: self.visible and self.search.active
                  and get_app().layout.current_control == self.search.input.control), eager=True)
        def search_next(event):
            self.search.move()

        @keys.add("escape", "p", filter=Condition(lambda: self.visible and self.search.active), eager=True)
        def search_previous(event):
            self.search.move(-1)

        @keys.add("tab", filter=dialog, eager=True)
        @keys.add("s-tab", filter=dialog, eager=True)
        def dialog_focus(event):
            controls = [container.content for container in walk(to_container(self._dialog), skip_hidden=True)
                        if isinstance(container, Window) and container.content.is_focusable()]
            if controls:
                current = event.app.layout.current_control
                index = controls.index(current) if current in controls else -1
                direction = -1 if event.key_sequence[0].key == "s-tab" else 1
                event.app.layout.focus(controls[(index + direction) % len(controls)])

        @keys.add("escape", filter=dialog)
        def dialog_close(event):
            self.close_dialog()

        @keys.add("c-s", filter=available, eager=True)
        def settings(event):
            if self.settings_open and not self.settings.layout.sidebar_focused:
                self.settings.save_and_close()
            else:
                self.toggle_settings()

        @keys.add("escape", "enter", filter=available & Condition(lambda: self.settings_open))
        def settings_newline(event):
            if self.settings.editing:
                event.app.current_buffer.insert_text("\n")

        settings = available & Condition(lambda: self.settings_open)
        chat = available & Condition(lambda: not self.settings_open)

        @keys.add("c-g", filter=chat, eager=True)
        def history(event):
            if self.on_history:
                self.on_history()

        @keys.add("c-l", filter=chat, eager=True)
        def activity(event):
            if self.on_activity:
                self.on_activity()

        @keys.add("tab", filter=settings, eager=True)
        @keys.add("s-tab", filter=settings, eager=True)
        def settings_focus(event):
            self.settings.focus(event.key_sequence[0].key == "s-tab")

        for key in ("up", "down", "left", "right"):
            @keys.add(key, filter=settings & Condition(lambda: not self.settings.editing), eager=True)
            def settings_move(event):
                self.settings.move(event.key_sequence[-1].key)

        for key in ("c", "d", "e", "r"):
            @keys.add(key, filter=settings & Condition(lambda: self.settings.project_focused), eager=True)
            def settings_project(event):
                self.settings.project_action(event.key_sequence[-1].key)

        @keys.add("escape", filter=available)
        def focus(event):
            self.active_page.focus_sidebar()

        @keys.add("tab", filter=chat, eager=True)
        @keys.add("s-tab", filter=chat, eager=True)
        def completion(event):
            if event.app.layout.current_control != self.composer.control:
                return
            if event.key_sequence[0].key == "s-tab":
                return
            buffer = self.composer.buffer
            if buffer.complete_state:
                completion = buffer.complete_state.current_completion
                if completion is None and buffer.complete_state.completions:
                    completion = buffer.complete_state.completions[0]
                if completion is not None:
                    buffer.apply_completion(completion)
            else:
                buffer.start_completion(select_first=False)

        sidebar = available & Condition(lambda: self.active_page.sidebar_focused)
        @keys.add("tab", filter=sidebar, eager=True)
        @keys.add(" ", filter=sidebar, eager=True)
        @keys.add("enter", filter=sidebar, eager=True)
        def enter_main(event):
            self.active_page.focus_main()

        for key in ("up", "down", "left", "right", "pageup", "pagedown", "home", "end"):
            @keys.add("escape", key, filter=settings, eager=True)
            def ignore_settings_scroll(event):
                pass

            @keys.add("escape", key, filter=chat, eager=True)
            def scroll(event):
                self.transcript.control.scroll(event.key_sequence[-1].key)
                event.app.invalidate()

        return keys

    @property
    def active_page(self):
        return self.settings.layout if self.settings_open else self.chat_page

    def _focus_sessions(self):
        self.composer.buffer.cancel_completion()
        self._session_focus = True
        get_app().layout.focus(self._session_control)

    def _focus_composer(self):
        self._session_focus = False
        get_app().layout.focus(self.composer.control)

    def _update_preview(self, _=None):
        self.draft_preview.control.messages = (ChatMessage("draft", self.composer.text),)
        get_app().invalidate()

    def _select_sidebar_session(self, key):
        self._session_focus = True
        self.select(next(i for i in range(len(self.sessions)) if self._draft_key(i) == key))

    def _session_keys(self):
        keys = KeyBindings()
        @keys.add("left")
        @keys.add("right")
        def resize(event):
            self.settings.resize(-1 if event.key_sequence[0].key == "left" else 1)
            event.app.invalidate()
        @keys.add("up")
        @keys.add("down")
        def select(event):
            self.select((self.selected + (-1 if event.key_sequence[0].key == "up" else 1)) % len(self.sessions))
        @keys.add("c")
        def create(event):
            self.session_dialog("new")
        @keys.add("r")
        def clone(event):
            self.session_dialog("clone")
        @keys.add("d")
        def delete(event):
            if self.no_sessions:
                return
            session = self.sessions[self.selected]
            self.open_dialog(self.t("session_delete"), Label(self.t("session_delete_confirm", title=session.title)),
                lambda: self.on_delete and self.on_delete(session.id))
        @keys.add("e")
        def rename(event):
            self.rename_dialog()
        return keys

    def _panel_keys(self):
        keys = KeyBindings()
        available = Condition(lambda: self.visible and self._dialog is None and not self.settings_open)
        composing = available & Condition(lambda: get_app().layout.current_control == self.composer.control)
        @keys.add("c-c", is_global=True, filter=Condition(lambda: self.visible))
        def close(event):
            if self._dialog:
                self.close_dialog()
            elif self.settings_open:
                self.toggle_settings()
            else:
                self.hide(event.app)
        @keys.add("f1", filter=available)
        def help_(event):
            self.help_dialog()
        @keys.add("f2", filter=available)
        def next_session(event):
            self.select((self.selected + 1) % len(self.sessions))
        @keys.add("c-e", filter=available, eager=True)
        def engine(event):
            self.engine_dialog()
        @keys.add("f4", filter=available)
        def create(event):
            self.session_dialog()
        @keys.add("f5", filter=available)
        def preview(event):
            self.show_preview = not self.show_preview
        @keys.add("f6", filter=available)
        def details(event):
            self.toggle_details()
        @keys.add("c-x", filter=available, eager=True)
        def interrupt(event):
            if self.on_interrupt:
                self.on_interrupt(self.sessions[self.selected].id)
        @keys.add("enter", filter=composing)
        def submit(event):
            buffer = self.composer.buffer
            if buffer.complete_state and buffer.complete_state.current_completion:
                buffer.apply_completion(buffer.complete_state.current_completion)
                return
            buffer.cancel_completion()
            if self._command(self.composer.text):
                return
            if self.on_submit:
                self.on_submit(self.sessions[self.selected].id, self.composer.text)
            else:
                self.notice = self.t("preview_submit")
        @keys.add("c-space", filter=composing, eager=True)
        def newline(event):
            self.composer.buffer.cancel_completion()
            self.composer.buffer.insert_text("\n")
        return keys

    def _command(self, text):
        if not text.startswith("/"):
            return False
        command, _, argument = text.partition(" ")
        if command[1:] in self.component_commands:
            if self.on_component:
                self.on_component(command[1:], argument.strip())
            return True
        actions = {"/help": self.help_dialog, "/engine": lambda: self.engine_dialog(argument.strip()),
                   "/new": lambda: self.session_dialog("new", argument.strip()),
                   "/clone": lambda: self.session_dialog("clone", argument.strip()),
                   "/preview": lambda: setattr(self, "show_preview", not self.show_preview),
                   "/details": self.toggle_details,
                   "/stop": lambda: self.on_interrupt and self.on_interrupt(self.sessions[self.selected].id)}
        if command not in actions:
            self.notice = self.t("unknown_command", command=command)
            return True
        self.composer.text = ""
        actions[command]()
        return True

    def toggle_details(self):
        self.show_details = not self.show_details
        self.notice = self.t("details_narrow" if self.show_details and self._columns() < 116
                             else "details_open" if self.show_details else "details_closed")

    def open_dialog(self, title, body, accept=None, focus=None, *, validate=None):
        self._dialog_focus = get_app().layout.current_control
        def confirm():
            if validate and not validate():
                return
            self.close_dialog()
            if accept:
                accept()
        buttons = [Button(self.t("confirm"), handler=confirm)]
        if accept:
            buttons.append(Button(self.t("cancel"), handler=self.close_dialog))
        self._dialog = Dialog(title=title, body=body, buttons=buttons,
                              width=Dimension(preferred=64, max=80), with_background=False,
                              modal=False)  # The surrounding Hub already isolates shell keys.
        get_app().layout.focus(focus or buttons[0])
        get_app().invalidate()

    def close_dialog(self):
        focus = self._dialog_focus
        self._dialog = None
        if focus in list(get_app().layout.find_all_controls()):
            get_app().layout.focus(focus)
        else:
            get_app().layout.focus(self.composer)
        get_app().invalidate()

    def engine_dialog(self, name=""):
        if self.on_choose_engine:
            self.on_choose_engine(name)
            return
        if name in self.engines:
            self.engine = name
            return
        choices = RadioList([(name, name) for name in self.engines], default=self.engine, select_on_focus=True)
        self.open_dialog(self.t("choose_engine"), choices,
                         lambda: setattr(self, "engine", choices.current_value), choices)

    @property
    def engine_options(self) -> dict:
        return self.execution_options_for(self.engine)

    def execution_options_for(self, engine: str) -> dict:
        key = (getattr(self, "project_id", ""), self._draft_key(self.selected), engine)
        return dict(self._execution_options.get(key, {}))

    @property
    def execution_label(self) -> str:
        workflow = self.engine_options.get("workflow")
        return f"{self.engine} · {workflow}" if workflow else self.engine

    def set_execution(self, engine: str, options: dict) -> None:
        self.engine = engine
        key = (getattr(self, "project_id", ""), self._draft_key(self.selected), engine)
        self._execution_options[key] = dict(options)

    def session_dialog(self, mode="new", title="", *, turns=None, through_message_id=None):
        if self.no_sessions and mode == "clone":
            self.notice = self.t("session_required")
            return
        source_id = self.sessions[self.selected].id
        ready = self.no_sessions or turns is not None or self.on_turns is None
        choices = RadioList([("new", self.t("create")), *([] if self.no_sessions else [("clone", self.t("clone"))])], default=mode, select_on_focus=True)
        name = TextArea(text=title, height=1, multiline=False)
        from .chat.history_ui import turn_label
        boundary = RadioList([(None, self.t("clone_latest")),
                              *[(row["id"], turn_label(row, index, self.t)) for index, row in enumerate(turns or [])]],
                             default=through_message_id, select_on_focus=True)
        boundary.window.height = Dimension(min=1, max=6)
        body = HSplit([choices, ConditionalContainer(HSplit([Label(self.t("clone_boundary")), boundary]),
                      Condition(lambda: choices.current_value == "clone")),
                      Label(self.t("name")), name, Label(self.t("auto_name_hint"))], padding=1)
        def create():
            if self.on_new:
                args = (choices.current_value, name.text.strip(), source_id)
                if choices.current_value == "clone" and boundary.current_value is not None:
                    self.on_new(*args, boundary.current_value)
                else:
                    self.on_new(*args)
        def accept_name(buffer):
            if validate():
                self.close_dialog()
                create()
            return True
        def validate():
            if choices.current_value == "clone" and not ready:
                self.notice = self.t("settings_loading")
                return False
            return True
        name.buffer.accept_handler = accept_name
        self.open_dialog(self.t("create_dialog"), body, create, name, validate=validate)
        pending = self._dialog
        if not ready:
            def loaded(rows):
                nonlocal ready
                if rows is not None and self._dialog is pending:
                    ready = True
                    boundary.values = [(None, self.t("clone_latest")),
                        *[(row["id"], turn_label(row, i, self.t)) for i, row in enumerate(rows)]]
                    boundary.current_value = through_message_id if any(row["id"] == through_message_id for row in rows) else None
                    boundary._selected_index = next(i for i, (value, _) in enumerate(boundary.values)
                                                    if value == boundary.current_value)
                    get_app().invalidate()
            self.on_turns(source_id, loaded)

    def rename_dialog(self):
        if self.no_sessions:
            return
        session = self.sessions[self.selected]
        name = TextArea(text=session.title, height=1, multiline=False)
        def rename():
            if self.on_rename:
                self.on_rename(session.id, name.text.strip())
        def validate():
            if name.text.strip():
                return True
            self.notice = self.t("session_name_required")
            return False
        self.open_dialog(self.t("session_rename"), name, rename, name, validate=validate)
        def accept(_):
            if validate():
                self.close_dialog()
                rename()
            return True
        name.buffer.accept_handler = accept

    def help_dialog(self):
        body = TextArea(text=self.t("help") + "\n\n" + self.t("help_history"), read_only=True, scrollbar=True,
                        height=Dimension(preferred=18, max=max(3, self._rows() - 6)))
        self.open_dialog(self.t("help_title"), body)

    def _draft_key(self, index):
        if self.no_sessions:
            return "empty:" + getattr(self, "project_id", "")
        return self.sessions[index].id or f"sample:{index}"

    def toggle_settings(self, event=None):
        app = event.app if event is not None else get_app()
        if not self.visible:
            self.show(app)
        if self._dialog is not None:
            return
        self.composer.buffer.cancel_completion()
        self.settings_open = not self.settings_open
        if self.settings_open:
            self.remember_position()
            app.layout.focus(self.settings.left_control())
            self.settings.open()
        else:
            self.settings.finish_edit()
            self._session_focus = False
            app.layout.focus(self.composer)
        app.invalidate()

    def remember_position(self):
        if self.no_sessions:
            return
        self._positions[self._draft_key(self.selected)] = self.transcript.control.reading_position()

    def select(self, index):
        if self.search.active:
            self.close_dialog()
        self.remember_position()
        self._drafts[self._draft_key(self.selected)] = self.composer.text
        self.selected = index
        self.composer.text = self._drafts.get(self._draft_key(index), "")
        self.transcript.set_messages(self.sessions[index].messages, self._positions.get(self._draft_key(index)))
        if self.on_save_view:
            self.on_save_view()
        if self.on_select:
            self.on_select(self.sessions[index].id)
        get_app().invalidate()

    def set_theme(self, theme):
        self.theme, self.style = theme, theme.style()
        self.transcript.control.theme = self.draft_preview.control.theme = theme
        get_app().invalidate()

    def show(self, app):
        if self.visible:
            return
        self._previous_focus = app.layout.current_control
        # ESC is also the prefix for Alt and VT100 sequences. Keep a small
        # discrimination window instead of eagerly consuming the Alt prefix.
        self._input_timeouts = (app.ttimeoutlen, app.timeoutlen)
        app.ttimeoutlen = app.timeoutlen = 0.02
        self.visible = True
        self._session_focus = False
        if self.on_open:
            self.on_open(app)
        app.layout.focus(self.settings.left_control() if self.settings_open else self.composer)
        app.invalidate()

    def hide(self, app):
        if not self.visible:
            return
        self.remember_position()
        if self.on_save_view:
            self.on_save_view()
        self._dialog = None
        self.transcript.control.restore_position(self._positions.get(self._draft_key(self.selected)))
        if self._previous_focus in list(app.layout.find_all_controls()):
            app.layout.focus(self._previous_focus)
        self.visible = False
        if self._input_timeouts is not None:
            app.ttimeoutlen, app.timeoutlen = self._input_timeouts
            self._input_timeouts = None
        app.invalidate()

    def toggle(self, event):
        self.hide(event.app) if self.visible else self.show(event.app)
