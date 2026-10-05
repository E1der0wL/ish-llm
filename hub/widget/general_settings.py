"""Categorized general preferences and draft JSON language-pack management."""

from copy import deepcopy
import json

from prompt_toolkit.layout import HSplit, VSplit, Window
from prompt_toolkit.widgets import Label

from .section import section
from ..locales import validate_packs
from .settings import SchemaForm
from .controls import Button, RadioList, TextArea


class GeneralForm:
    def __init__(self, screen, values):
        self.screen = screen
        self.groups = {
            "language": ("language",),
            "notifications": ("notification_kinds", "notification_seconds"),
            "startup": ("restore_last_project", "restore_last_session"),
            "output": ("auto_scroll",),
            "editor": ("editor",),
        }
        kinds = {"notification_kinds": "array", "notification_seconds": "number",
                 "restore_last_project": "boolean", "restore_last_session": "boolean", "auto_scroll": "boolean"}
        properties = {key: {"type": kinds.get(key, "string"), "description": screen.t("general_" + key)}
                      for keys in self.groups.values() for key in keys}
        properties["notification_kinds"].update(items={"type": "string", "enum": ["error", "info", "warning", "success"]})
        properties["notification_seconds"].update(minimum=1, maximum=120)
        self.form = SchemaForm({"type": "object", "properties": properties}, values, screen.t)
        self.form.original.setdefault("language_packs", {})
        self.add_button = Button(screen.t("language_pack_add"), handler=self.add_pack, width=18)
        self.delete_button = Button(screen.t("language_pack_delete"), handler=self.delete_pack, width=18)
        children, self.widgets = [], []
        for category, keys in self.groups.items():
            children.append(section(screen.t("general_category_" + category)))
            for key in keys:
                field = self.form.fields[(key,)]
                children.append(field.container)
                self.widgets.append(field.input)
            if category == "language":
                children.extend([Label(self.pack_names, style="class:hub.muted"),
                                 VSplit([self.add_button, self.delete_button], padding=1), Window(height=1)])
                self.widgets.extend([self.add_button, self.delete_button])
        self.container = HSplit(children)

    def pack_names(self):
        names = ", ".join(["ko", "en", *self.form.original["language_packs"]])
        return self.screen.t("language_pack_names", names=names)

    def add_pack(self):
        if self.screen.busy:
            return
        code = TextArea(height=1, multiline=False)
        content = TextArea(text='{"settings_title": "Settings"}', height=8, multiline=True)
        draft = {}
        def validate():
            try:
                draft["code"], draft["messages"] = code.text.strip(), json.loads(content.text)
                validate_packs({draft["code"]: draft["messages"]})
                return True
            except (TypeError, ValueError) as error:
                self.screen.error(error)
                return False
        def apply():
            self.form.original["language_packs"][draft["code"]] = deepcopy(draft["messages"])
            self.screen.status = self.screen.t("language_pack_draft")
        self.screen.view.open_dialog(self.screen.t("language_pack_add"), HSplit([
            Label(self.screen.t("language_pack_code")), code,
            Label(self.screen.t("language_pack_json")), content]), apply, code, validate=validate)

    def delete_pack(self):
        if self.screen.busy:
            return
        packs = self.form.original["language_packs"]
        if not packs:
            self.screen.status = self.screen.t("language_pack_none")
            return
        choices = RadioList([(code, code) for code in packs], select_on_focus=True)
        def apply():
            code = choices.current_value
            packs.pop(code)
            language = self.form.fields[("language",)]
            if language.value() == code:
                language.input.text = "en"
            self.screen.status = self.screen.t("language_pack_draft")
        self.screen.view.open_dialog(self.screen.t("language_pack_delete"), choices, apply, choices)
