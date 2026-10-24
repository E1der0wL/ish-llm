"""Draft settings pages with field and action regions."""

from hub.locales.keys import MessageKey, MessagePattern

from llm.core.configuration_views import EngineConfigurationKey

from llm.services.schema import (
    ProjectFormSchemas,
    ProjectConfiguration,
    ProjectSchemaDescription,
)
from llm.services.configuration_records import ProjectConfigForm
from llm.core.schema import SchemaKeyword, SchemaBoolean
from llm.components.rag.identity import RAGIdentity

from copy import deepcopy
from dataclasses import replace
from enum import StrEnum

from prompt_toolkit.layout import HSplit
from prompt_toolkit.widgets import Label

from ...widget.section import section
from ...widget.buttons import SaveButton
from ...widget.actions import ActionBar
from ...config.profile import UserProfile
from ...config.general import GeneralSettings, GeneralSettingsDraft
from ...config.metadata import SettingMetadata
from ...config.sections import PreferenceSection
from ...config.preferences import PreferencesStore
from ...config.preference_records import SidebarWidth
from ...config.theme_style import ThemeSelector
from ...backend.project.settings_contracts import (
    SettingsOperation,
    ProjectUsageObservation,
)
from .global_schema import GlobalSettingsSchema
from .sections import SettingsSection
from ...widget.forms.settings import SchemaForm
from ...widget.forms.settings_viewport import SettingsBody
from ...config.theme import HubTheme
from ...widget.controls import Button, TextArea


class SettingsTab(StrEnum):
    CONFIG = "config"
    POLICY = "policy"


class Page:
    query = ""
    UNFILTERED = "_unfiltered"
    GROUPS = "groups"
    NO_GROUPS = None
    FIELD_TABS = "field_tabs"
    JSON_CHANGED = "json_changed"
    JSON_BASELINE = "json_baseline"
    NO_JSON_CHANGE = False
    EMPTY_LABEL = ""
    SPACE = " "
    ACTION_WIDTH = 10
    MINIMUM_ACTION_WIDTH = 1
    ACTION_INSET = 2

    def json_baseline_for(self, default):
        return getattr(self, self.JSON_BASELINE, default)

    def set_json_baseline(self, baseline):
        self.json_baseline = baseline

    def set_json_changed(self, changed):
        self.json_changed = changed

    def filter(self, query):
        if not hasattr(self, self.UNFILTERED):
            self._unfiltered = self.container
        self.query = query.strip().casefold()
        self._filtered_fields = self._matching_fields()
        if not self.section_fields():
            self.container = SettingsBody(
                [Label(self.screen.t(MessageKey.SETTINGS_TAB_EMPTY))], []
            )
            return
        self.container = (
            SettingsBody(
                self.grouped_children(self.fields())
                or [Label(self.screen.t(MessageKey.SETTINGS_SEARCH_EMPTY))],
                self.fields(),
            )
            if self.query
            else self._unfiltered
        )

    def grouped_children(self, fields):
        groups = getattr(self, self.GROUPS, self.NO_GROUPS)
        if groups is self.NO_GROUPS:
            return [field.container for field in fields]
        selected = set(fields)
        children = []
        for title, members in groups:
            matching = [field for field in members if field in selected]
            if matching:
                children.extend(
                    [section(title), *(field.container for field in matching)]
                )
        return children

    def actions(self):
        self.save = SaveButton(
            lambda: self.screen.t(MessageKey.SETTINGS_SAVE),
            lambda: any(page.dirty for page in self.screen.drafts.active_pages()),
            self.screen.save,
        )
        self.reload = Button(
            self.screen.t(MessageKey.SETTINGS_RELOAD),
            handler=lambda: self.screen.reload(self),
            width=self.ACTION_WIDTH,
        )
        self.cancel = Button(
            self.screen.t(MessageKey.SETTINGS_DISCARD),
            handler=self.screen.discard,
            width=self.ACTION_WIDTH,
        )
        self.buttons = [self.save, self.reload, self.cancel]
        self.bottom = ActionBar(
            self.buttons,
            lambda: max(
                self.MINIMUM_ACTION_WIDTH, self.screen.main_width() - self.ACTION_INSET
            ),
        )
        for field in self.all_fields():
            field.enabled = lambda: not self.screen.busy
            field.on_edit = self.screen.edit_field

    def all_fields(self):
        return [field for form in self.forms() for field in form.fields.values()]

    def fields(self):
        # Keep the displayed rows and keyboard navigation identical while a
        # matching value is edited. Re-evaluate matches on the next search.
        return self._filtered_fields if self.query else self.section_fields()

    def visible_forms(self):
        return self.forms()

    def section_fields(self):
        tab = self.screen.tabs.selected
        return [
            field
            for form in self.visible_forms()
            for field in form.fields.values()
            if getattr(self, self.FIELD_TABS, {}).get(field, SettingsTab.CONFIG.value)
            == tab
        ]

    def _matching_fields(self):
        visible = set(self.section_fields())
        return [
            field
            for form in self.visible_forms()
            for path, field in form.fields.items()
            if field in visible
            if not self.query
            or self.query
            in (
                SettingMetadata.PATH_SEPARATOR.join(path)
                + self.SPACE
                + str(field.schema.get(SchemaKeyword.TITLE, self.EMPTY_LABEL))
                + self.SPACE
                + str(field.schema.get(SchemaKeyword.DESCRIPTION, self.EMPTY_LABEL))
                + self.SPACE
                + field.input.text
            ).casefold()
        ]

    def body_widgets(self):
        return [field.input for field in self.fields()]

    @property
    def dirty(self):
        return getattr(self, self.JSON_CHANGED, self.NO_JSON_CHANGE) or any(
            field.input.text != field.initial for field in self.all_fields()
        )


