"""Draft settings pages with field and action regions."""

from copy import deepcopy
from dataclasses import replace

from prompt_toolkit.layout import HSplit
from prompt_toolkit.widgets import Label

from ...widget.section import section
from ...widget.buttons import SaveButton
from ...widget.actions import ActionBar
from ...config.profile import UserProfile
from ...config.general import GeneralSettings
from ...widget.settings import SchemaForm
from ...widget.settings_viewport import SettingsBody
from ...config.theme import HubTheme
from ...widget.controls import Button, TextArea


class Page:
    query = ""

    def filter(self, query):
        if not hasattr(self, "_unfiltered"):
            self._unfiltered = self.container
        self.query = query.strip().casefold()
        self._filtered_fields = self._matching_fields()
        self.container = (SettingsBody([field.container for field in self.fields()] or
                          [Label(self.screen.t("settings_search_empty"))], self.fields())
                          if self.query else self._unfiltered)

    def actions(self, extra=()):
        self.save = SaveButton(lambda: self.screen.t("settings_save"), lambda: self.dirty, self.submit)
        self.reload = Button(self.screen.t("settings_reload"), handler=lambda: self.screen.reload(self), width=10)
        self.cancel = Button(self.screen.t("cancel"), handler=lambda: self.screen.cancel(self), width=10)
        self.buttons = [self.save, self.reload, self.cancel, *extra]
        self.bottom = ActionBar(self.buttons, lambda: max(1, self.screen.main_width() - 2))
        for field in self.all_fields():
            field.enabled = lambda: not self.screen.busy
            field.on_edit = self.screen.edit_field

    def button_rows(self):
        return self.bottom.rows()

    def all_fields(self):
        return [field for form in self.forms() for field in form.fields.values()]

    def fields(self):
        # Keep the displayed rows and keyboard navigation identical while a
        # matching value is edited. Re-evaluate matches on the next search.
        return self._filtered_fields if self.query else self.all_fields()

    def _matching_fields(self):
        return [field for form in self.forms() for path, field in form.fields.items()
                if not self.query or self.query in (".".join(path) + " " +
                    str(field.schema.get("title", "")) + " " + str(field.schema.get("description", "")) +
                    " " + field.input.text).casefold()]

    def body_widgets(self):
        return [field.input for field in self.fields()]

    def focusables(self):
        return [*self.body_widgets(), *self.buttons]

    @property
    def dirty(self):
        return any(field.input.text != field.initial for field in self.all_fields())


class ProjectPage(Page):
    def __init__(self, screen, record, *, new=False):
        self.screen, self.record, self.new = screen, record, new
        self.identifier = None if new else record["project"]["id"]
        self.components = list(record["schema"]["properties"]["components"]["items"].get("enum", [])) if new else list(record["values"]["components"])
        values = deepcopy(record["values"])
        values["config"] = deepcopy(record.get("project", {}).get("config", values["config"]))
        schema = deepcopy(record["schema"])
        config = schema["properties"]["config"]["properties"]
        parameters = config.pop("parameters")["properties"]
        self.component_schemas = parameters.pop("components")["properties"]
        engine_schemas = parameters.pop("engines")["properties"]
        engine_catalog = schema.get("x-engines", {})
        self.engine_keys = {name: spec["configuration_key"] for name, spec in engine_catalog.items()}
        schema["properties"].pop("components")
        self.form = SchemaForm(schema, values, screen.t)
        self.component_forms, self.engine_forms = {}, {}
        self.activate_button = Button(screen.t("settings_open_project"), handler=lambda: screen.activate(self), width=10)
        body = list(self.form.children)
        for key in dict.fromkeys(self.engine_keys.values()):
            effective = {name: view for name, view in record.get("effective_engines", {}).items()
                         if self.engine_keys.get(name) == key}
            form = SchemaForm(engine_schemas.get(key, {}), values, screen.t,
                              prefix=("config", "parameters", "engines", key), effective=effective)
            self.engine_forms[key] = form
            body.extend([section(key), *form.children])
        for name in self.components:
            view = record.get("components", {}).get(name, {}).get("effective")
            form = SchemaForm(self.component_schemas.get(name, False), values, screen.t,
                              prefix=("config", "parameters", "components", name),
                              effective={name: view} if view is not None else None)
            self.component_forms[name] = form
            required = record["schema"].get("x-components", {}).get(name, {}).get("required_components", [])
            missing = [item for item in required if item not in self.components]
            body.extend([section(name)])
            if required:
                body.append(Label(screen.t("settings_dependencies", names=", ".join(required))))
            if missing:
                body.append(Label(screen.t("settings_dependencies_missing", names=", ".join(missing))))
            body.extend(form.children or [form.container])
        self.container = SettingsBody(body, self.all_fields())
        self.actions(extra=() if new else (self.activate_button,))

    def forms(self):
        return [self.form, *self.engine_forms.values(), *self.component_forms.values()]

    @property
    def dirty(self):
        return self.new or super().dirty

    def values(self):
        values = self.form.values()
        for form in [*self.component_forms.values(), *self.engine_forms.values()]:
            form.apply(values)
        values["components"] = self.components[:]
        return values

    def save_request(self):
        values = self.values()
        operation = "create_project" if self.new else "save_project"
        args = (values,) if self.new else (self.identifier, values, self.record["config_version"],
                                         self.record["values"]["components"])
        return operation, args

    def accept_saved(self, record):
        if record is not None and self.new:
            self.screen.pages.pop("new", None)
        self.screen.saved_project(record)

    def submit(self, *, on_saved=None):
        if self.screen.busy:
            return
        self.screen.finish_edit()
        try:
            operation, args = self.save_request()
        except (ValueError, TypeError) as error:
            self.screen.error(error)
            return
        def saved(record):
            self.accept_saved(record)
            if record is not None and on_saved:
                on_saved()
        self.screen.call(operation, *args, completed=saved)


