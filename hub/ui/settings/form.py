"""Schema fields retain missing/null distinctions and unknown JSON properties."""

from copy import deepcopy
import json

from prompt_toolkit.filters import Condition
from prompt_toolkit.layout import HSplit, Window
from prompt_toolkit.application.current import get_app
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.widgets import Label
from ..widgets import TextArea
from ...asset import icon


MISSING = object()


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
        return "JSON"
    kind = schema.get("type", "JSON")
    text = " | ".join(kind) if isinstance(kind, list) else kind
    if kind == "array" and isinstance(schema.get("items"), dict):
        text += "[" + type_hint(schema["items"]) + "]"
    if "enum" in schema:
        text += " · " + ", ".join(json.dumps(item, ensure_ascii=False) for item in schema["enum"])
    return text


class SettingField:
    def __init__(self, key, schema, value, t, *, readonly=False, observations=()):
        self.schema = schema if isinstance(schema, dict) else {}
        self.original = deepcopy(value) if value is not MISSING else MISSING
        self.initial = ("" if value is MISSING else value if isinstance(value, str) and value else
                        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))
        kind = self.schema.get("type")
        complex_value = kind in ("object", "array") or isinstance(value, (dict, list)) or kind is None
        self.editing = False
        self.readonly = readonly
        self.observations = tuple(observations)
        self.enabled = lambda: True
        self.input = TextArea(text=self.initial, multiline=True, height=3 if complex_value else 1,
                              read_only=Condition(lambda: self.readonly or not self.editing or not self.enabled()),
                              wrap_lines=True, style="class:hub.settings-input", focus_on_click=True)
        keys = KeyBindings()
        @keys.add("enter", eager=True)
        def edit(event):
            self.finish() if self.editing else self.begin()
        @keys.add("c-space", filter=Condition(lambda: self.editing), eager=True)
        def newline(event):
            self.input.buffer.insert_text("\n")
        self.input.control.key_bindings = keys
        self.input.window.style = lambda: "class:hub.settings-input" + (
            " class:hub.focused" if get_app().layout.has_focus(self.input) else "")
        description = self.schema.get("description") or t("settings_no_description")
        constraints = {name: self.schema[name] for name in ("minimum", "maximum", "exclusiveMinimum",
                       "minLength", "maxLength", "pattern") if name in self.schema}
        if constraints:
            description += " · " + json.dumps(constraints, ensure_ascii=False)
        self.container = HSplit([Label(lambda: (icon.EDIT + " " if self.editing else "") + key, style="class:hub.title"),
                                 Label("- " + description, style="class:hub.muted"),
                                 Label(t("settings_type", type=type_hint(self.schema)), style="class:hub.muted"),
                                 *([Label(t("settings_stored"), style="class:hub.muted")] if observations else []), self.input,
                                 *[Label(note, style="class:hub.muted") for note in self.observations],
                                 Window(height=1)])

    def begin(self):
        if self.enabled() and not self.readonly:
            self.editing = True
        get_app().invalidate()

    def finish(self):
        self.editing = False
        get_app().invalidate()

    def value(self):
        raw = self.input.text
        if raw == self.initial:
            return deepcopy(self.original) if self.original is not MISSING else MISSING
        if not raw.strip():
            return MISSING
        kind = self.schema.get("type")
        kinds = kind if isinstance(kind, list) else [kind]
        if "string" in kinds:
            if raw.strip() == "null" and "null" in kinds:
                return None
            return json.loads(raw) if raw.lstrip().startswith('"') else raw
        return json.loads(raw, parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Invalid JSON: " + value)))


class SchemaForm:
    def __init__(self, schema, values, t, *, prefix=(), effective=None):
        self.schema, self.original, self.t = schema, deepcopy(values), t
        # 적용값은 표시 전용 사본이다. 저장할 값과 혼합하거나 기본값으로 채우지 않는다.
        self.prefix, self.effective = prefix, deepcopy(effective or {})
        self.locked = set()
        for view in self.effective.values():
            for pointer, editable in view.get("editable", {}).items():
                if not editable:
                    self.locked.add((*prefix, *(part.replace("~1", "/").replace("~0", "~")
                                               for part in pointer.lstrip("/").split("/"))))
        self._schema_locks(schema, prefix)
        self.fields = {}
        self.children = []
        self._walk(schema, prefix)
        self.container = HSplit(self.children or [Label(t("settings_no_fields"))])

    def _schema_locks(self, schema, path):
        if isinstance(schema, dict):
            if schema.get("x-host-override"):
                self.locked.add(path)
            for key, child in schema.get("properties", {}).items():
                self._schema_locks(child, (*path, key))
            for child in schema.get("allOf", ()):
                self._schema_locks(child, path)

    def _observations(self, path):
        relative = path[len(self.prefix):]
        pointer = ("/" + "/".join(part.replace("~", "~0").replace("/", "~1") for part in relative)) if relative else ""
        notes = []
        for name, view in self.effective.items():
            value = at_path(view.get("values", {}), relative)
            sources = {source for key, source in view.get("sources", {}).items()
                       if key == pointer or key.startswith(pointer + "/") or pointer.startswith(key + "/")}
            display = (self.t("settings_runtime_value") if "host_runtime" in sources else self.t("settings_unset")) if value is MISSING else json.dumps(value, ensure_ascii=False)
            notes.append(self.t("settings_effective", name=name, value=display,
                                source=", ".join(sorted(sources)) or self.t("settings_unset")))
        if any(path[:len(locked)] == locked for locked in self.locked):
            notes.append(self.t("settings_host_locked"))
        elif any(locked[:len(path)] == path for locked in self.locked):
            notes.append(self.t("settings_host_partial"))
        return notes

    def _walk(self, schema, path):
        if schema is False:
            return
        schema = schema if isinstance(schema, dict) else {}
        properties = schema.get("properties", {})
        # Nullable objects, unions and refs stay JSON editors so their semantics
        # are not lost by flattening an arbitrary branch of the schema.
        if properties and schema.get("type") == "object" and not any(
                name in schema for name in ("oneOf", "anyOf", "allOf", "$ref")):
            properties = dict(properties)
            if self.effective and schema.get("additionalProperties", True) is not False:
                # 이미 저장됐거나 주입된 열린 SDK 키도 조회/편집 가능하게 한다.
                candidates = [at_path(self.original, path), *(at_path(view.get("values", {}), path[len(self.prefix):])
                              for view in self.effective.values())]
                for value in candidates:
                    if isinstance(value, dict):
                        for key in value:
                            properties.setdefault(key, schema.get("additionalProperties", {}))
            for key, spec in properties.items():
                self._walk(spec, (*path, key))
        else:
            field = SettingField(schema.get("title", ".".join(path)), schema, at_path(self.original, path), self.t,
                readonly=any(path[:len(locked)] == locked for locked in self.locked), observations=self._observations(path))
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
                raise ValueError(f"{'.'.join(path)}: {error}") from error
        # Nullable object/ref/union는 JSON 편집기를 유지한다. 부분 고정된 객체도
        # sibling은 편집하되 고정 경로는 원래 저장값 그대로 두어야 한다.
        for path in self.locked:
            before, after = at_path(self.original, path), at_path(candidate, path)
            # Python의 True == 1과 달리 JSON 타입 변경도 고정값 변경이다.
            unchanged = (before is after if before is MISSING or after is MISSING else
                         json.dumps(before, sort_keys=True, allow_nan=False) ==
                         json.dumps(after, sort_keys=True, allow_nan=False))
            if not unchanged:
                raise ValueError(f"{'.'.join(path)}: {self.t('settings_host_locked')}")
        values.clear()
        values.update(candidate)
