"""구현체 설정의 공개 경로와 내부 인자 이름을 연결한다. 값이나 정책을 생성하지 않는다."""

from copy import deepcopy
from .schema import implementation_schema, object_schema, validate_implementation_settings


class SettingsLayout:
    """명시적 인자/스키마를 config·policy로 분류하는 선언. 과거 JSON 변환기는 아니다.

    pack은 생성자·주입된 client의 명시값에만 사용한다. 저장/API 입력은 항상
    공개 envelope를 검증한 뒤 unpack하며, 누락과 null을 그대로 보존한다.
    """

    def __init__(self, *, config=(), policy=(), paths=None):
        self.paths = {**{key: "config." + key for key in config},
                      **{key: "policy." + key for key in policy}, **(paths or {})}
        targets = list(self.paths.values())
        for names in (list(self.paths), targets):
            if any(not isinstance(path, str) or not all(path.split(".")) for path in names):
                raise ValueError("Settings paths require nonempty segments")
            if any(other.startswith(path + ".") for path in names for other in names if path != other):
                raise ValueError("Settings paths cannot overlap")
        if len(targets) != len(set(targets)):
            raise ValueError("Settings paths must be unique")
        if any(not path.startswith(("config.", "policy.")) for path in targets):
            raise ValueError("Settings paths must belong to config/policy")

    def _misplaced_paths(self):
        """열린 config 확장이 내부 정책 인자를 우회하지 않게 예약 경로만 제외한다."""
        config = [path[7:] for path in self.paths.values() if path.startswith("config.")]
        candidates = {*self.paths, *(path[7:] for path in self.paths.values() if path.startswith("policy."))}
        return sorted(path for path in candidates
                      if not any(path == allowed or path.startswith(allowed + ".") for allowed in config)
                      and not any(allowed.startswith(path + ".") for allowed in config))

    @staticmethod
    def _presence(path):
        result = {}
        for part in reversed(path.split(".")):
            result = {"type": "object", "required": [part], "properties": {part: result}}
        return result

    @staticmethod
    def _read(data, path):
        for part in path.split("."):
            if not isinstance(data, dict) or part not in data:
                return False, None
            data = data[part]
        return True, data

    @staticmethod
    def _write(data, path, value):
        parts = path.split(".")
        for part in parts[:-1]:
            data = data.setdefault(part, {})
        data[parts[-1]] = deepcopy(value)

    @staticmethod
    def _remove(data, parts):
        """알려진 경로만 제거하고 같은 컨테이너의 확장 필드는 보존한다."""
        key, *rest = parts
        if key not in data:
            return
        if not rest:
            del data[key]
        elif isinstance(data[key], dict):
            SettingsLayout._remove(data[key], rest)
            if not data[key]:
                del data[key]

    def pack(self, explicit):
        result = {}
        extra = deepcopy(explicit)
        for path in self.paths:
            self._remove(extra, path.split("."))
        if extra:
            result["config"] = deepcopy(extra)
        for source, target in self.paths.items():
            exists, value = self._read(explicit, source)
            if exists:
                self._write(result, target, value)
        return result

    def unpack(self, settings):
        validate_implementation_settings(settings)
        result = deepcopy(settings.get("config", {}))
        for path in self._misplaced_paths():
            if self._read(result, path)[0]:
                raise ValueError(f"Setting config.{path} belongs at its declared config/policy path")
        for path in self.paths.values():
            if path.startswith("config."):
                self._remove(result, path.split(".")[1:])
        for target, source in self.paths.items():
            exists, value = self._read(settings, source)
            if exists:
                self._write(result, target, value)
        return result

    def schema(self, schema):
        # 전체 JSON Schema 검증은 registry의 checked_implementation_schema 경계가
        # 담당한다. 실행별 설정 조회마다 같은 메타스키마를 중복 검증하지 않는다.
        def classified(spec, prefix=""):
            # 나누는 컨테이너의 조건을 추측해 재작성하지 않는다. 복잡한 제약은
            # implementation_schema로 최종 공개 경로에 직접 선언할 수 있다.
            supported = {"type", "properties", "additionalProperties", "required", "title", "description",
                         "$comment", "$schema", "$defs", "definitions"}
            unsupported = [key for key in spec if key not in supported and not key.startswith("x-")]
            if (spec.get("type") != "object" or unsupported
                    or not isinstance(spec.get("additionalProperties", True), bool)
                    or (prefix and any(spec.get(key) for key in ("required", "$defs", "definitions")))):
                raise ValueError(f"SettingsLayout cannot safely relocate constraints at {prefix or '<root>'}; use implementation_schema")
            for name, child in spec.get("properties", {}).items():
                path = prefix + name
                if path in self.paths:
                    continue
                if not any(key.startswith(path + ".") for key in self.paths):
                    raise ValueError(f"Setting requires config/policy classification: {path}")
                classified(child, path + ".")
        classified(schema)
        if any(key not in self.paths for key in schema.get("required", ())):
            raise ValueError("SettingsLayout required fields must have a direct mapping; use implementation_schema")
        result = implementation_schema(config=object_schema(additionalProperties=schema.get("additionalProperties", True)),
                                       policy=object_schema(additionalProperties=False))
        for source, target in self.paths.items():
            spec = schema
            ancestors = []
            for part in source.split("."):
                ancestors.append(spec)
                spec = spec["properties"][part]
            parent = result
            parts = target.split(".")
            for index, part in enumerate(parts[:-1]):
                # config의 중첩 확장 허용 여부도 원래 선언을 따른다. policy는 선언 키만 처리한다.
                open_fields = (target.startswith("config.") and index < len(ancestors)
                               and ancestors[index].get("additionalProperties", True))
                parent = parent["properties"].setdefault(part, object_schema(additionalProperties=open_fields))
            parent["properties"][parts[-1]] = deepcopy(spec)
        for key in schema.get("required", ()):
            parent = result
            for part in self.paths[key].split("."):
                required = parent.setdefault("required", [])
                if part not in required:
                    required.append(part)
                parent = parent["properties"][part]
        misplaced = self._misplaced_paths()
        if misplaced:
            result["properties"]["config"].setdefault("not", {"anyOf": []})["anyOf"].extend(
                self._presence(path) for path in misplaced)
        result.update({key: deepcopy(value) for key, value in schema.items()
                       if key.startswith("x-") or key in ("title", "description", "$comment", "$schema", "$defs", "definitions")})

        def pointer(path):
            return "#/" + "/".join("properties/" + part.replace("~", "~0").replace("/", "~1")
                                    for part in path.split("."))
        locations = {pointer(source): pointer(target) for source, target in self.paths.items()}
        def relocate(node, mapping=False):
            if isinstance(node, dict):
                if not mapping and any(key in node for key in ("$id", "$anchor", "$dynamicAnchor", "$dynamicRef")):
                    raise ValueError("SettingsLayout cannot relocate schema resource scopes; use implementation_schema")
                for key, value in tuple(node.items()):
                    if key == "$ref" and not mapping:
                        match = next((old for old in locations if value == old or value.startswith(old + "/")), None)
                        if match:
                            node[key] = locations[match] + value[len(match):]
                        elif not value.startswith(("#/$defs/", "#/definitions/")):
                            raise ValueError("SettingsLayout cannot relocate this reference; use implementation_schema")
                    elif mapping or key not in ("const", "enum", "examples"):
                        relocate(value, not mapping and key in ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas"))
            elif isinstance(node, list):
                for child in node:
                    relocate(child)
        relocate(result)
        return result
