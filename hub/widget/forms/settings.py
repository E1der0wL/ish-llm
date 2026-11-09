"""Schema fields retain missing/null distinctions and unknown JSON properties."""

from hub.locales.keys import MessageKey

from llm.core.json_data import JsonPointer, JsonObject
from llm.core.schema import SchemaKeyword, SchemaType, SchemaBoolean, SchemaExtension
from llm.core.models import ProjectConfig
from llm.core.implementation_parameters import ImplementationParameters
from llm.services.configuration_records import ProjectConfigForm
from ...config.metadata import SettingMetadata
from ...config.theme_style import ThemeSelector
from ...ui.input.keys import KeyName, ShortcutAction, ShortcutLabel
from ...ui.input.json_document import JsonDocument
from llm.core.configuration_views import ConfigurationView


from copy import deepcopy
import json

from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import HSplit, VSplit
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.application.current import get_app
from prompt_toolkit.utils import Event
from ...ui.input.registry import ShortcutRegistry
from ..controls import TextArea
from ..mouse_contract import MouseBinding
from ..notices import Notice
from .field_layout import FieldLayout
from ..color_preview import ColorPreview
from ..dropdown import DropdownBox


MISSING = object()


class FieldDisplay:
    JSON_LABEL = "JSON"
    TYPE_SEPARATOR = " | "
    DESCRIPTION_SEPARATOR = " • "
    ENUM_SEPARATOR = ", "
    ARRAY_OPEN = "["
    ARRAY_CLOSE = "]"
    EMPTY = ""
    SPACE = " "
    NEWLINE = "\n"
    DESCRIPTION_PREFIX = "- "
    JSON_INDENT = JsonDocument.INDENT
    JSON_ASCII = JsonDocument.ENSURE_ASCII
    JSON_NULL = json.dumps(JsonObject.NULL)
    JSON_STRING_PREFIX = '"'
    PATH_ERROR = "{path}: {error}"
    MAX_EDITOR_LINES = 3
    MULTILINE = True
    WRAP_LINES = True
    FOCUS_ON_CLICK = True
    CONSTRAINTS = (
        SchemaKeyword.MINIMUM,
        SchemaKeyword.MAXIMUM,
        SchemaKeyword.EXCLUSIVE_MINIMUM,
        SchemaKeyword.MIN_LENGTH,
        SchemaKeyword.MAX_LENGTH,
        SchemaKeyword.PATTERN,
    )


def at_path(value, path):
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return MISSING
        value = value[key]
    return value


def put_path(value, path, item):
    current = value
    for key in path[:-1]:
        if key not in current:
            if item is MISSING:
                return
            current[key] = {}
        if not isinstance(current[key], dict):
            if item is MISSING:
                return
            current[key] = {}
        current = current[key]
    if item is MISSING:
        current.pop(path[-1], None)
    else:
        current[path[-1]] = item


def type_hint(schema):
    if not isinstance(schema, dict):
        return FieldDisplay.JSON_LABEL
    kind = schema.get(SchemaKeyword.TYPE, FieldDisplay.JSON_LABEL)
    text = FieldDisplay.TYPE_SEPARATOR.join(kind) if isinstance(kind, list) else kind
    if kind == SchemaType.ARRAY and isinstance(schema.get(SchemaKeyword.ITEMS), dict):
        text += (
            FieldDisplay.ARRAY_OPEN
            + type_hint(schema[SchemaKeyword.ITEMS])
            + FieldDisplay.ARRAY_CLOSE
        )
    if SchemaKeyword.ENUM in schema:
        text += FieldDisplay.DESCRIPTION_SEPARATOR + FieldDisplay.ENUM_SEPARATOR.join(
            json.dumps(item, ensure_ascii=FieldDisplay.JSON_ASCII)
            for item in schema[SchemaKeyword.ENUM]
        )
    return text


