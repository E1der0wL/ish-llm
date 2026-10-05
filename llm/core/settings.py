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
        if len(targets) != len(set(targets)):
            raise ValueError("Settings paths must be unique")
        if any(not path.startswith(("config.", "policy.")) for path in targets):
            raise ValueError("Settings paths must belong to config/policy")

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
        for path in self.paths.values():
            if path.startswith("config."):
                self._remove(result, path.split(".")[1:])
        for target, source in self.paths.items():
            exists, value = self._read(settings, source)
            if exists:
                self._write(result, target, value)
        return result

    def schema(self, schema):
        def classified(spec, prefix=""):
            for name, child in spec.get("properties", {}).items():
                path = prefix + name
                if path in self.paths:
                    continue
                if not any(key.startswith(path + ".") for key in self.paths):
                    raise ValueError(f"Setting requires config/policy classification: {path}")
                classified(child, path + ".")
        classified(schema)
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
        # 열린 config 확장 공간이 이미 알려진 policy 키의 잘못된 위치를 허용하지 않는다.
        def disallow_policy(config, policy):
            fields, policies = config.get("properties", {}), policy.get("properties", {})
            misplaced = policies.keys() - fields.keys()
            if misplaced:
                config["not"] = {"anyOf": [{"required": [key]} for key in sorted(misplaced)]}
            for key in fields.keys() & policies.keys():
                if fields[key].get("type") == policies[key].get("type") == "object":
                    disallow_policy(fields[key], policies[key])
        disallow_policy(result["properties"]["config"], result["properties"]["policy"])
        result.update({key: deepcopy(value) for key, value in schema.items() if key.startswith("x-")})
        return result
