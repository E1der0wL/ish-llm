"""Draft settings pages with field and action regions."""

from hub.locales.keys import MessageKey, MessagePattern

from llm.core.configuration_views import EngineConfigurationKey

from llm.services.schema import ProjectFormSchemas, ProjectConfiguration

from copy import deepcopy
from dataclasses import replace
from enum import StrEnum

from prompt_toolkit.layout import HSplit
from prompt_toolkit.widgets import Label

from ...widget.section import section
from ...widget.buttons import SaveButton
from ...widget.actions import ActionBar
from ...config.profile import UserProfile
from ...config.general import GeneralSettings
from ...widget.forms.settings import SchemaForm
from ...widget.forms.settings_viewport import SettingsBody
from ...config.theme import HubTheme
from ...widget.controls import Button, TextArea


class SettingsTab(StrEnum):
    CONFIG = "config"
    POLICY = "policy"


class Page:
    query = ""

    def filter(self, query):
        if not hasattr(self, "_unfiltered"):
            self._unfiltered = self.container
        self.query = query.strip().casefold()
        self._filtered_fields = self._matching_fields()
        if not self.section_fields():
            self.container = SettingsBody([Label(self.screen.t(MessageKey.SETTINGS_TAB_EMPTY))], [])
            return
        self.container = (SettingsBody(self.grouped_children(self.fields()) or
                          [Label(self.screen.t(MessageKey.SETTINGS_SEARCH_EMPTY))], self.fields())
                          if self.query else self._unfiltered)

    def grouped_children(self, fields):
        groups = getattr(self, "groups", None)
        if groups is None:
            return [field.container for field in fields]
        selected = set(fields)
        children = []
        for title, members in groups:
            matching = [field for field in members if field in selected]
            if matching:
                children.extend([section(title), *(field.container for field in matching)])
        return children

    def actions(self):
        self.save = SaveButton(lambda: self.screen.t(MessageKey.SETTINGS_SAVE),
                               lambda: any(page.dirty for page in self.screen.drafts.active_pages()), self.screen.save)
        self.reload = Button(self.screen.t(MessageKey.SETTINGS_RELOAD), handler=lambda: self.screen.reload(self), width=10)
        self.cancel = Button(self.screen.t(MessageKey.SETTINGS_DISCARD), handler=self.screen.discard, width=10)
        self.buttons = [self.save, self.reload, self.cancel]
        self.bottom = ActionBar(self.buttons, lambda: max(1, self.screen.main_width() - 2))
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
        return [field for form in self.visible_forms() for field in form.fields.values()
                if getattr(self, "field_tabs", {}).get(field, SettingsTab.CONFIG.value) == tab]

    def _matching_fields(self):
        visible = set(self.section_fields())
        return [field for form in self.visible_forms() for path, field in form.fields.items() if field in visible
                if not self.query or self.query in (".".join(path) + " " +
                    str(field.schema.get("title", "")) + " " + str(field.schema.get("description", "")) +
                    " " + field.input.text).casefold()]

    def body_widgets(self):
        return [field.input for field in self.fields()]

    @property
    def dirty(self):
        return getattr(self, "json_changed", False) or any(
            field.input.text != field.initial for field in self.all_fields())