class SettingField:
    external_editable = True
    menu_open = False
    VIEWING = False
    EDITING = True
    DEFAULT_READ_ONLY = False
    ENABLED = True
    NO_CALLBACK = None

    def __init__(
        self, key, schema, value, t, *, readonly=DEFAULT_READ_ONLY, observations=()
    ):
        self.label = key
        self.schema = schema if isinstance(schema, dict) else {}
        self.original = deepcopy(value) if value is not MISSING else MISSING
        self.initial = (
            FieldDisplay.EMPTY
            if value is MISSING
            else (
                value
                if isinstance(value, str) and value
                else json.dumps(
                    value,
                    ensure_ascii=FieldDisplay.JSON_ASCII,
                    indent=FieldDisplay.JSON_INDENT,
                    allow_nan=JsonObject.ALLOW_NON_FINITE,
                )
            )
        )
        self.editing = self.VIEWING
        self._editing_app = None
        self.readonly = readonly
        self.observations = tuple(observations)
        self.enabled = lambda: self.ENABLED
        self.on_edit = self.NO_CALLBACK
        self._text, self._t = self.initial, t
        self._input = self._container = None
        self.on_change = Event(self)

    def _materialize(self):
        if self._input is not None:
            return
        self._input = TextArea(
            text=self._text,
            multiline=FieldDisplay.MULTILINE,
            height=lambda: min(
                FieldDisplay.MAX_EDITOR_LINES, self.input.buffer.document.line_count
            ),
            read_only=Condition(
                lambda: self.readonly or not self.editing or not self.enabled()
            ),
            wrap_lines=FieldDisplay.WRAP_LINES,
            style=ThemeSelector.SETTINGS_INPUT.style,
            focus_on_click=FieldDisplay.FOCUS_ON_CLICK,
        )
        self.input.window.always_hide_cursor = Condition(
            lambda: not self.editing or self.readonly or not self.enabled()
        )
        self.input.buffer.on_text_changed += lambda _: self.on_change()
        MouseBinding.set_focused_click(self.input.control, self.begin_if_viewing)
        MouseBinding.set_click_away(self.input.control, self.finish)
        registry = ShortcutRegistry()
        editable = lambda: self.enabled() and not self.readonly
        registry.add(
            [KeyName.EDIT, KeyName.EDIT_UPPER],
            KeyName.EDIT,
            ShortcutAction.EDITOR,
            lambda e: self.on_edit and self.on_edit(self),
            when=lambda: editable() and not self.editing,
        )
        registry.add(
            [KeyName.ENTER],
            ShortcutLabel.ENTER,
            lambda: ShortcutAction.DONE if self.editing else ShortcutAction.EDIT,
            lambda e: self.finish() if self.editing else self.begin(),
            when=editable,
        )
        registry.add(
            [KeyName.CLEAR],
            KeyName.CLEAR,
            ShortcutAction.CLEAR,
            lambda e: self.clear(),
            when=lambda: editable() and not self.editing,
        )
        registry.add(
            [KeyName.CONTROL_SPACE],
            ShortcutLabel.CONTROL_SPACE,
            ShortcutAction.NEWLINE,
            lambda e: self.input.insert_text(FieldDisplay.NEWLINE),
            when=lambda: editable() and self.editing,
        )
        registry.add(
            [(KeyName.ESCAPE, KeyName.ENTER)],
            ShortcutLabel.ALT_ENTER,
            ShortcutAction.NEWLINE,
            lambda e: self.input.insert_text(FieldDisplay.NEWLINE),
            when=lambda: editable() and self.editing,
        )
        self.input.control.key_bindings = registry.bindings
        registry.attach(self.input.control)
        self.input.window.style = lambda: (
            ThemeSelector.SETTINGS_INPUT_FOCUSED.style
            if get_app().layout.has_focus(self.input)
            else ThemeSelector.SETTINGS_INPUT.style
        )
        self._container = self._layout(self.editor())

    def editor(self):
        return self.input

    def _layout(self, editor):
        t = self._t
        description = self.schema.get(SchemaKeyword.DESCRIPTION) or t(
            MessageKey.SETTINGS_NO_DESCRIPTION
        )
        if SettingMetadata.schema_restart(self.schema):
            description += FieldDisplay.DESCRIPTION_SEPARATOR + t(
                MessageKey.SETTINGS_REQUIRES_RESTART
            )
        constraints = {
            name: self.schema[name]
            for name in FieldDisplay.CONSTRAINTS
            if name in self.schema
        }
        if constraints:
            description += FieldDisplay.DESCRIPTION_SEPARATOR + json.dumps(
                constraints, ensure_ascii=FieldDisplay.JSON_ASCII
            )
        return FieldLayout(
            lambda: (t.icons.edit + FieldDisplay.SPACE if self.editing else FieldDisplay.EMPTY) + self.label,
            editor,
            description=FieldDisplay.DESCRIPTION_PREFIX + description,
            type_label=t(MessageKey.SETTINGS_TYPE, type=type_hint(self.schema)),
            notes=self.observations,
        )

    @property
    def input(self):
        if self._input is None:
            self._materialize()
        return self._input

    @property
    def container(self):
        if self._container is None:
            self._materialize()
        return self._container

    @property
    def text(self):
        return self._input.text if self._input is not None else self._text

    @text.setter
    def text(self, value):
        changed = value != self.text
        self._text = value
        if self._input is not None:
            self._input.text = value
        elif changed:
            self.on_change()

    def clear(self):
        self.text = FieldDisplay.EMPTY

    def owns_focus(self):
        return get_app().layout.current_control is self.input.control

    def begin_if_viewing(self):
        if not self.editing:
            self.begin()

    def begin(self):
        if self.enabled() and not self.readonly:
            self.editing = self.EDITING
            self.input.buffer.cursor_position = len(self.input.text)
            if self._editing_app is None:
                self._editing_app = get_app()
                self._editing_app.before_render += self._finish_on_blur
                self._editing_app.on_invalidate += self._finish_on_blur
        get_app().invalidate()

    def _finish_on_blur(self, app):
        if app.layout.current_control is not self.input.control:
            self.finish()

    def finish(self):
        self.editing = self.VIEWING
        if self._editing_app is not None:
            self._editing_app.before_render -= self._finish_on_blur
            self._editing_app.on_invalidate -= self._finish_on_blur
            self._editing_app = None
        get_app().invalidate()

    def value(self):
        raw = self.text
        if raw == self.initial:
            return deepcopy(self.original) if self.original is not MISSING else MISSING
        if not raw.strip():
            return MISSING
        kind = self.schema.get(SchemaKeyword.TYPE)
        kinds = kind if isinstance(kind, list) else [kind]
        if SchemaType.STRING in kinds:
            if raw.strip() == FieldDisplay.JSON_NULL and SchemaType.NULL in kinds:
                return JsonObject.NULL
            return (
                json.loads(raw)
                if raw.lstrip().startswith(FieldDisplay.JSON_STRING_PREFIX)
                else raw
            )
        return JsonDocument.parse(raw)


