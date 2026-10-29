"""Settings navigation: two panels with fields and actions."""

from hub.locales.keys import MessageKey, MessagePattern

from llm.services.schema import ProjectConfiguration

from dataclasses import replace
from ...backend.project.settings_contracts import (
    ProjectSummaries,
    SettingsCatalog,
    SettingsOperation,
)
from ...config.sections import PreferenceSection
from ...config.theme_style import ThemeSelector
from ...model import HubSnapshot

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import DynamicContainer, HSplit, Window
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Label

from .pages import Page, GlobalPage, ProjectPage, SettingsTab
from ...config.theme import HubTheme
from ...widget.controls import TextArea
from ...widget.forms.settings_viewport import SettingsPane
from .navigation import SettingsNavigation, SettingsZone
from .sections import SettingsSection
from .state import SettingsActivity, SettingsTarget
from ...asset.icon import AuxiliaryIcon
from .drafts import SettingsDrafts
from ...widget.sidebar import SidebarHeading, SidebarItem, SidebarList
from ..layout import TwoPanelPage
from ...widget.header import HeaderBar
from ...widget.actions import ActionFrame
from ...widget.content_inset import ContentInset


class SettingsScreen:
    NO_PAGE = None
    NO_REQUEST = None
    NO_SCHEMA = None
    NO_PENDING = None
    NO_ENGINE_TARGET = None
    NO_COMPLETION = None
    NO_ERROR = None
    NO_RESULT = None
    NO_DIALOG = None
    EMPTY_QUERY = Page.query
    EMPTY_TEXT = ""
    EMPTY_STYLE = ""
    ICON_GAP = " "
    UNSELECTED_PREFIX = "  "
    TITLE_SEPARATOR = " • "
    STATUS_SEPARATOR = " • "
    DIRTY_PREFIX = "* "
    ERROR_OPERATION = "settings"
    GLOBAL_KEYS = (*tuple(section.value for section in PreferenceSection), SettingsSection.DEBUGGING)
    EMPTY_PAGE_HEIGHT = 1
    MINIMUM_WIDTH = 1
    MAIN_INSET = 1
    MINIMUM_MAIN_HEIGHT = 3
    FLEX_WEIGHT = 1
    SIDEBAR_GAP_HEIGHT = 1
    SCROLL_TOP = 0
    QUERY_HEIGHT = 1
    QUERY_MULTILINE = False
    ACCEPT_QUERY = True
    SHOW_SCROLLBAR = True
    PERSIST_WIDTH = True
    SIDEBAR_WIDTH_DIVISOR = 2
    FORWARD_FOCUS = False

    def __init__(self, view):
        self.view, self.t = view, view.t
        self.navigation = SettingsNavigation(self)
        self.drafts = SettingsDrafts(self)
        from .json_editor import SettingsJsonEditor

        self.json_editor = SettingsJsonEditor(self)
        self.request = self.NO_REQUEST
        self.busy = SettingsActivity.IDLE
        self.status = SettingsActivity.NO_STATUS
        self.query = self.EMPTY_QUERY
        self.schema = self.NO_SCHEMA
        self.projects = []
        self.usage = []
        self._usage_requested = False
        self._usage_epoch = 0
        self.global_keys = self.GLOBAL_KEYS
        self.selected = PreferenceSection.PROFILE.value
        self.page = self.NO_PAGE
        self._pending_selection = self.NO_PENDING
        self._engine_target = SettingsTarget.UNSET
        self._component_target = SettingsTarget.UNSET
        self._left = HSplit([])
        self.global_list = SidebarList(
            lambda: [
                SidebarItem(key, self._caption(key, self._item_title(key)))
                for key in self.global_keys
            ],
            lambda: self.navigation.left_key,
            self.select_left,
        )
        self.project_list = SidebarList(
            self.project_items, lambda: self.navigation.left_key, self.select_left
        )
        self.main = SettingsPane(
            DynamicContainer(
                lambda: (
                    self.page.container
                    if self.page
                    else Window(height=self.EMPTY_PAGE_HEIGHT)
                )
            ),
            show_scrollbar=self.SHOW_SCROLLBAR,
            width=lambda: Dimension(
                min=self.MINIMUM_WIDTH,
                preferred=max(self.MINIMUM_WIDTH, self.main_width() - self.MAIN_INSET),
            ),
        )
        from ...widget.tab_page import TabPage

        self.tabs = TabPage(
            [
                (
                    SettingsTab.CONFIG.value,
                    self.t(MessageKey.SETTINGS_TAB_CONFIG),
                    self.main,
                ),
                (
                    SettingsTab.POLICY.value,
                    self.t(MessageKey.SETTINGS_TAB_POLICY),
                    self.main,
                ),
            ],
            width=self.main_width,
            on_change=self.tab_changed,
            on_enter=self.enter_tab,
            on_leave=self.navigation.finish_edit,
        )
        bottom = DynamicContainer(
            lambda: self.page.bottom if self.page else Label(self.EMPTY_TEXT)
        )
        self.actions = ActionFrame(
            bottom, width=self.main_dimension, title=self.action_title
        )
        from ...widget.auxiliary import AuxiliaryBar

        self.auxiliary = AuxiliaryBar(
            [
                (AuxiliaryIcon.FILTER.value, self.search),
                (AuxiliaryIcon.JSON.value, self.show_json),
            ],
            lambda: view.icons,
            lambda: self.navigation.focus_zone(SettingsZone.ACTIONS),
        )
        self.layout = TwoPanelPage(
            header=HeaderBar(
                lambda: self.t(MessageKey.SETTINGS_TITLE), icons=lambda: view.icons,
                restart=view.restart, minimize=view.minimize,
            ),
            sidebar=DynamicContainer(lambda: self._left),
            main=HSplit(
                [
                    ContentInset(
                        self.tabs,
                        heading=self.page_title,
                        width=self.main_dimension,
                        height=Dimension(
                            min=self.MINIMUM_MAIN_HEIGHT, weight=self.FLEX_WEIGHT
                        ),
                    ),
                    self.auxiliary,
                    self.actions,
                ],
                width=self.main_dimension,
            ),
            progress=view.progress,
            notice=self.status_text,
            status_annotation=self.filter_label,
            footer=view.shortcut_bar(),
            sidebar_controls=lambda: (
                self.global_list,
                self.project_list,
                *view.profile_bar.controls,
            ),
            categories=(self.global_list, self.project_list),
            focus_sidebar=self.navigation.switch_panel,
            focus_main=self.tabs.focus_tabs,
            sidebar_width=view.sidebar_width,
            resize_sidebar=self.set_sidebar_width,
        )
        self.layout.main_region.on_click = self.layout.focus_main
        self.container = self.layout.container
        self._sidebar()

    @property
    def projects(self):
        return self._project_rows.to_records()

    @projects.setter
    def projects(self, value):
        self._project_rows = ProjectSummaries(value)

    def select_location(self, key):
        self.selected = self.navigation.left_key = key

    def reset_draft_page(self):
        self.clear_page()
        self._engine_target = self._component_target = SettingsTarget.UNSET
        self._pending_selection = self.NO_PENDING

    def clear_page(self):
        self.page = self.NO_PAGE

    def main_width(self):
        return max(
            self.MINIMUM_WIDTH,
            self.view._columns() - self.view.sidebar_width() - self.MAIN_INSET,
        )

    def tab_changed(self, key):
        if isinstance(self.page, ProjectPage):
            self.page.set_section(self.page.section)
        elif self.page is not self.NO_PAGE:
            self.page.filter(self.query)
        self.main.vertical_scroll = self.SCROLL_TOP
        self.navigation.reset_positions()

    def enter_tab(self):
        if self.page and self.page.body_widgets():
            self.navigation.focus_zone(SettingsZone.FIELDS)

    def main_dimension(self):
        # The host may inset Hub's Float. The terminal width is a preference,
        # never a minimum that can make an otherwise usable page disappear.
        return Dimension(min=self.MINIMUM_WIDTH, preferred=self.main_width())

    def action_title(self):
        from ...asset.icon import SELECTED

        title = self._project_rows.title_or(
            self.drafts.current_project_id, self.view.project_title
        )
        active = not self.layout.sidebar_focused
        return [
            (
                ThemeSelector.FOCUSED.style if active else self.EMPTY_STYLE,
                (SELECTED + self.ICON_GAP if active else self.UNSELECTED_PREFIX)
                + self.t(MessageKey.SETTINGS_CURRENT_PROJECT, title=title),
            )
        ]

    def page_title(self) -> str:
        if self.page is self.NO_PAGE:
            return self.EMPTY_TEXT
        if isinstance(self.page, GlobalPage):
            category, key = MessageKey.SETTINGS_GLOBAL, self.page.location
        else:
            category, key = MessageKey.SETTINGS_PROJECTS, self.page.section
        return self.t(category) + self.TITLE_SEPARATOR + self._item_title(key)

    def _sidebar(self):
        previous = self.navigation.focused_key()
        if self.navigation.left_key not in self.navigation.left_items():
            self.navigation.left_key = PreferenceSection.PROFILE.value
        from ...widget.sidebar_panel import SidebarPanel

        self._left = SidebarPanel(
            [
                HSplit(
                    [
                        SidebarHeading(
                            lambda: self.view.icons.settings
                            + self.ICON_GAP
                            + self.t(MessageKey.SETTINGS_GLOBAL)
                        ),
                        Window(self.global_list, height=len(self.global_keys)),
                        Window(height=self.SIDEBAR_GAP_HEIGHT),
                    ]
                ),
                HSplit(
                    [
                        SidebarHeading(
                            lambda: self.view.icons.project
                            + self.ICON_GAP
                            + self.t(MessageKey.SETTINGS_PROJECTS)
                        ),
                        Window(self.project_list),
                    ]
                ),
            ],
            self.view.profile_bar,
            width=self.view.sidebar_width,
            controls=(self.global_list, self.project_list),
        )
        if previous and self.view.visible and self.view.settings_open:
            get_app().layout.focus(self.navigation.left_control())

    def select_left(self, key):
        self.navigation.left_key = key
        if self.busy:
            self._pending_selection = key
        else:
            self.choose(key)
        get_app().invalidate()

    def _caption(self, key, title):
        if key == SettingsSection.DEBUGGING:
            key = PreferenceSection.GENERAL.value
        page = self.drafts.pages.get(key)
        return (self.DIRTY_PREFIX if page and page.dirty else self.EMPTY_TEXT) + title

    def _item_title(self, key: str) -> str:
        kind, name = SettingsSection.parts(key)
        if kind in SettingsSection.NAMED_KINDS:
            return self.t(MessagePattern.settings_item(kind), name=name)
        return self.t(MessagePattern.settings(name or kind))

    def project_items(self):
        keys = [SettingsSection.GENERAL]
        page = self.drafts.pages.get(self.drafts.current_project_id)
        if isinstance(page, ProjectPage):
            keys = page.section_choices()
        return [SidebarItem(key, self._item_title(key)) for key in keys]

    def project_changed(self, identifier):
        changed = identifier != self.drafts.current_project_id
        self.drafts.select_project(identifier)
        if changed:
            self._engine_target = self._component_target = SettingsTarget.UNSET
        if isinstance(self.page, ProjectPage) and self.page.identifier != identifier:
            self.page = self.NO_PAGE
            self.select_location(SettingsSection.GENERAL)

    def filter(self, query):
        if self.page is self.NO_PAGE:
            return
        self.navigation.finish_edit()
        self.query = query.strip()
        self.page.filter(self.query)
        self.main.vertical_scroll = self.SCROLL_TOP
        self.navigation.reset_positions()
        self.status = SettingsActivity.NO_STATUS
        self.navigation.focus_zone(SettingsZone.FIELDS)

    def search(self):
        if self.page is self.NO_PAGE or self.busy:
            return
        field = TextArea(
            text=self.query, height=self.QUERY_HEIGHT, multiline=self.QUERY_MULTILINE
        )

        def accept():
            self.filter(field.text)

        self.view.open_dialog(
            self.t(MessageKey.SETTINGS_SEARCH),
            HSplit([Label(self.t(MessageKey.SETTINGS_SEARCH_HINT)), field]),
            accept,
            field,
        )

        def entered(_):
            self.view.close_dialog()
            accept()
            return self.ACCEPT_QUERY

        field.buffer.accept_handler = entered

    def status_text(self):
        return self.STATUS_SEPARATOR.join(
            part for part in (self.filter_label(), self.status) if part
        )

    def filter_label(self):
        return (
            self.t(MessageKey.SETTINGS_SEARCH_ACTIVE, query=self.query)
            if self.query
            else self.EMPTY_TEXT
        )

    def show_json(self):
        self.json_editor.open()

    def open_engine(self, engine=NO_ENGINE_TARGET):
        project = HubSnapshot.project_identifier(self.view)
        if not project:
            self.view.toggle_settings()
            return
        self.project_changed(project)
        self._engine_target = SettingsTarget(project, engine or self.view.engine)
        if not self.view.settings_open:
            self.view.toggle_settings()
        if self.schema is not self.NO_SCHEMA and not self.busy:
            self.choose(project)

    def open_component(self, name):
        project = HubSnapshot.project_identifier(self.view)
        if not project:
            return
        self.project_changed(project)
        self._component_target = SettingsTarget(project, name)
        if not self.view.settings_open:
            self.view.toggle_settings()
        if self.schema is not self.NO_SCHEMA and not self.busy:
            self.choose(project)

    def resize(self, delta):
        self.set_sidebar_width(self.view.theme.sidebar_width + delta)

    def set_sidebar_width(self, width, *, persist=PERSIST_WIDTH):
        if self.busy:
            return
        minimum = self.view.profile_bar.minimum_width
        width = max(
            minimum,
            min(
                HubTheme.MAXIMUM_SIDEBAR_WIDTH,
                max(minimum, self.view._columns() // self.SIDEBAR_WIDTH_DIVISOR),
                width,
            ),
        )
        if width == self.view.theme.sidebar_width and (
            not persist or width == self.drafts.saved_sidebar_width
        ):
            return
        if width != self.view.theme.sidebar_width:
            self.view.set_theme(replace(self.view.theme, sidebar_width=width))
        self.drafts.set_sidebar_width(width, persist=persist)
        from ..view import HubView

        save = HubView.sidebar_width_callback(self.view)
        if save and persist:
            save(width)

    def error(self, error):
        from ..error_presentation import operation_notice
        from ...errors.adapters import from_exception

        info = from_exception(
            error,
            operation=self.ERROR_OPERATION,
            validation=isinstance(error, (ValueError, TypeError)),
        )
        self.view.operation_diagnostics.record_view(self.view, info)
        self.status = self.t(
            MessageKey.ERROR,
            error=operation_notice(error if isinstance(error, str) else info, self.t),
        )
        get_app().invalidate()

    def call(self, operation, *args, completed=NO_COMPLETION, blocking=True):
        if self.busy and blocking:
            return
        if self.request is self.NO_REQUEST:
            self.error(self.t(MessageKey.NOT_CONNECTED))
            return
        if blocking:
            self.busy = SettingsActivity.BUSY
            self.status = SettingsActivity.NO_STATUS
        progress = self.view.progress.start(
            self.t(
                MessageKey.PROGRESS_SETTINGS_LOAD
                if operation
                in (SettingsOperation.CATALOG, SettingsOperation.LOAD_PROJECT, SettingsOperation.USAGE)
                else MessageKey.PROGRESS_SETTINGS_WORK
            )
        )

        def done(value, error=self.NO_ERROR):
            self.view.progress.finish(progress)
            if blocking:
                self.busy = SettingsActivity.IDLE
                self.status = SettingsActivity.NO_STATUS
            if error is not self.NO_ERROR:
                self.error(error)
            if completed:
                completed(value)
            if not self.busy and self._pending_selection is not self.NO_PENDING:
                selected, self._pending_selection = (
                    self._pending_selection,
                    self.NO_PENDING,
                )
                self.select_left(selected)
            self.load_usage()
            get_app().invalidate()

        try:
            self.request(operation, args, self.view.progress.callback(progress, done))
        except Exception as error:
            done(self.NO_RESULT, error)

    def reset_usage(self):
        self._usage_epoch += 1
        self._usage_requested = False

    def load_usage(self):
        if (self.busy or self._usage_requested or self.request is None or self.schema is None
                or self.selected != PreferenceSection.PROFILE.value):
            return
        self._usage_requested = True
        epoch = self._usage_epoch

        def loaded(value):
            if value is None or epoch != self._usage_epoch:
                return
            self.usage = value
            page = self.drafts.pages.get(PreferenceSection.PROFILE.value)
            if page is not None:
                page.refresh_usage()

        self.call(SettingsOperation.USAGE, completed=loaded, blocking=False)

    def open(self):
        self.project_changed(
            HubSnapshot.project_identifier(self.view) or self.drafts.current_project_id
        )
        if self.request is self.NO_REQUEST:
            self.drafts.preview_preferences()
            self.choose(PreferenceSection.PROFILE.value)
            self.status = self.t(MessageKey.PREVIEW_NOTICE)
            return
        self.call(SettingsOperation.CATALOG, completed=self.loaded_catalog)

    def loaded_catalog(self, value):
        if value is self.NO_RESULT:
            return
        observation = SettingsCatalog.observe(value)
        self.schema, self._project_rows = observation.schema, observation.projects
        if self.drafts.current_project_id not in self._project_rows.identifiers():
            self.drafts.current_project_id = observation.active_project
        self.drafts.replace_preferences(observation.preferences)
        self.reset_usage()
        self.drafts.prepare_preferences()
        self._sidebar()
        if self._engine_target:
            self.choose(self._engine_target.project_id)
        elif self._component_target:
            self.choose(self._component_target.project_id)
        elif self.page is self.NO_PAGE:
            self.choose(PreferenceSection.PROFILE.value)
        if (
            self.drafts.current_project_id
            and self.drafts.current_project_id not in self.drafts.pages
            and not self.busy
        ):

            def loaded(record):
                if record is not self.NO_RESULT:
                    self.drafts.pages[ProjectConfiguration.view(record).project_id] = (
                        ProjectPage(self, record)
                    )
                    self._sidebar()

            self.call(
                SettingsOperation.LOAD_PROJECT,
                self.drafts.current_project_id,
                completed=loaded,
            )

    def choose(self, key):
        if self.busy:
            return
        self.navigation.finish_edit()
        if key == self.drafts.current_project_id:
            key = SettingsSection.GENERAL
        if key not in (*self.global_keys, *[item.key for item in self.project_items()]):
            return
        self.selected = key
        if key in self.navigation.left_items():
            self.navigation.left_key = key
        if (
            self.view.visible
            and self.navigation.focused_key() is not SettingsNavigation.NO_KEY
        ):
            get_app().layout.focus(self.navigation.left_control())
        if key in self.global_keys:
            storage = PreferenceSection.GENERAL.value if key == SettingsSection.DEBUGGING else key
            if storage not in self.drafts.pages:
                self.drafts.pages[storage] = GlobalPage(
                    self, storage, self.drafts.preference_values(storage))
            self.show_page(self.drafts.pages[storage])
        else:
            page = self.drafts.pages.get(self.drafts.current_project_id)
            if page is not self.NO_PAGE:
                page.set_section(key)
                self.show_page(page)
            elif self.drafts.current_project_id:
                self.call(
                    SettingsOperation.LOAD_PROJECT,
                    self.drafts.current_project_id,
                    completed=self.loaded_project,
                )
        self.load_usage()

    def show_page(self, page):
        left = self.navigation.focused_key() is not SettingsNavigation.NO_KEY
        self.page = page
        if isinstance(page, GlobalPage) and page.general_form is not page.NO_GENERAL_FORM:
            page.set_section(self.selected)
        page.filter(self.query)
        self.main.vertical_scroll = self.SCROLL_TOP
        self.navigation.reset_positions()
        if (
            self._engine_target
            and ProjectPage.identifier_for(page) == self._engine_target.project_id
        ):
            engine = self._engine_target.name
            self._engine_target = SettingsTarget.UNSET
            section = SettingsSection.engine(page.engine_keys.get(engine, engine))
            if section in {item.key for item in self.project_items()}:
                self.tabs.selected = SettingsTab.CONFIG.value
                page.set_section(section)
                self.select_location(section)
                self.filter(self.EMPTY_QUERY)
        if (
            self._component_target
            and ProjectPage.identifier_for(page) == self._component_target.project_id
        ):
            name = self._component_target.name
            self._component_target = SettingsTarget.UNSET
            section = SettingsSection.component(name)
            if section in {item.key for item in self.project_items()}:
                self.tabs.selected = SettingsTab.CONFIG.value
                page.set_section(section)
                self.select_location(section)
                self.filter(self.EMPTY_QUERY)
        if (
            self.view.visible
            and self.view.settings_open
            and self.view.dialogs.current is self.NO_DIALOG
            and not left
        ):
            self.navigation.focus_zone(self.navigation.main_zone)
        get_app().layout.update_parents_relations()
        get_app().invalidate()

    def loaded_project(self, record):
        if record is not self.NO_RESULT:
            identifier = ProjectConfiguration.view(record).project_id
            query = Page.query_or(self.drafts.pages.get(identifier), self.EMPTY_QUERY)
            section = ProjectPage.section_or(
                self.drafts.pages.get(identifier), self.selected
            )
            page = self.drafts.pages[identifier] = ProjectPage(self, record)
            section = (
                section
                if section in {item.key for item in self.project_items()}
                else SettingsSection.GENERAL
            )
            page.set_section(section)
            if query:
                page.filter(query)
            self.select_location(section)
            if (
                self.view.visible
                and self.navigation.focused_key() is not SettingsNavigation.NO_KEY
            ):
                get_app().layout.focus(self.navigation.left_control())
            self.show_page(page)

    def saved_project(self, record):
        if record is self.NO_RESULT:
            return
        self.drafts.select_project(ProjectConfiguration.view(record).project_id)
        self.loaded_project(record)
        identifier, title = (
            ProjectConfiguration.view(record).project_id,
            ProjectConfiguration.view(record).values.title,
        )
        self._project_rows.replace(identifier, title)
        self._sidebar()
        self.status = self.t(MessageKey.SETTINGS_SAVED)

    def left_control(self):
        return self.navigation.left_control()

    def finish_edit(self):
        return self.navigation.finish_edit()

    def edit_field(self, field):
        return self.navigation.edit_field(field)

    def switch_panel(self):
        return self.navigation.switch_panel()

    def focus(self, reverse=FORWARD_FOCUS):
        return self.navigation.focus(reverse=reverse)

    def move(self, key):
        return self.navigation.move(key)

    def save(self):
        return self.drafts.save()

    def discard(self):
        return self.drafts.discard()

    def reload(self, page):
        return self.drafts.reload(page)

    def cancel(self, page):
        return self.drafts.cancel(page)

    @property
    def actions_focused(self):
        return self.navigation.actions_focused

    @property
    def editing(self):
        return self.navigation.editing

    @property
    def project_focused(self):
        return self.navigation.project_focused
