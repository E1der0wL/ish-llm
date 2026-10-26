"""Localized PTK layout and input behavior shared by preview and live mode."""

from hub.backend.execution.engine_selection import HubEngineIdentity

from hub.locales.codes import LanguageCode

from hub.locales.keys import MessageKey

from pathlib import Path

from prompt_toolkit.application.current import get_app
from prompt_toolkit.completion import ThreadedCompleter
from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import (
    ConditionalContainer,
    DynamicContainer,
    Float,
    FloatContainer,
    HSplit,
    Window,
)
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu
from ..widget.shortcuts import ShortcutBar
from ..widget.header import HeaderBar

from .chat.completion import HubCompleter
from ..widget.output.engine import EngineOutput
from ..model import SessionView, HubSnapshot
from ..config.theme_style import ThemeSelector
from ..asset.icon import AuxiliaryIcon
from ..locales import Language
from ..config.theme import HubTheme
from ..widget.controls import TextArea
from ..widget.focus import focus_control
from ..widget.input_frame import InputFrame
from ..asset import icon
from ..config.general import GeneralSettings
from ..widget.sidebar import SidebarHeading, SidebarItem, SidebarList
from .layout import TwoPanelPage
from ..backend.project.agents import DEFAULT_AGENT


class HubView:
    ABSENT = None
    OUTPUT_INTERVAL_CALLBACK = "on_output_interval"
    SAMPLE_PREFIX = "sample:"
    SAMPLE_KEY = SAMPLE_PREFIX + "{index}"
    EMPTY_PREFIX = "empty:"
    PROMPT_FORMAT = " {prompt} "
    FOLLOW_TAIL = True
    HIDDEN = False
    SHOWN = True
    COMPOSER_FOCUS = False
    INITIAL_SELECTION = 0
    PREVIEW_TITLE = "Hub"
    NO_MODEL = ""
    NO_AGENT = ""
    NO_ENGINE = ""
    EMPTY_DRAFT = ""
    SETTINGS_CLOSED = False
    SESSIONS_PRESENT = False
    MULTILINE = True
    COMPOSER_MIN_ROWS = 2
    COMPOSER_MAX_ROWS = 8
    FIXED_COMPOSER_HEIGHT = True
    WRAP_COMPOSER = True
    HEADING_SEPARATOR = " "
    PROJECT_FRACTION = 0.27
    SIDEBAR_MIN_COLUMNS = 88
    HINT_SEPARATOR = " · "
    NO_HINT = ""
    NO_STYLE = ""
    BODY_LAYER = 0
    DIALOG_LAYER = 300
    TOAST_LAYER = 250
    COMPLETION_LAYER = 500
    OVERLAY_MARGIN = 1
    COMPLETION_ROWS = 8
    COMPLETION_SCROLL_OFFSET = 1
    CURSOR_ANCHORED = True
    MODAL = True
    SIDEBAR_WIDTH_DIVISOR = 2
    UNFOCUSED_PREFIX = "  "
    AGENT_SEPARATOR = ", "
    INPUT_SEQUENCE_TIMEOUT = 0.02

    SIDEBAR_FOCUS = True
    SIDEBAR_WIDTH_CALLBACK = "on_sidebar_width"
    NO_SIDEBAR_WIDTH_CALLBACK = ABSENT

    @staticmethod
    def output_interval_callback(view):
        return getattr(view, HubView.OUTPUT_INTERVAL_CALLBACK, HubView.ABSENT)

    @staticmethod
    def bind_output_interval(view, callback):
        setattr(view, HubView.OUTPUT_INTERVAL_CALLBACK, callback)

    @staticmethod
    def bind_default_agent(view, identifier):
        view._default_agent_id = identifier

    @staticmethod
    def saved_positions(view):
        return {
            key: value
            for key, value in view._positions.items()
            if not key.startswith(HubView.SAMPLE_PREFIX)
        }

    @staticmethod
    def clear_positions(view):
        view._positions.clear()

    @staticmethod
    def restore_positions(view, positions):
        view._positions.update(positions)

    @staticmethod
    def drop_session_state(view, identifier):
        view._drafts.pop(identifier, HubView.ABSENT)
        view._positions.pop(identifier, HubView.ABSENT)

    def composer_draft(self, default):
        return self._drafts.get(self._draft_key(self.selected), default)

    def store_composer_draft(self, text):
        self._drafts[self._draft_key(self.selected)] = text

    @staticmethod
    def request_sidebar_focus(view):
        view._session_focus = HubView.SIDEBAR_FOCUS

    @staticmethod
    def sidebar_width_callback(view):
        return getattr(
            view, HubView.SIDEBAR_WIDTH_CALLBACK, HubView.NO_SIDEBAR_WIDTH_CALLBACK
        )

    @staticmethod
    def bind_sidebar_width(view, callback):
        setattr(view, HubView.SIDEBAR_WIDTH_CALLBACK, callback)

    def __init__(
        self,
        sessions=(),
        theme=ABSENT,
        language=LanguageCode.DEFAULT,
        language_packs=ABSENT,
        *,
        output_factory=ABSENT
    ) -> None:
        self.t = Language(language, language_packs, icons=lambda: self.icons)
        self.general = GeneralSettings()
        from ..config.profile import UserProfile

        self.user_profile = UserProfile()
        self.theme = theme or HubTheme()
        self.style = self.theme.style()
        self.sessions = list(sessions) or [
            SessionView(self.t(MessageKey.WELCOME_TITLE))
        ]
        self.on_open = self.on_submit = self.on_select = self.on_new = HubView.ABSENT
        self.on_interrupt = HubView.ABSENT
        self.on_answer = self.on_review = HubView.ABSENT
        self.on_history = self.on_activity = HubView.ABSENT
        self.on_turns = HubView.ABSENT
        self.on_delete = self.on_rename = self.on_component = HubView.ABSENT
        self.component_commands = {}
        self.visible = HubView.HIDDEN
        from ..widget.mouse import MouseController

        self.mouse = MouseController(self)
        self.selected = HubView.INITIAL_SELECTION
        self._session_focus = HubView.COMPOSER_FOCUS
        self._agent_choices = {}
        self._default_agent_id = DEFAULT_AGENT
        self.agents = (DEFAULT_AGENT,)
        self._participant_sets = {}
        self._participant_groups = {}
        self._default_participants = (DEFAULT_AGENT,)
        self.agent_titles = {}
        self._recipients = {}
        self.on_invite_agent = HubView.ABSENT
        self.on_change_participants = HubView.ABSENT
        self.agent_engines = {DEFAULT_AGENT: HubEngineIdentity.LOOP}
        self.engines = (HubEngineIdentity.LOOP,)
        self.file_root = Path.cwd()
        self.project_title = HubView.PREVIEW_TITLE
        self.model = HubView.NO_MODEL
        self.notice = self.t(MessageKey.PREVIEW_NOTICE)
        from .operation_diagnostics import OperationDiagnostics

        self.operation_diagnostics = OperationDiagnostics()
        self._previous_focus = HubView.ABSENT
        self._input_timeouts = HubView.ABSENT
        self._drafts = {}
        self._positions = {}
        self.on_save_view = HubView.ABSENT
        self.on_output_position_changed = HubView.ABSENT
        self.clipboard_environment = HubView.ABSENT
        from ..widget.x11_clipboard import X11Clipboard

        self.native_clipboard = X11Clipboard()
        self.on_restart = HubView.ABSENT
        from .dialogs import DialogController

        self.dialogs = DialogController(self)
        from .chat.session_actions import SessionActions

        self.session_actions = SessionActions(self)
        from .input.commands import HubCommands

        self.commands = HubCommands(self)
        self.settings_open = HubView.SETTINGS_CLOSED
        self.no_sessions = HubView.SESSIONS_PRESENT
        from ..widget.feedback.progress import TaskProgress

        self.progress = TaskProgress(self)
        from ..widget.profile import ProfileBar

        self.profile_bar = ProfileBar(self)
        from .settings.screen import SettingsScreen

        self.settings = SettingsScreen(self)
        from ..widget.feedback.notifications import SessionToasts

        self.toasts = SessionToasts(self)
        completer = HubCompleter(
            lambda: self.file_root,
            self.t,
            components=lambda: self.component_commands,
        )
        from ..widget.forms.followup import FollowupSuggestion

        self.followup = FollowupSuggestion(self)
        self.composer = TextArea(
            auto_suggest=self.followup,
            # Two input rows, one hint row and two borders make five rows.
            multiline=HubView.MULTILINE,
            height=Dimension(
                min=HubView.COMPOSER_MIN_ROWS, max=HubView.COMPOSER_MAX_ROWS
            ),
            dont_extend_height=HubView.FIXED_COMPOSER_HEIGHT,
            wrap_lines=HubView.WRAP_COMPOSER,
            prompt=HubView.PROMPT_FORMAT.format(prompt=icon.PROMPT),
            style=ThemeSelector.COMPOSER.style,
            completer=ThreadedCompleter(completer),
            complete_while_typing=Condition(
                lambda: self.question.item is HubView.ABSENT
                and not self.input_history.browsing
            ),
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
            lambda: (
                []
                if self.no_sessions
                else [
                    SidebarItem(self._draft_key(i), session.title, session.status)
                    for i, session in enumerate(self.sessions)
                ]
            ),
            lambda: self._draft_key(self.selected),
            self._select_sidebar_session,
            on_activate=self._focus_composer,
        )
        from .chat.projects import ChatProjects
        from ..widget.sidebar_panel import SidebarPanel

        self.projects = ChatProjects(self)
        self._sidebar = ConditionalContainer(
            SidebarPanel(
                [
                    HSplit(
                        [
                            SidebarHeading(
                                lambda: self.icons.project
                                + HubView.HEADING_SEPARATOR
                                + self.t(MessageKey.SETTINGS_PROJECTS)
                            ),
                            Window(
                                self.projects.control, style=ThemeSelector.SIDEBAR.style
                            ),
                        ]
                    ),
                    HSplit(
                        [
                            SidebarHeading(lambda: self.t(MessageKey.SESSIONS)),
                            Window(
                                self._session_control, style=ThemeSelector.SIDEBAR.style
                            ),
                        ]
                    ),
                ],
                self.profile_bar,
                width=self.sidebar_width,
                project_fraction=HubView.PROJECT_FRACTION,
                controls=(self.projects.control, self._session_control),
            ),
            Condition(
                lambda: self._columns() >= HubView.SIDEBAR_MIN_COLUMNS
                or self._session_focus
            ),
        )
        from ..widget.composer_actions import ComposerActions

        self.composer_actions = ComposerActions(self)
        self._composer_frame = InputFrame(
            HSplit(
                [
                    self.question.container,
                    self.composer,
                ]
            ),
            title=lambda: self._focus_heading(
                self.composer.control, self.recipient_title()
            ),
            hint=lambda: self.t(MessageKey.INPUT_HINT)
            + (
                HubView.HINT_SEPARATOR + self.t(MessageKey.FOLLOWUP_HINT)
                if self.followup.get_suggestion(
                    self.composer.buffer, self.composer.buffer.document
                )
                else HubView.NO_HINT
            ),
            style=ThemeSelector.INPUT_FRAME.style,
            actions=self.composer_actions,
        )
        from ..widget.auxiliary import AuxiliaryBar
        from ..widget.forms.file_attachment import attach_file

        self.auxiliary = AuxiliaryBar(
            [
                (
                    AuxiliaryIcon.HISTORY.value,
                    lambda: (
                        self.on_history() if self.on_history else self._not_connected()
                    ),
                ),
                (
                    AuxiliaryIcon.TAGS.value,
                    lambda: self.output.invoke(EngineOutput.TAGS),
                ),
                (AuxiliaryIcon.PARTICIPANTS.value, lambda: self.output.participants.open()),
                (
                    AuxiliaryIcon.FIND.value,
                    lambda: self.output.invoke(EngineOutput.FIND),
                ),
                (AuxiliaryIcon.ATTACH.value, lambda: attach_file(self)),
                (
                    AuxiliaryIcon.LOGS.value,
                    lambda: (
                        self.on_activity()
                        if self.on_activity
                        else self._not_connected()
                    ),
                ),
                (
                    AuxiliaryIcon.SETTINGS.value,
                    lambda: self.settings.open_engine(HubEngineIdentity.LOOP),
                ),
            ],
            lambda: self.icons,
            self._focus_composer,
        )
        conversation = HSplit(
            [
                DynamicContainer(lambda: self.output),
                self.auxiliary,
                self._composer_frame,
            ]
        )
        self.header = HeaderBar(
            lambda: self.t(MessageKey.PROFILE_HOME), icons=lambda: self.icons,
            restart=self.restart, minimize=self.minimize,
        )
        self.chat_page = TwoPanelPage(
            header=self.header,
            footer=self.shortcut_bar(),
            sidebar=self._sidebar,
            main=conversation,
            progress=self.progress,
            notice=lambda: self.notice,
            sidebar_visible=self._sidebar.filter,
            sidebar_controls=lambda: (
                self._session_control,
                self.projects.control,
                *self.profile_bar.controls,
            ),
            categories=(self.projects.control, self._session_control),
            focus_sidebar=self._focus_sessions,
            focus_main=self._focus_composer,
            sidebar_width=self.sidebar_width,
            resize_sidebar=self.settings.set_sidebar_width,
        )
        panel = self.chat_page.container
        # PTK defers cursor-positioned floats with an absolute drawing priority.
        # An enclosing host Float can inherit a later priority than our popups.
        # Paint the Hub body at this container's own base, then draw its overlays.
        root = FloatContainer(
            DynamicContainer(
                lambda: self.settings.container if self.settings_open else panel
            ),
            style=ThemeSelector.ROOT.style,
            z_index=HubView.BODY_LAYER,
            floats=[
                Float(
                    ConditionalContainer(
                        DynamicContainer(lambda: self._dialog or Window()),
                        Condition(
                            lambda: self._dialog is not HubView.ABSENT
                            and self.output.floating_dialog is HubView.ABSENT
                        ),
                    ),
                    z_index=HubView.DIALOG_LAYER,
                ),
                Float(
                    ConditionalContainer(
                        DynamicContainer(
                            lambda: self.output.floating_dialog or Window()
                        ),
                        Condition(
                            lambda: self.output.floating_dialog is not HubView.ABSENT
                        ),
                    ),
                    bottom=HubView.OVERLAY_MARGIN,
                    right=HubView.OVERLAY_MARGIN,
                    z_index=HubView.DIALOG_LAYER,
                ),
                Float(
                    ConditionalContainer(
                        self.toasts.container,
                        Condition(
                            lambda: self.toasts.active()
                            and self._dialog is HubView.ABSENT
                        ),
                    ),
                    bottom=HubView.OVERLAY_MARGIN,
                    right=HubView.OVERLAY_MARGIN,
                    width=self.toasts.width,
                    height=self.toasts.height,
                    z_index=HubView.TOAST_LAYER,
                ),
                # Keep one cursor overlay after every dialog. Nested cursor floats
                # can run before the dialog's Windows have published their position.
                Float(
                    CompletionsMenu(
                        max_height=HubView.COMPLETION_ROWS,
                        scroll_offset=HubView.COMPLETION_SCROLL_OFFSET,
                    ),
                    xcursor=HubView.CURSOR_ANCHORED,
                    ycursor=HubView.CURSOR_ANCHORED,
                    z_index=HubView.COMPLETION_LAYER,
                ),
            ],
        )
        # Register chat bindings at the application level too: PTK's cached
        # parent chain can still describe settings immediately after switching.
        from .input.bindings import HubBindings

        self.bindings = HubBindings(self)
        self.navigation_keys = self.bindings.keys
        modal = HSplit([root], modal=HubView.MODAL, key_bindings=self.navigation_keys)
        self.container = ConditionalContainer(modal, Condition(lambda: self.visible))

    @staticmethod
    def _columns():
        return get_app().output.get_size().columns

    @staticmethod
    def _rows():
        return get_app().output.get_size().rows

    def sidebar_width(self):
        minimum = self.profile_bar.minimum_width
        return max(
            minimum,
            min(
                self.theme.sidebar_width,
                max(minimum, self._columns() // HubView.SIDEBAR_WIDTH_DIVISOR),
            ),
        )

    @property
    def icons(self):
        return icon.for_style(self.theme.icon_style)

    def shortcut_bar(self):
        return ShortcutBar(lambda: self.bindings.hints(), self._columns)

    def _focus_heading(self, control, text):
        focused = self.visible and get_app().layout.current_control == control
        return [
            (
                ThemeSelector.FOCUSED.style if focused else HubView.NO_STYLE,
                (
                    icon.SELECTED + HubView.HEADING_SEPARATOR
                    if focused
                    else HubView.UNFOCUSED_PREFIX
                )
                + text,
            )
        ]

    @property
    def active_page(self):
        return self.settings.layout if self.settings_open else self.chat_page

    def mark_sidebar_focus(self):
        self._session_focus = self.SIDEBAR_FOCUS

    def _focus_sessions(self):
        self.composer.buffer.cancel_completion()
        self._session_focus = HubView.SIDEBAR_FOCUS
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
        self._session_focus = HubView.COMPOSER_FOCUS
        focus_control(self.composer.control)

        get_app().invalidate()

    def _not_connected(self):
        self.notice = self.t(MessageKey.NOT_CONNECTED)

    def _select_sidebar_session(self, key):
        self._session_focus = HubView.SIDEBAR_FOCUS
        self.select(
            next(i for i in range(len(self.sessions)) if self._draft_key(i) == key)
        )

    def toggle_details(self):
        self.output.invoke(EngineOutput.DETAILS)

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
        output.set_messages(
            self.sessions[self.selected].messages,
            self._positions.get(self._draft_key(self.selected)),
        )
        app.layout.update_parents_relations()
        if self.visible:
            if focus in list(app.layout.find_all_controls()):
                focus_control(focus)
            elif not self.settings_open:
                self._focus_composer()
        app.invalidate()

    def close(self) -> None:
        self.toasts.close()
        self.native_clipboard.close()
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

    @property
    def participant_ids(self):
        return self._participant_sets.get(self._draft_key(self.selected), self._default_participants)

    @participant_ids.setter
    def participant_ids(self, identifiers):
        self._participant_sets[self._draft_key(self.selected)] = tuple(identifiers)

    @property
    def recipient_id(self):
        return self._recipients.get(self._draft_key(self.selected), self.NO_AGENT)

    @recipient_id.setter
    def recipient_id(self, identifier):
        self._recipients[self._draft_key(self.selected)] = identifier

    @property
    def recipients(self):
        for group in self.participant_groups:
            if group.id == self.recipient_id:
                return group.members
        return (self.recipient_id,) if self.recipient_id else self.participant_ids

    @property
    def participant_groups(self):
        return self._participant_groups.get(self._draft_key(self.selected), ())

    def recipient_title(self):
        target = (self.agent_titles.get(self.recipient_id, self.recipient_id)
                  if self.recipient_id else self.t(MessageKey.PARTICIPANTS_ALL))
        target = next((group.title for group in self.participant_groups
                       if group.id == self.recipient_id), target)
        return self.t(MessageKey.RECIPIENT_TITLE, target=target)

    @property
    def agent_id(self):
        key = (HubSnapshot.project_identifier(self), self._draft_key(self.selected))
        return self._agent_choices.get(key, self._default_agent_id)

    @agent_id.setter
    def agent_id(self, identifier):
        key = (HubSnapshot.project_identifier(self), self._draft_key(self.selected))
        self._agent_choices[key] = identifier

    @property
    def engine(self):
        """Selected Agent's Engine, for parameter editing only."""
        return self.agent_engines.get(self.agent_id, HubView.NO_ENGINE)

    def _draft_key(self, index):
        if self.no_sessions:
            return HubView.EMPTY_PREFIX + HubSnapshot.project_identifier(self)
        return self.sessions[index].id or HubView.SAMPLE_KEY.format(index=index)

    def toggle_settings(self, event=ABSENT):
        app = event.app if event is not HubView.ABSENT else get_app()
        if not self.visible:
            self.show(app)
        if self._dialog is not HubView.ABSENT:
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
            self._session_focus = HubView.COMPOSER_FOCUS
            focus_control(self.composer)
        # Global shortcuts follow the focused window's parent containers.
        # Refresh them before the next key, even if rendering is throttled.
        app.layout.update_parents_relations()
        app.invalidate()

    def restart(self):
        if self.on_restart is not HubView.ABSENT:
            self.on_restart()
        else:
            self._not_connected()

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
        self.composer.buffer.suggestion = HubView.ABSENT
        self.composer.text = self._drafts.get(
            self._draft_key(index), HubView.EMPTY_DRAFT
        )
        key = (HubSnapshot.project_identifier(self), self.sessions[index].id)
        self.output.set_messages(
            self.preparing.project(key, self.sessions[index].messages),
            self._positions.get(self._draft_key(index)),
        )
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
        app.ttimeoutlen = app.timeoutlen = HubView.INPUT_SEQUENCE_TIMEOUT
        self.visible = HubView.SHOWN
        from ..widget.clipboard import SystemClipboard

        app.clipboard = SystemClipboard(
            app,
            app.clipboard,
            environment=self.clipboard_environment,
            x11=self.native_clipboard,
        )
        self.mouse.attach(app)
        self._session_focus = HubView.COMPOSER_FOCUS
        if self.on_open:
            self.on_open(app)
        focus_control(
            self.settings.left_control() if self.settings_open else self.composer
        )
        app.invalidate()

    def hide(self, app):
        if not self.visible:
            return
        self.remember_position()
        self.output.deactivate()
        if self.on_save_view:
            self.on_save_view()
        self.dialogs.clear_state()
        self.output.restore_position(
            self._positions.get(self._draft_key(self.selected))
        )
        if self._previous_focus in list(app.layout.find_all_controls()):
            app.layout.focus(self._previous_focus)
        self.visible = HubView.HIDDEN
        self.mouse.detach()
        from ..widget.clipboard import SystemClipboard

        if isinstance(app.clipboard, SystemClipboard):
            app.clipboard = app.clipboard.original
        if self._input_timeouts is not HubView.ABSENT:
            app.ttimeoutlen, app.timeoutlen = self._input_timeouts
            self._input_timeouts = HubView.ABSENT
        app.invalidate()

    def toggle(self, event):
        self.hide(event.app) if self.visible else self.show(event.app)