class ColorField(SettingField):
    GAP = 1

    def editor(self):
        return VSplit([self.input, ColorPreview(self.value)], padding=self.GAP)


class FieldDropdown(DropdownBox):
    """Use the shared dropdown while the field retains its typed JSON draft."""

    def __init__(self, field):
        self.field = field
        super().__init__(field.menu_items, on_change=field.select, icons=lambda: field._t.icons)
        self.button.window.width = Dimension(min=self.MIN_CAPTION_WIDTH, weight=1)
        self.button.enabled = Condition(lambda: field.enabled() and not field.readonly)
        registry = ShortcutRegistry.require(self.control)
        registry.add([KeyName.CLEAR], KeyName.CLEAR, ShortcutAction.CLEAR,
                     lambda e: field.clear(), when=lambda: field.enabled() and not field.readonly)

    @property
    def text(self):
        return self.field.text

    @text.setter
    def text(self, value):
        self.field.text = value

    def caption(self):
        self.value = self.field.selected_index()
        self.placeholder = self.field.text
        return super().caption()

    def open(self):
        if not self.field.enabled() or self.field.readonly:
            return
        self.value = self.field.selected_index()
        self.field.editing = self.field.EDITING
        super().open()

    def close(self):
        self.field.editing = self.field.VIEWING
        super().close()

    def is_open(self):
        expanded = super().is_open()
        self.field.editing = expanded
        return expanded