class ProjectPage(Page):
    def __init__(self, screen, record, *, suggest_defaults=True):
        self.screen, self.record = screen, record
        view = ProjectConfiguration.view(record)
        self.identifier = view.project_id
        self.components = list(view.values.components)
        values = view.values.to_dict()
        values["config"] = view.stored_config
        schema = view.schema
        forms = ProjectFormSchemas.read(schema)
        self.component_schemas = forms.components
        engine_schemas = forms.engines
        engine_catalog = schema.get("x-engines", {})
        from ...backend.project.naming import TITLE_ENGINE
        keys = {name: EngineConfigurationKey.from_dict(spec).configuration_key for name, spec in engine_catalog.items()}
        self.engine_keys = {name: key for name, key in keys.items()
                            if name != TITLE_ENGINE and key != TITLE_ENGINE}
        self.form = SchemaForm(forms.general, values, screen.t)
        self.component_forms, self.engine_forms = {}, {}
        for key in dict.fromkeys(self.engine_keys.values()):
            effective = {name: view for name, view in view.effective_engines.items()
                         if self.engine_keys.get(name) == key}
            form = SchemaForm(engine_schemas.get(key, {}), values, screen.t,
                              prefix=ProjectConfiguration.engine_form_path(key), effective=effective)
            self.engine_forms[key] = form
        for name in self.components:
            effective = view.components.get(name, {}).get("effective")
            form = SchemaForm(self.component_schemas.get(name, False), values, screen.t,
                              prefix=ProjectConfiguration.component_form_path(name),
                              effective={name: effective} if effective is not None else None)
            self.component_forms[name] = form
        # Older projects receive editable suggestions, persisted only on Save.
        # Host-fixed fields and explicit nulls retain their ownership/meaning.
        if suggest_defaults and "rag" in self.component_forms:
            import json
            from ...backend.project.defaults import rag_defaults
            from ...widget.forms.settings import MISSING, at_path
            defaults = rag_defaults()
            for path, field in self.component_forms["rag"].fields.items():
                default = at_path(defaults, path[4:])
                explicit_null = any(at_path(values, path[:i]) is None for i in range(1, len(path) + 1))
                if field.original is MISSING and not field.readonly and default is not MISSING and not explicit_null:
                    field.input.text = default if isinstance(default, str) else json.dumps(default)
        self.actions()
        self.section = "project:general"
        self.set_section(self.section)

    def visible_forms(self):
        section = getattr(self, "section", "project:general")
        if section.startswith("engine:"):
            return [self.engine_forms[section.split(":", 1)[1]]]
        if section.startswith("component:"):
            return [self.component_forms[section.split(":", 1)[1]]]
        return [self.form]

    def set_section(self, section):
        self.section = section
        form = self.visible_forms()[0]
        groups = {}
        self.field_tabs = {}
        for path, field in form.fields.items():
            relative = path[len(form.prefix):]
            if not form.prefix and relative[:2] == SchemaForm.PROJECT_POLICY_PATH:
                tab, relative = SettingsTab.POLICY.value, relative[2:]
            elif form.prefix and relative[:1] == SchemaForm.POLICY_PATH:
                tab, relative = SettingsTab.POLICY.value, relative[1:]
            else:
                tab = SettingsTab.CONFIG.value
                if relative[:1] == SchemaForm.CONFIG_PATH:
                    relative = relative[1:]
            self.field_tabs[field] = tab
            if len(relative) > 1:
                category, label = relative[0], ".".join(relative[1:])
            else:
                category, label = "general", ".".join(relative)
            field.label = field.schema.get("title") or label
            groups.setdefault(category, []).append(field)
        def title(category):
            key = MessagePattern.settings_group(category)
            value = self.screen.t(key)
            return category.replace("_", " ") if value == key else value
        self.groups = [(title(category), fields) for category, fields in groups.items()]
        body = []
        if section.startswith("component:"):
            name = section.split(":", 1)[1]
            required = ProjectConfiguration.view(self.record).schema.get("x-components", {}).get(name, {}).get("required_components", [])
            missing = [item for item in required if item not in self.components]
            if required:
                body.append(Label(self.screen.t(MessageKey.SETTINGS_DEPENDENCIES, names=", ".join(required))))
            if missing:
                body.append(Label(self.screen.t(MessageKey.SETTINGS_DEPENDENCIES_MISSING, names=", ".join(missing))))
        self._unfiltered = SettingsBody([*body, *self.grouped_children(self.section_fields())], self.section_fields())
        self.container = self._unfiltered
        self.filter(self.screen.query)

    def forms(self):
        return [self.form, *self.engine_forms.values(), *self.component_forms.values()]

    def values(self):
        values = self.form.values()
        for form in [*self.component_forms.values(), *self.engine_forms.values()]:
            form.apply(values)
        values["components"] = self.components[:]
        return values

    def save_request(self):
        values = self.values()
        view = ProjectConfiguration.view(self.record)
        return "save_project", (self.identifier, values, view.config_version, view.values.components)

    def accept_saved(self, record):
        self.screen.saved_project(record)

