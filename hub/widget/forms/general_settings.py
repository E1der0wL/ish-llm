"""Categorized general preferences and draft JSON language-pack management."""

from hub.locales.codes import LanguageCode

from hub.locales.keys import MessageKey, MessagePattern

from dataclasses import dataclass
from typing import ClassVar

from llm.core.schema import SchemaKeyword, SchemaType
from ...config.general import GeneralSettings, GeneralSettingsDraft
from ...config.theme_style import ThemeSelector
import json

from prompt_toolkit.layout import HSplit, VSplit, Window
from prompt_toolkit.widgets import Label

from ..section import section
from ...locales import validate_packs
from .settings import SchemaForm
from ..controls import Button, RadioList, TextArea
from ..section import Heading


@dataclass(slots=True)
class PackInput:
    MISSING: ClassVar[object] = object()

    code: object = MISSING
    messages: object = MISSING

    def require(self):
        # Assignment reads the message payload before its destination key.
        for name in (type(self).messages.__name__, type(self).code.__name__):
            if getattr(self, name) is self.MISSING:
                raise KeyError(name)
        return self

    def to_pack(self):
        self.require()
        return {self.code: self.messages}


class GeneralForm:
    DEBUG_CATEGORIES = ("diagnostics", "recovery")
    LANGUAGE_CATEGORY = "language"
    BUTTON_WIDTH = 18
    SINGLE_LINE = 1
    PACK_EDITOR_HEIGHT = 8
    GROUP_PADDING = 1
    PACK_SEPARATOR = ", "
    PACK_EXAMPLE = '{"settings_title": "Settings"}'
    SINGLE_LINE_EDIT = False
    MULTILINE_EDIT = True
    SELECT_ON_FOCUS = True
    VALID_INPUT = True
    INVALID_INPUT = False

    GROUPS = {
        "storage": (GeneralSettings.workspace_root.__name__,),
        LANGUAGE_CATEGORY: (GeneralSettings.language.__name__,),
        "notifications": (
            GeneralSettings.notification_kinds.__name__,
            GeneralSettings.notification_seconds.__name__,
        ),
        "startup": (
            GeneralSettings.restore_last_project.__name__,
            GeneralSettings.restore_last_session.__name__,
        ),
        "output": (GeneralSettings.auto_scroll.__name__,),
        "editor": (GeneralSettings.editor.__name__,),
        "diagnostics": (
            GeneralSettings.provider_request_summary.__name__,
            GeneralSettings.provider_max_detail_chars.__name__,
        ),
        "recovery": (
            GeneralSettings.recovery_enabled.__name__,
            GeneralSettings.recovery_model_analysis.__name__,
            GeneralSettings.recovery_timeout_seconds.__name__,
            GeneralSettings.recovery_input_chars.__name__,
            GeneralSettings.recovery_summary_chars.__name__,
            GeneralSettings.recovery_max_summary_calls.__name__,
        ),
    }

    def __init__(self, screen, values):
        self.screen = screen
        self.groups = dict(self.GROUPS)
        owned = GeneralSettings.schema_properties()
        properties = {
            key: {
                **owned[key],
                SchemaKeyword.DESCRIPTION: screen.t(MessagePattern.general(key)),
            }
            for keys in self.groups.values()
            for key in keys
        }
        self.form = SchemaForm(
            {
                SchemaKeyword.TYPE: SchemaType.OBJECT,
                SchemaKeyword.PROPERTIES: properties,
            },
            values,
            screen.t,
        )
        GeneralSettingsDraft(self.form.original).initialize_packs()
        self.add_button = Button(
            screen.t(MessageKey.LANGUAGE_PACK_ADD),
            handler=self.add_pack,
            width=self.BUTTON_WIDTH,
        )
        self.delete_button = Button(
            screen.t(MessageKey.LANGUAGE_PACK_DELETE),
            handler=self.delete_pack,
            width=self.BUTTON_WIDTH,
        )
        self.set_debugging(False)

    def set_debugging(self, debugging):
        self.groups = {category: keys for category, keys in self.GROUPS.items()
                       if (category in self.DEBUG_CATEGORIES) == debugging}
        children, self.widgets = [], []
        screen = self.screen
        for category, keys in self.groups.items():
            children.append(
                section(screen.t(MessagePattern.general_category(category)))
            )
            for key in keys:
                field = self.form.fields[(key,)]
                children.append(field.container)
                self.widgets.append(field.input)
            if category == self.LANGUAGE_CATEGORY:
                children.extend(
                    [
                        Label(self.pack_names, style=ThemeSelector.MUTED.style),
                        VSplit(
                            [self.add_button, self.delete_button],
                            padding=self.GROUP_PADDING,
                        ),
                        Window(height=self.SINGLE_LINE),
                    ]
                )
                self.widgets.extend([self.add_button, self.delete_button])
        self.container = HSplit(children)

    def pack_names(self):
        names = self.PACK_SEPARATOR.join(
            [
                *LanguageCode.BUILTIN,
                *GeneralSettingsDraft(self.form.original).pack_names(),
            ]
        )
        return self.screen.t(MessageKey.LANGUAGE_PACK_NAMES, names=names)

    def add_pack(self):
        if self.screen.busy:
            return
        code = TextArea(height=self.SINGLE_LINE, multiline=self.SINGLE_LINE_EDIT)
        content = TextArea(
            text=self.PACK_EXAMPLE,
            height=self.PACK_EDITOR_HEIGHT,
            multiline=self.MULTILINE_EDIT,
        )
        draft = PackInput()

        def validate():
            try:
                draft.code, draft.messages = code.text.strip(), json.loads(content.text)
                validate_packs(draft.to_pack())
                return self.VALID_INPUT
            except (TypeError, ValueError) as error:
                self.screen.error(error)
                return self.INVALID_INPUT

        def apply():
            draft.require()
            GeneralSettingsDraft(self.form.original).set_pack(
                draft.code, draft.messages
            )
            self.screen.status = self.screen.t(MessageKey.LANGUAGE_PACK_DRAFT)

        self.screen.view.open_dialog(
            self.screen.t(MessageKey.LANGUAGE_PACK_ADD),
            HSplit(
                [
                    Heading(self.screen.t(MessageKey.LANGUAGE_PACK_CODE)),
                    code,
                    Heading(self.screen.t(MessageKey.LANGUAGE_PACK_JSON)),
                    content,
                ]
            ),
            apply,
            code,
            validate=validate,
        )

    def delete_pack(self):
        if self.screen.busy:
            return
        packs = GeneralSettingsDraft(self.form.original).packs()
        if not packs:
            self.screen.status = self.screen.t(MessageKey.LANGUAGE_PACK_NONE)
            return
        choices = RadioList(
            [(code, code) for code in packs.names()],
            select_on_focus=self.SELECT_ON_FOCUS,
        )

        def apply():
            code = choices.current_value
            packs.remove(code)
            language = self.form.fields[(GeneralSettings.language.__name__,)]
            if language.value() == code:
                language.input.text = LanguageCode.ENGLISH
            self.screen.status = self.screen.t(MessageKey.LANGUAGE_PACK_DRAFT)

        self.screen.view.open_dialog(
            self.screen.t(MessageKey.LANGUAGE_PACK_DELETE), choices, apply, choices
        )
