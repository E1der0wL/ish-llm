"""Localized PTK layout and input behavior shared by preview and live mode."""

from pathlib import Path

from prompt_toolkit.application.current import get_app
from prompt_toolkit.completion import ThreadedCompleter
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import (ConditionalContainer, DynamicContainer, Float,
                                   FloatContainer, HSplit, VSplit, Window)
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.widgets import Frame
from ..widget.shortcuts import ShortcutBar
from ..widget.header import HeaderBar

from .chat.completion import HubCompleter
from ..widget.conversation import ChatMessage, ConversationView
from ..locales import Language
from ..config.theme import HubTheme
from ..widget.controls import TextArea
from ..asset import icon
from ..config.general import GeneralSettings
from ..widget.sidebar import SidebarItem, SidebarList
from .layout import TwoPanelPage


class HubView:
    def __init__(self, sessions, theme=None, language="ko", language_packs=None) -> None:
        self.t = Language(language, language_packs, icons=lambda: self.icons)
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
        from .dialogs import DialogController
        self.dialogs = DialogController(self)
        from .chat.session_actions import SessionActions
        self.session_actions = SessionActions(self)
        from .input.commands import HubCommands
        self.commands = HubCommands(self)
        self.settings_open = False
        self.no_sessions = False
        from ..widget.progress import TaskProgress
        self.progress = TaskProgress(self)
        from .settings.screen import SettingsScreen
        self.settings = SettingsScreen(self)
        from ..widget.search import SessionSearch
        from ..widget.notifications import SessionToasts
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
        from ..widget.welcome import welcome_window
        self.welcome = welcome_window(self)
        self.transcript.window.height = Dimension(min=5, weight=1)
        self.draft_preview = ConversationView((), self.theme, language=self.t)
        self.composer.buffer.on_text_changed += self._update_preview
        self._session_control = SidebarList(
            lambda: [] if self.no_sessions else [SidebarItem(self._draft_key(i), session.title, session.status) for i, session in enumerate(self.sessions)],
            lambda: self._draft_key(self.selected), self._select_sidebar_session)
        self._sidebar = ConditionalContainer(HSplit([
            self._line(lambda: self._focus_heading(self._session_control, self.t("sessions")), "hub.muted", height=2),
            Window(self._session_control, style="class:hub.sidebar"),
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
            header=HeaderBar(lambda: self.project_title), footer=self.shortcut_bar(),
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
        from .input.bindings import HubBindings
        self.bindings = HubBindings(self)
        self.navigation_keys = self.bindings.keys
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
        return HeaderBar(lambda: self.project_title).fragments()

    @property
    def icons(self):
        return icon.for_style(self.theme.icon_style)

    def shortcut_bar(self):
        return ShortcutBar(lambda: self.bindings.hints(), self._columns)

    def _footer(self):
        return " · ".join(key + " " + label for key, label in self.bindings.hints())

    def _conversation_header(self):
        session = self.sessions[self.selected]
        return self._focus_heading(self.transcript.control, session.title + "  ·  " + session.status)

    def _focus_heading(self, control, text):
        focused = self.visible and get_app().layout.current_control == control
        return [("class:hub.focused" if focused else "", (icon.SELECTED + " " if focused else "  ") + text)]

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

    def toggle_details(self):
        self.show_details = not self.show_details
        self.notice = self.t("details_narrow" if self.show_details and self._columns() < 116
                             else "details_open" if self.show_details else "details_closed")

    @property
    def _dialog(self):
        return self.dialogs.current

    def open_dialog(self, *args, **kwargs):
        return self.dialogs.open(*args, **kwargs)

    def close_dialog(self):
        self.dialogs.close()

    def session_dialog(self, *args, **kwargs):
        self.session_actions.create(*args, **kwargs)

    def rename_dialog(self):
        self.session_actions.rename()

    def help_dialog(self):
        self.dialogs.help()

    def engine_dialog(self, name=""):
        self.dialogs.engine(name)

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
        self.progress.close()
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
        self.dialogs.current = None
        self.dialogs.previous_focus = None
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
