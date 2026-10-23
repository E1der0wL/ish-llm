"""구현체 설정의 공개 경로와 내부 인자 이름을 연결한다. 값이나 정책을 생성하지 않는다."""

from copy import deepcopy
from .schema import (implementation_schema, object_schema, schema_nodes, SchemaKeyword,
                     SchemaType, SchemaBoolean, SchemaExtension, SchemaReference)
from .json_data import JsonPointer
from .implementation_parameters import ImplementationParameters, ParameterLookup


class ParameterLayout:
    """명시적 인자/스키마를 config·policy로 분류하는 선언. 과거 JSON 변환기는 아니다.

    pack은 생성자·주입된 client의 명시값에만 사용한다. 저장/API 입력은 항상
    공개 envelope를 검증한 뒤 unpack하며, 누락과 null을 그대로 보존한다.
    """

    CONFIG_PREFIX = ImplementationParameters.CONFIG + ParameterLookup.SEPARATOR
    POLICY_PREFIX = ImplementationParameters.POLICY + ParameterLookup.SEPARATOR
    ROOT = ""
    ROOT_LABEL = "<root>"
    NO_PATH_OVERRIDES = None
    SUPPORTED_SCHEMA_FIELDS = frozenset((SchemaKeyword.TYPE, SchemaKeyword.PROPERTIES,
        SchemaKeyword.ADDITIONAL_PROPERTIES, SchemaKeyword.REQUIRED, SchemaKeyword.TITLE,
        SchemaKeyword.DESCRIPTION, SchemaKeyword.COMMENT, SchemaKeyword.SCHEMA,
        SchemaKeyword.DEFS, SchemaKeyword.DEFINITIONS))
    ROOT_ONLY_FIELDS = (SchemaKeyword.REQUIRED, SchemaKeyword.DEFS, SchemaKeyword.DEFINITIONS)
    PRESERVED_SCHEMA_FIELDS = (SchemaKeyword.TITLE, SchemaKeyword.DESCRIPTION, SchemaKeyword.COMMENT,
        SchemaKeyword.SCHEMA, SchemaKeyword.DEFS, SchemaKeyword.DEFINITIONS)
    MISPLACED = "Setting config.{path} belongs at its declared config/policy path"
    UNSAFE_RELOCATION = "ParameterLayout cannot safely relocate constraints at {path}; use implementation_schema"
    UNCLASSIFIED = "Setting requires config/policy classification: {path}"
    INVALID_PATH = 'Parameter paths require nonempty segments'
    OVERLAPPING_PATH = 'Parameter paths cannot overlap'
    DUPLICATE_PATH = 'Parameter paths must be unique'
    INVALID_SECTION = 'Parameter paths must belong to config/policy'
    UNMAPPED_REQUIRED = 'ParameterLayout required fields must have a direct mapping; use implementation_schema'
    RESOURCE_SCOPE = 'ParameterLayout cannot relocate schema resource scopes; use implementation_schema'
    UNSUPPORTED_REFERENCE = 'ParameterLayout cannot relocate this reference; use implementation_schema'

    def __init__(self, *, config=(), policy=(), paths=NO_PATH_OVERRIDES):
        self.paths = {**{key: ParameterLayout.CONFIG_PREFIX + key for key in config},
                      **{key: ParameterLayout.POLICY_PREFIX + key for key in policy}, **(paths or {})}
        targets = list(self.paths.values())
        for names in (list(self.paths), targets):
            if any(not isinstance(path, str) or not all(path.split(ParameterLookup.SEPARATOR)) for path in names):
                raise ValueError(ParameterLayout.INVALID_PATH)
            if any(other.startswith(path + ParameterLookup.SEPARATOR) for path in names for other in names if path != other):
                raise ValueError(ParameterLayout.OVERLAPPING_PATH)
        if len(targets) != len(set(targets)):
            raise ValueError(ParameterLayout.DUPLICATE_PATH)
        if any(not path.startswith((ParameterLayout.CONFIG_PREFIX, ParameterLayout.POLICY_PREFIX)) for path in targets):
            raise ValueError(ParameterLayout.INVALID_SECTION)

    def _misplaced_paths(self):
        """열린 config 확장이 내부 정책 인자를 우회하지 않게 예약 경로만 제외한다."""
        config_paths = [path[len(self.CONFIG_PREFIX):] for path in self.paths.values() if path.startswith(self.CONFIG_PREFIX)]
        candidates = {*self.paths, *(path[len(self.POLICY_PREFIX):] for path in self.paths.values() if path.startswith(self.POLICY_PREFIX))}
        return sorted(path for path in candidates
                      if not any(path == allowed or path.startswith(allowed + ParameterLookup.SEPARATOR) for allowed in config_paths)
                      and not any(allowed.startswith(path + ParameterLookup.SEPARATOR) for allowed in config_paths))

    @staticmethod
    def _presence(path):
        result = {}
        for part in reversed(path.split(ParameterLookup.SEPARATOR)):
            result = {SchemaKeyword.TYPE: SchemaType.OBJECT, SchemaKeyword.REQUIRED: [part], SchemaKeyword.PROPERTIES: {part: result}}
        return result

    @staticmethod
    def _write(data, path, value):
        parts = path.split(ParameterLookup.SEPARATOR)
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
            ParameterLayout._remove(data[key], rest)
            if not data[key]:
                del data[key]

    def pack(self, explicit):
        result = {}
        extra = deepcopy(explicit)
        for path in self.paths:
            self._remove(extra, path.split(ParameterLookup.SEPARATOR))
        if extra:
            result[ImplementationParameters.CONFIG] = deepcopy(extra)
        for source, target in self.paths.items():
            selected = ParameterLookup.read(explicit, source)
            if selected.present:
                self._write(result, target, selected.value)
        return result

    def unpack(self, config):
        parameters = ImplementationParameters.read(config)
        result = parameters.config
        for path in self._misplaced_paths():
            if ParameterLookup.read(result, path).present:
                raise ValueError(self.MISPLACED.format(path=path))
        for path in self.paths.values():
            if path.startswith(ParameterLayout.CONFIG_PREFIX):
                self._remove(result, path.split(ParameterLookup.SEPARATOR)[1:])
        for target, source in self.paths.items():
            selected = parameters.select(source)
            if selected.present:
                self._write(result, target, selected.value)
        return result

    def schema(self, schema):
        # 전체 JSON Schema 검증은 registry의 checked_implementation_schema 경계가
        # 담당한다. 실행별 설정 조회마다 같은 메타스키마를 중복 검증하지 않는다.
        def classified(spec, prefix=self.ROOT):
            # 나누는 컨테이너의 조건을 추측해 재작성하지 않는다. 복잡한 제약은
            # implementation_schema로 최종 공개 경로에 직접 선언할 수 있다.
            unsupported = [key for key in spec if key not in self.SUPPORTED_SCHEMA_FIELDS and not key.startswith(SchemaExtension.PREFIX)]
            if (spec.get(SchemaKeyword.TYPE) != SchemaType.OBJECT or unsupported
                    or not isinstance(spec.get(SchemaKeyword.ADDITIONAL_PROPERTIES, SchemaBoolean.ACCEPT), bool)
                    or (prefix and any(spec.get(key) for key in self.ROOT_ONLY_FIELDS))):
                raise ValueError(self.UNSAFE_RELOCATION.format(path=prefix or self.ROOT_LABEL))
            for name, child in spec.get(SchemaKeyword.PROPERTIES, {}).items():
                path = prefix + name
                if path in self.paths:
                    continue
                if not any(key.startswith(path + ParameterLookup.SEPARATOR) for key in self.paths):
                    raise ValueError(self.UNCLASSIFIED.format(path=path))
                classified(child, path + ParameterLookup.SEPARATOR)
        classified(schema)
        if any(key not in self.paths for key in schema.get(SchemaKeyword.REQUIRED, ())):
            raise ValueError(ParameterLayout.UNMAPPED_REQUIRED)
        result = implementation_schema(config=object_schema(additionalProperties=schema.get(SchemaKeyword.ADDITIONAL_PROPERTIES, SchemaBoolean.ACCEPT)),
                                       policy=object_schema(additionalProperties=SchemaBoolean.REJECT))
        for source, target in self.paths.items():
            spec = schema
            ancestors = []
            for part in source.split(ParameterLookup.SEPARATOR):
                ancestors.append(spec)
                spec = spec[SchemaKeyword.PROPERTIES][part]
            parent = result
            parts = target.split(ParameterLookup.SEPARATOR)
            for index, part in enumerate(parts[:-1]):
                # config의 중첩 확장 허용 여부도 원래 선언을 따른다. policy는 선언 키만 처리한다.
                open_fields = (target.startswith(ParameterLayout.CONFIG_PREFIX) and index < len(ancestors)
                               and ancestors[index].get(SchemaKeyword.ADDITIONAL_PROPERTIES, SchemaBoolean.ACCEPT))
                parent = parent[SchemaKeyword.PROPERTIES].setdefault(part, object_schema(additionalProperties=open_fields))
            parent[SchemaKeyword.PROPERTIES][parts[-1]] = deepcopy(spec)
        for key in schema.get(SchemaKeyword.REQUIRED, ()):
            parent = result
            for part in self.paths[key].split(ParameterLookup.SEPARATOR):
                required = parent.setdefault(SchemaKeyword.REQUIRED, [])
                if part not in required:
                    required.append(part)
                parent = parent[SchemaKeyword.PROPERTIES][part]
        misplaced = self._misplaced_paths()
        if misplaced:
            result[SchemaKeyword.PROPERTIES][ImplementationParameters.CONFIG].setdefault(SchemaKeyword.NOT, {SchemaKeyword.ANY_OF: []})[SchemaKeyword.ANY_OF].extend(
                self._presence(path) for path in misplaced)
        result.update({key: deepcopy(value) for key, value in schema.items()
                       if key.startswith(SchemaExtension.PREFIX) or key in self.PRESERVED_SCHEMA_FIELDS})

        def pointer(path):
            return JsonPointer.reference(part for name in path.split(ParameterLookup.SEPARATOR)
                                         for part in (SchemaKeyword.PROPERTIES, name))
        locations = {pointer(source): pointer(target) for source, target in self.paths.items()}
        for node in schema_nodes(result):
            if any(key in node for key in SchemaReference.RESOURCE_KEYS):
                raise ValueError(ParameterLayout.RESOURCE_SCOPE)
            if SchemaKeyword.REF in node:
                value = node[SchemaKeyword.REF]
                match = next((old for old in locations if value == old or value.startswith(old + JsonPointer.SEPARATOR)), None)
                if match:
                    node[SchemaKeyword.REF] = locations[match] + value[len(match):]
                elif not value.startswith(SchemaReference.DEFINITION_PREFIXES):
                    raise ValueError(ParameterLayout.UNSUPPORTED_REFERENCE)
        return result