class GlobalPage(Page):
    def __init__(self, screen, name, values):
        self.screen, self.name = screen, name
        if name == "appearance":
            values = {"icon_style": "nerd", "output_refresh_interval": 0.1,
                      "progress_refresh_interval": 0.1, **values}
        self.original = deepcopy(values)
        descriptions = ({"display_name": screen.t("settings_profile_description"),
                         "email": screen.t("settings_email_description"),
                         "phone": screen.t("settings_phone_description")} if name == "profile" else
                        {"editor": screen.t("settings_editor_description")} if name == "general" else
                        {key: screen.t("settings_color_description") for key in
                         ("background", "foreground", "accent1", "accent2", "accent3", "comment")})
        properties = {key: {"type": "string", "description": description} for key, description in descriptions.items()}
        if name == "appearance":
            properties["icon_style"] = {"type": "string", "enum": ["nerd", "unicode"],
                "title": screen.t("settings_icon_style"), "description": screen.t("settings_icon_description"),
                "x-hub-choices": {"nerd": "Nerd Font", "unicode": screen.t("settings_icon_unicode")}}
            for key in ("background", "foreground", "accent1", "accent2", "accent3", "comment"):
                properties[key]["title"] = screen.t("settings_theme_" + key)
            properties["sidebar_width"] = {"type": "integer", "minimum": 18, "maximum": 60,
                "title": screen.t("settings_sidebar_width_title"), "description": screen.t("settings_sidebar_width")}
            for key in ("output_refresh_interval", "progress_refresh_interval"):
                properties[key] = {"type": "number", "minimum": 0.01, "maximum": 10,
                    "title": screen.t("settings_" + key), "description": screen.t("settings_" + key + "_description")}
        self.form = SchemaForm({"type": "object", "properties": properties}, values, screen.t)
        self.general_form = None
        if name == "general":
            from ...widget.general_settings import GeneralForm
            self.general_form = GeneralForm(screen, values)
            self.form = self.general_form.form
        self.container = HSplit([section(screen.t("settings_" + name)), self.form.container])
        if name == "appearance":
            self.container = SettingsBody([section(screen.t("settings_" + name)), *self.form.children], self.all_fields())
        if self.general_form is not None:
            self.container = self.general_form.container
        self.usage_output = None
        if name == "profile":
            self.usage_output = TextArea(text=self.usage_text(), read_only=True, scrollbar=True,
                                         wrap_lines=True, height=6, style="class:hub.muted")
            self.container = HSplit([self.container, section(screen.t("settings_usage")), self.usage_output])
        self.actions()
        if name == "appearance":
            self.form.fields[("sidebar_width",)].input.buffer.on_text_changed += self.preview_width

    def usage_text(self):
        rows = [self.screen.t("settings_usage_scope")]
        if not self.screen.usage:
            rows.append(self.screen.t("settings_usage_empty"))
        for record in self.screen.usage:
            if "error" in record:
                rows.append(self.screen.t("settings_usage_error", title=record["title"], error=record["error"]))
                continue
            period = (self.screen.t("settings_usage_all") if record["period_seconds"] is None else
                      self.screen.t("settings_usage_period", seconds=record["period_seconds"]))
            rows.append(self.screen.t("settings_usage_row", **record, period=period))
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
        if self.query:
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
        self.screen.preferences[self.name] = result
        page = self.screen.pages[self.name] = GlobalPage(self.screen, self.name, result)
        self.screen.show_page(page)
        self.screen.status = self.screen.t("settings_saved")
        if self.name == "appearance":
            self.screen.view.set_theme(HubTheme(**result))
        elif self.name == "general":
            self.screen.view.general = GeneralSettings(**result)
            if not self.screen.view.general.auto_scroll:
                self.screen.view.transcript.control.follow_tail = False

    def submit(self, *, on_saved=None):
        if self.screen.busy:
            return
        self.screen.finish_edit()
        try:
            operation, args = self.save_request()
        except (TypeError, ValueError) as error:
            self.screen.error(error)
            return
        def saved(result):
            if result is not None:
                self.accept_saved(result)
                if on_saved:
                    on_saved()
        self.screen.call(operation, *args, completed=saved)