class GlobalPage(Page):
    USAGE_OUTPUT = "usage_output"
    NO_USAGE_OUTPUT = None

    @classmethod
    def usage_output_for(cls, page):
        return getattr(page, cls.USAGE_OUTPUT, cls.NO_USAGE_OUTPUT)

    def __init__(self, screen, name, values):
        self.screen, self.name = screen, name
        if name == "appearance":
            values = {"icon_style": "nerd", "mode": "dark", "output_refresh_interval": 0.1,
                      "progress_refresh_interval": 0.1, **values}
        self.original = deepcopy(values)
        descriptions = ({"display_name": screen.t(MessageKey.SETTINGS_PROFILE_DESCRIPTION),
                         "email": screen.t(MessageKey.SETTINGS_EMAIL_DESCRIPTION),
                         "phone": screen.t(MessageKey.SETTINGS_PHONE_DESCRIPTION)} if name == "profile" else
                        {"editor": screen.t(MessageKey.SETTINGS_EDITOR_DESCRIPTION)} if name == "general" else
                        {key: screen.t(MessageKey.SETTINGS_COLOR_DESCRIPTION) for key in
                         ("background", "foreground", "accent1", "accent2", "accent3", "comment")})
        properties = {key: {"type": "string", "description": description} for key, description in descriptions.items()}
        from ...config.restart import setting_metadata
        for key, metadata in setting_metadata(name).items():
            if key in properties:
                properties[key].update(metadata)
        if name == "appearance":
            properties["mode"] = {"type": "string", "enum": ["dark", "light"],
                "title": screen.t(MessageKey.SETTINGS_THEME_MODE), "description": screen.t(MessageKey.SETTINGS_THEME_MODE_DESCRIPTION)}
            properties["icon_style"] = {"type": "string", "enum": ["nerd", "unicode"],
                "title": screen.t(MessageKey.SETTINGS_ICON_STYLE), "description": screen.t(MessageKey.SETTINGS_ICON_DESCRIPTION),
                "x-hub-choices": {"nerd": "Nerd Font", "unicode": screen.t(MessageKey.SETTINGS_ICON_UNICODE)}}
            for key in ("background", "foreground", "accent1", "accent2", "accent3", "comment"):
                properties[key]["title"] = screen.t(MessagePattern.settings_theme(key))
            properties["sidebar_width"] = {"type": "integer", "minimum": screen.view.profile_bar.minimum_width, "maximum": 60,
                "title": screen.t(MessageKey.SETTINGS_SIDEBAR_WIDTH_TITLE), "description": screen.t(MessageKey.SETTINGS_SIDEBAR_WIDTH)}
            for key in ("output_refresh_interval", "progress_refresh_interval"):
                properties[key] = {"type": "number", "minimum": 0.01, "maximum": 10,
                    "title": screen.t(MessagePattern.settings(key)), "description": screen.t(MessagePattern.settings_help(key))}
        self.form = SchemaForm({"type": "object", "properties": properties}, values, screen.t)
        self.general_form = None
        if name == "general":
            from ...widget.forms.general_settings import GeneralForm
            self.general_form = GeneralForm(screen, values)
            self.form = self.general_form.form
        self.container = HSplit([section(screen.t(MessagePattern.settings(name))), self.form.container])
        if name == "appearance":
            self.container = SettingsBody([section(screen.t(MessagePattern.settings(name))), *self.form.children], self.all_fields())
        if self.general_form is not None:
            self.container = self.general_form.container
            self.groups = [(screen.t(MessagePattern.general_category(category)), [self.form.fields[(key,)] for key in keys])
                           for category, keys in self.general_form.groups.items()]
        else:
            self.groups = [(screen.t(MessagePattern.settings(name)), self.all_fields())]
        self.usage_output = None
        if name == "profile":
            from ...widget.usage_chart import DailyUsageChart
            self.usage_output = TextArea(text=self.usage_text(), read_only=True, scrollbar=True,
                                         wrap_lines=True, height=6, style="class:hub.muted")
            self.usage_chart = DailyUsageChart(screen.usage, screen.t)
            self.container = SettingsBody([section(screen.t(MessageKey.SETTINGS_PROFILE)), *self.form.children,
                section(screen.t(MessageKey.SETTINGS_USAGE)), self.usage_output, self.usage_chart], self.all_fields())
        self.actions()
        if name == "appearance":
            self.form.fields[("sidebar_width",)].input.buffer.on_text_changed += self.preview_width

    def usage_text(self):
        rows = [self.screen.t(MessageKey.SETTINGS_USAGE_SCOPE)]
        if not self.screen.usage:
            rows.append(self.screen.t(MessageKey.SETTINGS_USAGE_EMPTY))
        for record in self.screen.usage:
            if "error" in record:
                from ..error_presentation import error_text
                rows.append(self.screen.t(MessageKey.SETTINGS_USAGE_ERROR, title=record["title"], error=error_text(record["error"])))
                continue
            period = (self.screen.t(MessageKey.SETTINGS_USAGE_ALL) if record["period_seconds"] is None else
                      self.screen.t(MessageKey.SETTINGS_USAGE_PERIOD, seconds=record["period_seconds"]))
            rows.append(self.screen.t(MessageKey.SETTINGS_USAGE_ROW, **record, period=period))
        return "\n".join(rows)

    def preview_width(self, _=None):
        try:
            width = self.form.fields[("sidebar_width",)].value()
            self.screen.view.set_theme(replace(self.screen.view.theme, sidebar_width=width))
        except (TypeError, ValueError):
            pass

    def forms(self):
        return [self.form]

    @property
    def dirty(self):
        return super().dirty or (self.general_form is not None and
            self.form.original.get("language_packs", {}) != self.original.get("language_packs", {}))

    def body_widgets(self):
        if self.query or self.screen.tabs.selected == SettingsTab.POLICY.value:
            return super().body_widgets()
        if self.general_form is not None:
            return self.general_form.widgets
        return [*super().body_widgets(), *([self.usage_output] if self.usage_output is not None else [])]

    def save_request(self):
        values = self.form.values()
        {"profile": UserProfile, "appearance": HubTheme, "general": GeneralSettings}[self.name](**values)
        return "save_global", (self.name, values)

    def accept_saved(self, result):
        if result is None:
            return
        self.screen.drafts.preferences[self.name] = result
        page = self.screen.drafts.pages[self.name] = GlobalPage(self.screen, self.name, result)
        self.screen.show_page(page)
        self.screen.status = self.screen.t(MessageKey.SETTINGS_SAVED)
        if self.name == "appearance":
            self.screen.drafts.saved_sidebar_width = result["sidebar_width"]
            self.screen.view.set_theme(HubTheme(**result))
        elif self.name == "general":
            self.screen.view.general = GeneralSettings(**result)
            if not self.screen.view.general.auto_scroll:
                self.screen.view.output.set_follow_tail(False)
        elif self.name == "profile":
            self.screen.view.user_profile = UserProfile(**result)