class ChoiceField(SettingField):
    """Only declared finite values; missing/null and the original JSON stay distinct."""

    external_editable = False
    SCHEMA_KEY = "x-hub-choices"
    BOOLEAN_VALUES = (True, False)
    OPAQUE_FIELDS = (SchemaKeyword.ONE_OF, SchemaKeyword.ANY_OF, SchemaKeyword.ALL_OF, SchemaKeyword.REF)
    NO_SELECTION = None
    allow_unset = True

    @property
    def menu_open(self):
        return self._input is not None and self._input.is_open()

    def __init__(self, key, schema, value, t, **kwargs):
        super().__init__(key, schema, value, t, **kwargs)
        self.choices = self.declared_values(schema)

    @classmethod
    def declared_values(cls, schema):
        if any(key in schema for key in cls.OPAQUE_FIELDS):
            return ()
        if SchemaKeyword.CONST in schema:
            return (schema[SchemaKeyword.CONST],)
        if SchemaKeyword.ENUM in schema:
            return tuple(schema[SchemaKeyword.ENUM])
        if cls.SCHEMA_KEY in schema:
            return tuple(schema[cls.SCHEMA_KEY])
        kind = schema.get(SchemaKeyword.TYPE)
        kinds = set(kind if isinstance(kind, list) else (kind,))
        if SchemaType.BOOLEAN in kinds and kinds <= {SchemaType.BOOLEAN, SchemaType.NULL}:
            return cls.BOOLEAN_VALUES + ((JsonObject.NULL,) if SchemaType.NULL in kinds else ())
        return ()

    @classmethod
    def supports(cls, schema):
        return bool(cls.declared_values(schema))

    @property
    def text(self):
        return self._text

    @text.setter
    def text(self, value):
        changed = value != self._text
        self._text = value
        if changed:
            self.on_change()
        get_app().invalidate()

    def menu_items(self):
        result = [(0, self._t(MessageKey.SETTINGS_UNSET))] if self.allow_unset else []
        labels = self.schema.get(self.SCHEMA_KEY, {})
        for index, value in enumerate(self.choices, 1):
            label = next((label for key, label in labels.items()
                          if JsonDocument.same(key, value, missing=MISSING)), None)
            if label is None:
                label = value if isinstance(value, str) else json.dumps(value, ensure_ascii=FieldDisplay.JSON_ASCII)
            result.append((index, label))
        return result

    def selected_index(self):
        try:
            selected = self.value()
        except (ValueError, TypeError):
            return self.NO_SELECTION
        if selected is MISSING:
            return 0
        return next((index for index, value in enumerate(self.choices, 1)
                     if JsonDocument.same(value, selected, missing=MISSING)), self.NO_SELECTION)

    def select(self, index):
        if not self.enabled() or self.readonly:
            return
        if not index and not self.allow_unset:
            return
        value = self.choices[index - 1] if index else MISSING
        if JsonDocument.same(value, self.original, missing=MISSING):
            self.text = self.initial
        else:
            self.text = (FieldDisplay.EMPTY if value is MISSING else
                         json.dumps(value, ensure_ascii=FieldDisplay.JSON_ASCII,
                                    allow_nan=JsonObject.ALLOW_NON_FINITE))

    def clear(self):
        if self.allow_unset:
            super().clear()

    def _materialize(self):
        if self._input is not None:
            return
        self._input = FieldDropdown(self)
        self._container = self._layout(self.input)

    def begin(self):
        self.input.open()

    def finish(self):
        self.editing = self.VIEWING
        if self._input is not None:
            self._input.expanded = self._input.CLOSED
        get_app().invalidate()

    def owns_focus(self):
        return get_app().layout.current_control in self.input.controls


