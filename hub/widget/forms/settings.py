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
from prompt_toolkit.layout import HSplit, Window
from prompt_toolkit.application.current import get_app
from ...ui.input.registry import ShortcutRegistry
from prompt_toolkit.widgets import Label
from ..controls import TextArea


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
    GAP_HEIGHT = 1
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
        self.readonly = readonly
        self.observations = tuple(observations)
        self.enabled = lambda: self.ENABLED
        self.on_edit = self.NO_CALLBACK
        self.input = TextArea(
            text=self.initial,
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
        self.container = HSplit(
            [
                Label(
                    lambda: (
                        t.icons.edit + FieldDisplay.SPACE
                        if self.editing
                        else FieldDisplay.EMPTY
                    )
                    + self.label,
                    style=ThemeSelector.TITLE.style,
                ),
                Label(
                    FieldDisplay.DESCRIPTION_PREFIX + description,
                    style=ThemeSelector.MUTED.style,
                ),
                Label(
                    t(MessageKey.SETTINGS_TYPE, type=type_hint(self.schema)),
                    style=ThemeSelector.MUTED.style,
                ),
                self.input,
                *[
                    Label(note, style=ThemeSelector.MUTED.style)
                    for note in self.observations
                ],
                Window(height=FieldDisplay.GAP_HEIGHT),
            ]
        )

    def clear(self):
        self.input.text = FieldDisplay.EMPTY

    def begin(self):
        if self.enabled() and not self.readonly:
            self.editing = self.EDITING
            self.input.buffer.cursor_position = len(self.input.text)
        get_app().invalidate()

    def finish(self):
        self.editing = self.VIEWING
        get_app().invalidate()

    def value(self):
        raw = self.input.text
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


class ChoiceField(SettingField):
    """A finite choice in the same field region: Enter, arrows, Enter."""

    external_editable = False
    SCHEMA_KEY = "x-hub-choices"
    READ_ONLY = True
    CURSOR_HIDDEN = True

    def __init__(self, key, schema, value, t, **kwargs):
        super().__init__(key, schema, value, t, **kwargs)
        self.choices = schema[ChoiceField.SCHEMA_KEY]
        self.input.text = self.initial = self.choices[value]
        self.input.buffer.read_only = Condition(lambda: self.READ_ONLY)
        self.input.window.always_hide_cursor = Condition(lambda: self.CURSOR_HIDDEN)
        registry = ShortcutRegistry()
        editable = lambda: self.enabled() and not self.readonly
        registry.add(
            [KeyName.ENTER],
            ShortcutLabel.ENTER,
            lambda: ShortcutAction.DONE if self.editing else ShortcutAction.EDIT,
            lambda e: self.finish() if self.editing else self.begin(),
            when=editable,
        )

        def choose(event):
            labels = list(self.choices.values())
            delta = (
                -1 if event.key_sequence[-1].key in (KeyName.UP, KeyName.LEFT) else 1
            )
            self.input.text = labels[
                (labels.index(self.input.text) + delta) % len(labels)
            ]

        registry.add(
            [KeyName.UP, KeyName.DOWN, KeyName.LEFT, KeyName.RIGHT],
            ShortcutLabel.DIRECTIONS,
            ShortcutAction.SELECT,
            choose,
            when=lambda: editable() and self.editing,
        )
        self.input.control.key_bindings = registry.bindings
        registry.attach(self.input.control)

    def value(self):
        return next(
            key for key, label in self.choices.items() if label == self.input.text
        )


class SchemaForm:
    CONFIG_PATH = (ProjectConfigForm.config.__name__,)
    POLICY_PATH = (ImplementationParameters.POLICY,)
    PROJECT_POLICY_PATH = (*CONFIG_PATH, ProjectConfig.POLICIES)
    NO_EFFECTIVE = None
    OPAQUE_FIELDS = (
        SchemaKeyword.ONE_OF,
        SchemaKeyword.ANY_OF,
        SchemaKeyword.ALL_OF,
        SchemaKeyword.REF,
    )

    def __init__(self, schema, values, t, *, prefix=(), effective=NO_EFFECTIVE):
        self.schema, self.original, self.t = schema, deepcopy(values), t
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
        self.children = []
        self._walk(schema, prefix)
        self.container = HSplit(
            self.children or [Label(t(MessageKey.SETTINGS_NO_FIELDS))]
        )

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

    def _walk(self, schema, path):
        if schema is SchemaBoolean.REJECT:
            return
        schema = schema if isinstance(schema, dict) else {}
        properties = schema.get(SchemaKeyword.PROPERTIES, {})
        if (schema.get(SchemaKeyword.TYPE) == SchemaType.OBJECT and not properties
                and schema.get(SchemaKeyword.ADDITIONAL_PROPERTIES) is SchemaBoolean.REJECT
                and not any(name in schema for name in self.OPAQUE_FIELDS)):
            return
        # Nullable objects, unions and refs stay JSON editors so their semantics
        # are not lost by flattening an arbitrary branch of the schema.
        if (
            properties
            and schema.get(SchemaKeyword.TYPE) == SchemaType.OBJECT
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
                self._walk(spec, (*path, key))
        else:
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
            field_type = (
                ChoiceField if ChoiceField.SCHEMA_KEY in schema else SettingField
            )
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
            self.children.append(field.container)

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