class ProjectPage(Page):
    SUGGEST_DEFAULTS = True
    NO_SUGGESTIONS = False
    NO_EFFECTIVE = None
    EXPLICIT_NULL = None
    SECTION = "section"
    GENERAL_GROUP = "general"
    WORD_SEPARATOR = "_"
    DEPENDENCY_SEPARATOR = ", "

    def __init__(self, screen, record, *, suggest_defaults=SUGGEST_DEFAULTS):
        self.screen, self.record = screen, record
        view = ProjectConfiguration.view(record)
        self.identifier = view.project_id
        self.components = list(view.values.components)
        values = view.edit_values()
        schema = view.schema
        forms = ProjectFormSchemas.read(schema)
        self.component_schemas = forms.components
        engine_schemas = forms.engines
        engine_catalog = ProjectSchemaDescription.engine_catalog(schema)
        from ...backend.project.naming import TITLE_ENGINE

        keys = {
            name: EngineConfigurationKey.from_dict(spec).configuration_key
            for name, spec in engine_catalog.items()
        }
        self.engine_keys = {
            name: key
            for name, key in keys.items()
            if name != TITLE_ENGINE and key != TITLE_ENGINE
        }
        self.form = SchemaForm(forms.general, values, screen.t)
        self.component_forms, self.engine_forms = {}, {}
        for key in dict.fromkeys(self.engine_keys.values()):
            effective = {
                name: view
                for name, view in view.effective_engines.items()
                if self.engine_keys.get(name) == key
            }
            form = SchemaForm(
                engine_schemas.get(key, {}),
                values,
                screen.t,
                prefix=ProjectConfiguration.engine_form_path(key),
                effective=effective,
            )
            self.engine_forms[key] = form
        for name in self.components:
            effective = view.component_effective(name)
            form = SchemaForm(
                self.component_schemas.get(name, SchemaBoolean.REJECT),
                values,
                screen.t,
                prefix=ProjectConfiguration.component_form_path(name),
                effective=(
                    {name: effective}
                    if effective is not self.NO_EFFECTIVE
                    else self.NO_EFFECTIVE
                ),
            )
            self.component_forms[name] = form
        # Older projects receive editable suggestions, persisted only on Save.
        # Host-fixed fields and explicit nulls retain their ownership/meaning.
        if suggest_defaults and RAGIdentity.CAPABILITY in self.component_forms:
            import json
            from ...backend.project.defaults import rag_defaults
            from ...widget.forms.settings import MISSING, at_path

            defaults = rag_defaults()
            for path, field in self.component_forms[
                RAGIdentity.CAPABILITY
            ].fields.items():
                default = at_path(
                    defaults,
                    path[
                        len(
                            ProjectConfiguration.component_form_path(
                                RAGIdentity.CAPABILITY
                            )
                        ) :
                    ],
                )
                explicit_null = any(
                    at_path(values, path[:i]) is self.EXPLICIT_NULL
                    for i in range(1, len(path) + 1)
                )
                if (
                    field.original is MISSING
                    and not field.readonly
                    and default is not MISSING
                    and not explicit_null
                ):
                    field.input.text = (
                        default if isinstance(default, str) else json.dumps(default)
                    )
        self.actions()
        self.section = SettingsSection.GENERAL
        self.set_section(self.section)

    def visible_forms(self):
        section = getattr(self, self.SECTION, SettingsSection.GENERAL)
        if SettingsSection.is_engine(section):
            return [self.engine_forms[SettingsSection.name(section)]]
        if SettingsSection.is_component(section):
            return [self.component_forms[SettingsSection.name(section)]]
        return [self.form]

    def section_choices(self):
        return SettingsSection.choices(self.engine_forms, self.component_forms)

    def set_section(self, section):
        self.section = section
        form = self.visible_forms()[0]
        groups = {}
        self.field_tabs = {}
        for path, field in form.fields.items():
            relative = path[len(form.prefix) :]
            if (
                not form.prefix
                and relative[: len(SchemaForm.PROJECT_POLICY_PATH)]
                == SchemaForm.PROJECT_POLICY_PATH
            ):
                tab, relative = (
                    SettingsTab.POLICY.value,
                    relative[len(SchemaForm.PROJECT_POLICY_PATH) :],
                )
            elif (
                form.prefix
                and relative[: len(SchemaForm.POLICY_PATH)] == SchemaForm.POLICY_PATH
            ):
                tab, relative = (
                    SettingsTab.POLICY.value,
                    relative[len(SchemaForm.POLICY_PATH) :],
                )
            else:
                tab = SettingsTab.CONFIG.value
                if relative[: len(SchemaForm.CONFIG_PATH)] == SchemaForm.CONFIG_PATH:
                    relative = relative[len(SchemaForm.CONFIG_PATH) :]
            self.field_tabs[field] = tab
            if len(relative) > 1:
                category, label = relative[0], SettingMetadata.PATH_SEPARATOR.join(
                    relative[1:]
                )
            else:
                category, label = (
                    self.GENERAL_GROUP,
                    SettingMetadata.PATH_SEPARATOR.join(relative),
                )
            field.label = field.schema.get(SchemaKeyword.TITLE) or label
            groups.setdefault(category, []).append(field)

        def title(category):
            key = MessagePattern.settings_group(category)
            value = self.screen.t(key)
            return (
                category.replace(self.WORD_SEPARATOR, self.SPACE)
                if value == key
                else value
            )

        self.groups = [(title(category), fields) for category, fields in groups.items()]
        body = []
        if SettingsSection.is_component(section):
            name = SettingsSection.name(section)
            required = ProjectSchemaDescription.component_requirements(
                ProjectConfiguration.view(self.record).schema, name
            )
            missing = [item for item in required if item not in self.components]
            if required:
                body.append(
                    Label(
                        self.screen.t(
                            MessageKey.SETTINGS_DEPENDENCIES,
                            names=self.DEPENDENCY_SEPARATOR.join(required),
                        )
                    )
                )
            if missing:
                body.append(
                    Label(
                        self.screen.t(
                            MessageKey.SETTINGS_DEPENDENCIES_MISSING,
                            names=self.DEPENDENCY_SEPARATOR.join(missing),
                        )
                    )
                )
        self._unfiltered = SettingsBody(
            [*body, *self.grouped_children(self.section_fields())],
            self.section_fields(),
        )
        self.container = self._unfiltered
        self.filter(self.screen.query)

    def forms(self):
        return [self.form, *self.engine_forms.values(), *self.component_forms.values()]

    def values(self):
        values = self.form.values()
        for form in [*self.component_forms.values(), *self.engine_forms.values()]:
            form.apply(values)
        ProjectConfigForm.store_components(values, self.components)
        return values

    def save_request(self):
        values = self.values()
        view = ProjectConfiguration.view(self.record)
        return SettingsOperation.SAVE_PROJECT, (
            self.identifier,
            values,
            view.config_version,
            view.values.components,
        )

    def accept_saved(self, record):
        self.screen.saved_project(record)


