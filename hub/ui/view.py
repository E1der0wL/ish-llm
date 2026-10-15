"""Localized PTK layout and input behavior shared by preview and live mode."""

from pathlib import Path

from prompt_toolkit.application.current import get_app
from prompt_toolkit.completion import ThreadedCompleter
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import (ConditionalContainer, DynamicContainer, Float,
                                   FloatContainer, HSplit, Window)
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu
from ..widget.shortcuts import ShortcutBar
from ..widget.header import HeaderBar

from .chat.completion import HubCompleter
from ..widget.output.engine import EngineOutput
from ..model import SessionView
from ..locales import Language
from ..config.theme import HubTheme
from ..widget.controls import TextArea
from ..widget.input_frame import InputFrame
from ..asset import icon
from ..config.general import GeneralSettings
from ..widget.sidebar import SidebarHeading, SidebarItem, SidebarList
from .layout import TwoPanelPage


class HubView:
    def __init__(self, sessions=(), theme=None, language="ko", language_packs=None, *, output_factory=None) -> None:
        self.t = Language(language, language_packs, icons=lambda: self.icons)
        self.general = GeneralSettings()
        from ..config.profile import UserProfile
        self.user_profile = UserProfile()
        self.theme = theme or HubTheme()
        self.style = self.theme.style()
        self.sessions = list(sessions) or [SessionView(self.t("welcome_title"))]
        self.on_open = self.on_submit = self.on_select = self.on_new = None
        self.on_interrupt = None
        self.on_answer = self.on_review = None
        self.on_history = self.on_activity = None
        self.on_turns = None
        self.on_delete = self.on_rename = self.on_component = None
        self.component_commands = {}
        self._execution_options = {}
        self.visible = False
        from ..widget.mouse import MouseController
        self.mouse = MouseController(self)
        self.selected = 0
        self._session_focus = False
        self.engine = "loop"
        self.engines = ("loop",)
        self.file_root = Path.cwd()
        self.project_title = "Hub"
        self.model = ""
        self.notice = self.t("preview_notice")
        self._previous_focus = None
        self._input_timeouts = None
        self._drafts = {}
        self._positions = {}
        self.on_save_view = None
        self.on_output_position_changed = None
        self.animate_output = False
        from .dialogs import DialogController
        self.dialogs = DialogController(self)
        from .chat.session_actions import SessionActions
        self.session_actions = SessionActions(self)
        from .input.commands import HubCommands
        self.commands = HubCommands(self)
        self.settings_open = False
        self.no_sessions = False
        from ..widget.feedback.progress import TaskProgress
        self.progress = TaskProgress(self)
        from ..widget.profile import ProfileBar
        self.profile_bar = ProfileBar(self)
        from .settings.screen import SettingsScreen
        self.settings = SettingsScreen(self)
        from ..widget.feedback.notifications import SessionToasts
        self.toasts = SessionToasts(self)
        completer = HubCompleter(lambda: self.engines, lambda: self.file_root, self.t,
                                 components=lambda: self.component_commands)
        from ..widget.forms.followup import FollowupSuggestion
        self.followup = FollowupSuggestion(self)
        self.composer = TextArea(
            auto_suggest=self.followup,
            # Two input rows, one hint row and two borders make five rows.
            multiline=True, height=Dimension(min=2, max=8), dont_extend_height=True,
            wrap_lines=True, prompt=f" {icon.PROMPT} ", style="class:hub.composer",
            completer=ThreadedCompleter(completer), complete_while_typing=Condition(
                lambda: self.question.item is None and not self.input_history.browsing),
        )
        from ..widget.forms.question import QuestionComposer
        self.question = QuestionComposer(self)
        from ..widget.feedback.preparing import PreparingResponse
        self.preparing = PreparingResponse(self)
        from ..widget.feedback.interrupt_feedback import InterruptFeedback
        self.interrupting = InterruptFeedback(self)
        from ..widget.feedback.history_deletion import HistoryDeletion
        self.deleting = HistoryDeletion(self)
        from .input.history import ComposerHistory
        self.input_history = ComposerHistory(self)
        self.followup.bind(self.composer.buffer)
        from .output.worker import RenderWorker
        self.render_worker = RenderWorker()
        from ..widget.output.loop import LoopOutput
        self.output: EngineOutput = (output_factory or LoopOutput)(self)
        self._session_control = SidebarList(
            lambda: [] if self.no_sessions else [SidebarItem(self._draft_key(i), session.title, session.status) for i, session in enumerate(self.sessions)],
            lambda: self._draft_key(self.selected), self._select_sidebar_session)
        from .chat.projects import ChatProjects
        from ..widget.sidebar_panel import SidebarPanel
        self.projects = ChatProjects(self)
        self._sidebar = ConditionalContainer(SidebarPanel([
            HSplit([SidebarHeading(lambda: self.icons.project + " " + self.t("settings_projects")),
                    Window(self.projects.control, style="class:hub.sidebar")]),
            HSplit([SidebarHeading(lambda: self.t("sessions")),
                    Window(self._session_control, style="class:hub.sidebar")]),
        ], self.profile_bar, width=self.sidebar_width, project_fraction=0.27,
            controls=(self.projects.control, self._session_control)),
            Condition(lambda: self._columns() >= 88 or self._session_focus))
        self._composer_frame = InputFrame(HSplit([
            self.question.container,
            self.composer,
        ]), title=lambda: self._focus_heading(self.composer.control, self.t("model_line", model=self.model)),
            hint=lambda: self.t("input_hint") + (
                " · " + self.t("followup_hint") if self.followup.get_suggestion(
                    self.composer.buffer, self.composer.buffer.document) else ""),
            style="class:hub.input-frame")
        from ..widget.auxiliary import AuxiliaryBar
        from ..widget.forms.file_attachment import attach_file
        self.auxiliary = AuxiliaryBar([
            ("history", lambda: self.on_history() if self.on_history else self._not_connected()),
            ("tags", lambda: self.output.invoke("tags")), ("find", lambda: self.output.invoke("find")),
            ("attach", lambda: attach_file(self)),
            ("logs", lambda: self.on_activity() if self.on_activity else self._not_connected()),
            ("settings", lambda: self.settings.open_engine("loop")),
        ], lambda: self.icons, self._focus_composer)
        conversation = HSplit([
            DynamicContainer(lambda: self.output),
            self.auxiliary, self._composer_frame,
        ])
        self.header = HeaderBar(lambda: self.t("profile_home"), icons=lambda: self.icons)
        self.chat_page = TwoPanelPage(
            header=self.header, footer=self.shortcut_bar(),
            sidebar=self._sidebar, main=conversation, progress=self.progress, notice=lambda: self.notice,
            sidebar_visible=self._sidebar.filter,
            sidebar_controls=lambda: (self._session_control, self.projects.control, *self.profile_bar.controls),
            categories=(self.projects.control, self._session_control),
            focus_sidebar=self._focus_sessions, focus_main=self._focus_composer,
            sidebar_width=self.sidebar_width, resize_sidebar=self.settings.set_sidebar_width)
        panel = self.chat_page.container
        # PTK defers cursor-positioned floats with an absolute drawing priority.
        # An enclosing host Float can inherit a later priority than our popups.
        # Paint the Hub body at this container's own base, then draw its overlays.
        root = FloatContainer(DynamicContainer(lambda: self.settings.container if self.settings_open else panel),
                              style="class:hub", z_index=0, floats=[
            Float(ConditionalContainer(DynamicContainer(lambda: self._dialog or Window()),
                                       Condition(lambda: self._dialog is not None and self.output.floating_dialog is None)), z_index=300),
            Float(ConditionalContainer(DynamicContainer(lambda: self.output.floating_dialog or Window()),
                                       Condition(lambda: self.output.floating_dialog is not None)), bottom=1, right=1, z_index=300),
            Float(ConditionalContainer(self.toasts.container,
                    Condition(lambda: self.toasts.active() and self._dialog is None)),
                  bottom=1, right=1, width=self.toasts.width, height=self.toasts.height, z_index=250),
            # Keep one cursor overlay after every dialog. Nested cursor floats
            # can run before the dialog's Windows have published their position.
            Float(CompletionsMenu(max_height=8, scroll_offset=1),
                  xcursor=True, ycursor=True, z_index=500),
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
        minimum = self.profile_bar.minimum_width
        return max(minimum, min(self.theme.sidebar_width, max(minimum, self._columns() // 2)))

    @property
    def icons(self):
        return icon.for_style(self.theme.icon_style)

    def shortcut_bar(self):
        return ShortcutBar(lambda: self.bindings.hints(), self._columns)

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

    def focus_sidebar(self):
        if self.settings_open:
            self.settings.switch_panel()
        else:
            self._focus_sessions()
        get_app().invalidate()

    def open_page(self, settings):
        if self.settings.busy or self.projects.busy:
            return
        if self.settings_open != settings:
            self.toggle_settings()
        elif settings:
            self.settings.layout.focus_main()
        else:
            self._focus_composer()

    def _focus_composer(self):
        self._session_focus = False
        get_app().layout.focus(self.composer.control)

        get_app().invalidate()

    def _not_connected(self):
        self.notice = self.t("not_connected")

    def _select_sidebar_session(self, key):
        self._session_focus = True
        self.select(next(i for i in range(len(self.sessions)) if self._draft_key(i) == key))

    def toggle_details(self):
        self.output.invoke("details")

    def set_output(self, output: EngineOutput) -> None:
        """Replace only the engine-owned output, retaining Home controls and drafts."""
        if output is self.output:
            return
        app = get_app()
        focus = app.layout.current_control
        self.remember_position()
        self.output.close()
        self.output = output
        output.set_theme(self.theme)
        output.set_file_root(self.file_root)
        output.set_messages(self.sessions[self.selected].messages, self._positions.get(self._draft_key(self.selected)))
        app.layout.update_parents_relations()
        if self.visible:
            if focus in list(app.layout.find_all_controls()):
                app.layout.focus(focus)
            elif not self.settings_open:
                self._focus_composer()
        app.invalidate()

    def close(self) -> None:
        self.output.close()
        self.render_worker.close()
        self.progress.close()

    @property
    def _dialog(self):
        return self.dialogs.current

    def open_dialog(self, *args, **kwargs):
        return self.dialogs.open(*args, **kwargs)

    def close_dialog(self):
        self.dialogs.close()

    def session_dialog(self, *args, **kwargs):
        self.session_actions.create(*args, **kwargs)

    def help_dialog(self):
        self.dialogs.help()

    def choose_engine(self, name=""):
        if name in self.engines:
            self.set_execution(name, self.execution_options_for(name))
        else:
            self.notice = self.t("engine_command_usage", engines=", ".join(self.engines))

    @property
    def engine_options(self) -> dict:
        return self.execution_options_for(self.engine)

    def execution_options_for(self, engine: str) -> dict:
        key = (getattr(self, "project_id", ""), self._draft_key(self.selected), engine)
        return dict(self._execution_options.get(key, {}))

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
            self.output.deactivate()
            app.layout.focus(self.settings.left_control())
            self.settings.open()
        else:
            self.settings.finish_edit()
            self._session_focus = False
            app.layout.focus(self.composer)
        # Global shortcuts follow the focused window's parent containers.
        # Refresh them before the next key, even if rendering is throttled.
        app.layout.update_parents_relations()
        app.invalidate()

    def minimize(self):
        self.settings.finish_edit()
        self.hide(get_app())

    def remember_position(self):
        if self.no_sessions:
            return
        self._positions[self._draft_key(self.selected)] = self.output.reading_position()

    def select(self, index):
        self.question.leave()
        self.output.deactivate()
        self.remember_position()
        self._drafts[self._draft_key(self.selected)] = self.composer.text
        self.selected = index
        self.composer.buffer.suggestion = None
        self.composer.text = self._drafts.get(self._draft_key(index), "")
        key = (getattr(self, "project_id", ""), self.sessions[index].id)
        self.output.set_messages(self.preparing.project(key, self.sessions[index].messages),
                                     self._positions.get(self._draft_key(index)))
        if self.on_save_view:
            self.on_save_view()
        if self.on_select:
            self.on_select(self.sessions[index].id)
        get_app().invalidate()

    def set_theme(self, theme):
        self.theme, self.style = theme, theme.style()
        self.output.set_theme(theme)
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
        from ..widget.clipboard import SystemClipboard
        app.clipboard = SystemClipboard(app, app.clipboard)
        self.mouse.attach(app)
        self._session_focus = False
        if self.on_open:
            self.on_open(app)
        app.layout.focus(self.settings.left_control() if self.settings_open else self.composer)
        app.invalidate()

    def hide(self, app):
        if not self.visible:
            return
        self.remember_position()
        self.output.deactivate()
        if self.on_save_view:
            self.on_save_view()
        self.dialogs.current = None
        self.dialogs.previous_focus = None
        self.output.restore_position(self._positions.get(self._draft_key(self.selected)))
        if self._previous_focus in list(app.layout.find_all_controls()):
            app.layout.focus(self._previous_focus)
        self.visible = False
        self.mouse.detach()
        from ..widget.clipboard import SystemClipboard
        if isinstance(app.clipboard, SystemClipboard):
            app.clipboard = app.clipboard.original
        if self._input_timeouts is not None:
            app.ttimeoutlen, app.timeoutlen = self._input_timeouts
            self._input_timeouts = None
        app.invalidate()

    def toggle(self, event):
        self.hide(event.app) if self.visible else self.show(event.app)