class SchemaForm:
    CONFIG_PATH = (ProjectConfigForm.config.__name__,)
    POLICY_PATH = (ImplementationParameters.POLICY,)
    PROJECT_POLICY_PATH = (*CONFIG_PATH, ProjectConfig.POLICIES)
    NO_EFFECTIVE = None
    OPAQUE_FIELDS = ChoiceField.OPAQUE_FIELDS

    def __init__(self, schema, values, t, *, prefix=(), effective=NO_EFFECTIVE, presentation=None):
        self.schema, self.original, self.t = schema, deepcopy(values), t
        self.presentation = presentation
        self.required = set()
        # 적용값은 고정 경로와 확장 키 조회에만 사용하고 저장할 값과 혼합하지 않는다.
        self.prefix = prefix
        self.effective = {
            name: ConfigurationView.read(view)
            for name, view in (effective or {}).items()
        }
        self.locked = set()
        for view in self.effective.values():
            for pointer, editable in view.editable.items():
                if not editable:
                    self.locked.add((*prefix, *JsonPointer.parts(pointer)))
        self._schema_locks(schema, prefix)
        self.fields = {}
        self._container = None
        self._walk(schema, prefix)
        self.visible_fields = {
            path: field for path, field in self.fields.items()
            if presentation is None or presentation(path[len(prefix):], required=path in self.required)
        }

    @property
    def children(self):
        return [field.container for field in self.visible_fields.values()]

    @property
    def container(self):
        if self._container is None:
            self._container = HSplit(self.children
                                     or [Notice(self.t(MessageKey.SETTINGS_NO_FIELDS))])
        return self._container

    def _schema_locks(self, schema, path):
        if isinstance(schema, dict):
            if schema.get(SchemaExtension.HOST_OVERRIDE):
                self.locked.add(path)
            for key, child in schema.get(SchemaKeyword.PROPERTIES, {}).items():
                self._schema_locks(child, (*path, key))
            for child in schema.get(SchemaKeyword.ALL_OF, ()):
                self._schema_locks(child, path)

    def _observations(self, path):
        notes = []
        if any(path[: len(locked)] == locked for locked in self.locked):
            notes.append(self.t(MessageKey.SETTINGS_HOST_LOCKED))
        elif any(locked[: len(path)] == path for locked in self.locked):
            notes.append(self.t(MessageKey.SETTINGS_HOST_PARTIAL))
        return notes

    def _walk(self, schema, path, *, required=False):
        if schema is SchemaBoolean.REJECT:
            return
        schema = schema if isinstance(schema, dict) else {}
        properties = schema.get(SchemaKeyword.PROPERTIES, {})
        if (schema.get(SchemaKeyword.TYPE) == SchemaType.OBJECT and not properties
                and schema.get(SchemaKeyword.ADDITIONAL_PROPERTIES) is SchemaBoolean.REJECT
                and not any(name in schema for name in self.OPAQUE_FIELDS)):
            return
        # Only an explicit object|null with ordinary properties may expose leaf
        # controls in essential mode. Preserve the full schema and original
        # object/null; unions, refs and conditional schemas remain JSON editors.
        kind = schema.get(SchemaKeyword.TYPE)
        if (
            properties
            and (kind == SchemaType.OBJECT or (
                self.presentation is not None and isinstance(kind, list)
                and set(kind) == {SchemaType.OBJECT, SchemaType.NULL}
            ))
            and not any(name in schema for name in self.OPAQUE_FIELDS)
        ):
            properties = dict(properties)
            if (
                self.effective
                and schema.get(
                    SchemaKeyword.ADDITIONAL_PROPERTIES, SchemaBoolean.ACCEPT
                )
                is not SchemaBoolean.REJECT
            ):
                # 이미 저장됐거나 주입된 열린 SDK 키도 조회/편집 가능하게 한다.
                candidates = [
                    at_path(self.original, path),
                    *(
                        at_path(view.values, path[len(self.prefix) :])
                        for view in self.effective.values()
                    ),
                ]
                for value in candidates:
                    if isinstance(value, dict):
                        for key in value:
                            properties.setdefault(
                                key, schema.get(SchemaKeyword.ADDITIONAL_PROPERTIES, {})
                            )
            for key, spec in properties.items():
                self._walk(spec, (*path, key), required=key in schema.get(SchemaKeyword.REQUIRED, ()))
        else:
            # A root-only open schema has no named field. Its complete value
            # remains in the JSON draft, rather than an unusable empty-path row.
            if not path:
                return
            if not schema.get(SchemaKeyword.DESCRIPTION) and (
                self.prefix or path[: len(SchemaForm.CONFIG_PATH)] == SchemaForm.CONFIG_PATH
            ):
                schema = {
                    **schema,
                    SchemaKeyword.DESCRIPTION: self.t(
                        MessageKey.SETTINGS_OPTION_DESCRIPTION,
                        name=SettingMetadata.PATH_SEPARATOR.join(path),
                    ),
                }
            field_type = (ColorField if schema.get(ColorPreview.SCHEMA_KEY) else
                          ChoiceField if ChoiceField.supports(schema) else SettingField)
            field = field_type(
                schema.get(
                    SchemaKeyword.TITLE, SettingMetadata.PATH_SEPARATOR.join(path)
                ),
                schema,
                at_path(self.original, path),
                self.t,
                readonly=any(path[: len(locked)] == locked for locked in self.locked),
                observations=self._observations(path),
            )
            self.fields[path] = field
            if required:
                self.required.add(path)

    def values(self):
        values = deepcopy(self.original)
        self.apply(values)
        return values

    def apply(self, values):
        candidate = deepcopy(values)
        for path, field in self.fields.items():
            if field.readonly:
                continue
            try:
                put_path(candidate, path, field.value())
            except (ValueError, TypeError) as error:
                raise ValueError(
                    FieldDisplay.PATH_ERROR.format(
                        path=SettingMetadata.PATH_SEPARATOR.join(path), error=error
                    )
                ) from error
        # Nullable object/ref/union는 JSON 편집기를 유지한다. 부분 고정된 객체도
        # sibling은 편집하되 고정 경로는 원래 저장값 그대로 두어야 한다.
        for path in self.locked:
            before, after = at_path(self.original, path), at_path(candidate, path)
            # Python의 True == 1과 달리 JSON 타입 변경도 고정값 변경이다.
            unchanged = JsonDocument.same(before, after, missing=MISSING)
            if not unchanged:
                raise ValueError(
                    FieldDisplay.PATH_ERROR.format(
                        path=SettingMetadata.PATH_SEPARATOR.join(path),
                        error=self.t(MessageKey.SETTINGS_HOST_LOCKED),
                    )
                )
        values.clear()
        values.update(candidate)