class GlobalPage(Page):
    USAGE_OUTPUT = "usage_output"
    NO_USAGE_OUTPUT = None
    NO_GENERAL_FORM = None
    NO_RESULT = None
    NO_EVENT = None
    ALL_TIME = None
    USAGE_READ_ONLY = True
    USAGE_SCROLLBAR = True
    USAGE_WRAP = True
    USAGE_HEIGHT = 6
    NEWLINE = "\n"
    FOLLOW_TAIL_OFF = False

    @classmethod
    def usage_output_for(cls, page):
        return getattr(page, cls.USAGE_OUTPUT, cls.NO_USAGE_OUTPUT)

    def __init__(self, screen, name, values):
        self.screen, self.name = screen, name
        values = GlobalSettingsSchema.initial_values(name, values)
        self.original = deepcopy(values)
        schema = GlobalSettingsSchema.describe(
            name, screen.t, lambda: screen.view.profile_bar.minimum_width
        )
        self.form = SchemaForm(schema, values, screen.t)
        self.general_form = self.NO_GENERAL_FORM
        if name == PreferenceSection.GENERAL:
            from ...widget.forms.general_settings import GeneralForm

            self.general_form = GeneralForm(screen, values)
            self.form = self.general_form.form
        self.container = HSplit(
            [section(screen.t(MessagePattern.settings(name))), self.form.container]
        )
        if name == PreferenceSection.APPEARANCE:
            self.container = SettingsBody(
                [section(screen.t(MessagePattern.settings(name))), *self.form.children],
                self.all_fields(),
            )
        if self.general_form is not self.NO_GENERAL_FORM:
            self.container = self.general_form.container
            self.groups = [
                (
                    screen.t(MessagePattern.general_category(category)),
                    [self.form.fields[(key,)] for key in keys],
                )
                for category, keys in self.general_form.groups.items()
            ]
        else:
            self.groups = [(screen.t(MessagePattern.settings(name)), self.all_fields())]
        self.usage_output = self.NO_USAGE_OUTPUT
        if name == PreferenceSection.PROFILE:
            from ...widget.usage_chart import DailyUsageChart

            self.usage_output = TextArea(
                text=self.usage_text(),
                read_only=self.USAGE_READ_ONLY,
                scrollbar=self.USAGE_SCROLLBAR,
                wrap_lines=self.USAGE_WRAP,
                height=self.USAGE_HEIGHT,
                style=ThemeSelector.MUTED.style,
            )
            self.usage_chart = DailyUsageChart(screen.usage, screen.t)
            self.container = SettingsBody(
                [
                    section(screen.t(MessageKey.SETTINGS_PROFILE)),
                    *self.form.children,
                    section(screen.t(MessageKey.SETTINGS_USAGE)),
                    self.usage_output,
                    self.usage_chart,
                ],
                self.all_fields(),
            )
        self.actions()
        if name == PreferenceSection.APPEARANCE:
            self.form.fields[
                (HubTheme.sidebar_width.__name__,)
            ].input.buffer.on_text_changed += self.preview_width

    def usage_text(self):
        rows = [self.screen.t(MessageKey.SETTINGS_USAGE_SCOPE)]
        if not self.screen.usage:
            rows.append(self.screen.t(MessageKey.SETTINGS_USAGE_EMPTY))
        for record in self.screen.usage:
            observation = ProjectUsageObservation(record)
            if observation.has_error:
                from ..error_presentation import error_text

                rows.append(
                    self.screen.t(
                        MessageKey.SETTINGS_USAGE_ERROR,
                        title=observation.title,
                        error=error_text(observation.error),
                    )
                )
                continue
            period = (
                self.screen.t(MessageKey.SETTINGS_USAGE_ALL)
                if observation.period_seconds is self.ALL_TIME
                else self.screen.t(
                    MessageKey.SETTINGS_USAGE_PERIOD, seconds=observation.period_seconds
                )
            )
            rows.append(
                self.screen.t(MessageKey.SETTINGS_USAGE_ROW, **record, period=period)
            )
        return self.NEWLINE.join(rows)

    def preview_width(self, _=NO_EVENT):
        try:
            width = self.form.fields[(HubTheme.sidebar_width.__name__,)].value()
            self.screen.view.set_theme(
                replace(self.screen.view.theme, sidebar_width=width)
            )
        except (TypeError, ValueError):
            pass

    def forms(self):
        return [self.form]

    @property
    def dirty(self):
        return super().dirty or (
            self.general_form is not self.NO_GENERAL_FORM
            and not GeneralSettingsDraft.same_language_packs(
                self.form.original, self.original
            )
        )

    def body_widgets(self):
        if self.query or self.screen.tabs.selected == SettingsTab.POLICY.value:
            return super().body_widgets()
        if self.general_form is not self.NO_GENERAL_FORM:
            return self.general_form.widgets
        return [
            *super().body_widgets(),
            *(
                [self.usage_output]
                if self.usage_output is not self.NO_USAGE_OUTPUT
                else []
            ),
        ]

    def save_request(self):
        values = self.form.values()
        PreferencesStore.input_record(self.name, values)
        return SettingsOperation.SAVE_GLOBAL, (self.name, values)

    def accept_saved(self, result):
        if result is self.NO_RESULT:
            return
        self.screen.drafts.preferences[self.name] = result
        page = self.screen.drafts.pages[self.name] = GlobalPage(
            self.screen, self.name, result
        )
        self.screen.show_page(page)
        self.screen.status = self.screen.t(MessageKey.SETTINGS_SAVED)
        if self.name == PreferenceSection.APPEARANCE:
            self.screen.drafts.saved_sidebar_width = SidebarWidth.observe(result)
            self.screen.view.set_theme(HubTheme(**result))
        elif self.name == PreferenceSection.GENERAL:
            self.screen.view.general = GeneralSettings(**result)
            if not self.screen.view.general.auto_scroll:
                self.screen.view.output.set_follow_tail(self.FOLLOW_TAIL_OFF)
        elif self.name == PreferenceSection.PROFILE:
            self.screen.view.user_profile = UserProfile(**result)
