"""UI에서 공유할 JSON Schema. 공급자의 열린 인자를 닫힌 목록으로 제한하지 않는다."""

from copy import deepcopy
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
from urllib.parse import urldefrag, urljoin
from jsonschema import Draft202012Validator
from .implementation_parameters import ImplementationParameters
from .json_data import JsonObject, JsonPointer


_schema_checks = ContextVar("schema_checks", default=None)


class _SchemaChecks:
    """Exact syntax proofs and the subset used by this preparation."""

    def __init__(self, previous):
        self.known = set(previous)
        self.used = set()

    def __contains__(self, key):
        if key not in self.known:
            return False
        self.used.add(key)
        return True

    def add(self, key):
        self.known.add(key)
        self.used.add(key)


@contextmanager
def reuse_schema_checks(*, previous=None):
    """Reuse exact syntax proofs, never configuration or authorization decisions.

    Nested operations share the synchronous scope. An implementation registry may
    seed a separate scope with its last successful preparation's syntax proofs
    and retain only the yielded, actually used subset. Hooks, instance validation
    and semantic checks still run; changed JSON cannot borrow a different proof.
    """
    current = _schema_checks.get()
    if previous is None and current is not None:
        yield current.used
        return
    checks = _SchemaChecks(previous if previous is not None else ())
    if current is not None:
        checks.known.update(current.known)
    token = _schema_checks.set(checks)
    try:
        yield checks.used
    finally:
        _schema_checks.reset(token)
        if current is not None:
            # Nested owners share only exact syntax proofs, never input values
            # or authorization. Retain only schemas actually used in the scope.
            current.known.update(checks.used)
            current.used.update(checks.used)


class SchemaKeyword:
    TYPE = "type"
    CONST = "const"
    MINIMUM = "minimum"
    MAXIMUM = "maximum"
    EXCLUSIVE_MINIMUM = "exclusiveMinimum"
    MIN_LENGTH = "minLength"
    MAX_LENGTH = "maxLength"
    MIN_ITEMS = "minItems"
    MAX_ITEMS = "maxItems"
    MIN_PROPERTIES = "minProperties"
    PATTERN = "pattern"
    ENUM = "enum"
    UNIQUE_ITEMS = "uniqueItems"
    DESCRIPTION = "description"
    TITLE = "title"
    PROPERTIES = "properties"
    ADDITIONAL_PROPERTIES = "additionalProperties"
    PATTERN_PROPERTIES = "patternProperties"
    DEFS = "$defs"
    DEFINITIONS = "definitions"
    DEPENDENT_SCHEMAS = "dependentSchemas"
    ITEMS = "items"
    ADDITIONAL_ITEMS = "additionalItems"
    UNEVALUATED_PROPERTIES = "unevaluatedProperties"
    UNEVALUATED_ITEMS = "unevaluatedItems"
    CONTAINS = "contains"
    PROPERTY_NAMES = "propertyNames"
    NOT = "not"
    IF = "if"
    THEN = "then"
    ELSE = "else"
    CONTENT_SCHEMA = "contentSchema"
    ALL_OF = "allOf"
    ANY_OF = "anyOf"
    ONE_OF = "oneOf"
    PREFIX_ITEMS = "prefixItems"
    DEPENDENCIES = "dependencies"
    REF = "$ref"
    DYNAMIC_REF = "$dynamicRef"
    DEFAULT = "default"
    REQUIRED = "required"
    COMMENT = "$comment"
    SCHEMA = "$schema"
    ID = "$id"
    ANCHOR = "$anchor"
    DYNAMIC_ANCHOR = "$dynamicAnchor"


class SchemaType:
    OBJECT = "object"
    INTEGER = "integer"
    NUMBER = "number"
    NULL = "null"
    STRING = "string"
    ARRAY = "array"
    BOOLEAN = "boolean"


class SchemaBoolean:
    ACCEPT = True
    REJECT = False


class SchemaExtension:
    OPEN_PARAMETERS = "x-open-parameters"
    RUNTIME_CONFIGURATION = "x-runtime-configuration"
    PREFIX = "x-"
    PARAMETER_KEY = "x-parameter-key"
    OWNER = "x-schema-owner"
    OPEN_KIND = "x-open-kind"
    HOST_OVERRIDE = "x-host-override"
    HOST_FIXED = True
    NARROWING = "x-narrowing"
    RESOURCE = "x-resource"


class SchemaResource:
    TOKEN_COUNTERS = "token_counters"


class OpenSchemaKind:
    METADATA = "metadata"
    PROVIDER = "provider"
    IMPLEMENTATION = "implementation"
    ADAPTER = "adapter"
    RESULT = "result"
    SCHEMA = "schema"
    DATA = "data"
    ALL = (METADATA, PROVIDER, IMPLEMENTATION, ADAPTER, RESULT, SCHEMA, DATA)


class SchemaOwnership:
    APPLICATION_METADATA = "application.metadata"
    HOST_IMPLEMENTATION = "host-supplied selected implementation value"


class SchemaInput:
    UNSPECIFIED = None
    EMPTY_DESCRIPTION = ""


class SchemaTraversal:
    REFERENCE_OR_COMPOSITION = (SchemaKeyword.REF, SchemaKeyword.ALL_OF, SchemaKeyword.ANY_OF, SchemaKeyword.ONE_OF)
    MAPPINGS = (SchemaKeyword.PROPERTIES, SchemaKeyword.PATTERN_PROPERTIES, SchemaKeyword.DEFS,
                SchemaKeyword.DEFINITIONS, SchemaKeyword.DEPENDENT_SCHEMAS)
    SINGLE = (SchemaKeyword.ITEMS, SchemaKeyword.ADDITIONAL_ITEMS, SchemaKeyword.ADDITIONAL_PROPERTIES,
              SchemaKeyword.UNEVALUATED_PROPERTIES, SchemaKeyword.UNEVALUATED_ITEMS, SchemaKeyword.CONTAINS,
              SchemaKeyword.PROPERTY_NAMES, SchemaKeyword.NOT, SchemaKeyword.IF, SchemaKeyword.THEN,
              SchemaKeyword.ELSE, SchemaKeyword.CONTENT_SCHEMA)
    ARRAYS = (SchemaKeyword.ALL_OF, SchemaKeyword.ANY_OF, SchemaKeyword.ONE_OF, SchemaKeyword.PREFIX_ITEMS)
    DIRECT = True
    INDIRECT = False


class SchemaReference:
    LOCAL_KEYS = (SchemaKeyword.REF, SchemaKeyword.DYNAMIC_REF)
    RESOURCE_KEYS = (SchemaKeyword.ID, SchemaKeyword.ANCHOR, SchemaKeyword.DYNAMIC_ANCHOR, SchemaKeyword.DYNAMIC_REF)
    ANCHOR_KEYS = (SchemaKeyword.ANCHOR, SchemaKeyword.DYNAMIC_ANCHOR)
    ANONYMOUS_IDS = (None, "", JsonPointer.FRAGMENT)
    NESTED_ID_PREFIX = "https://schemas.ish-llm.invalid/nested/"
    NESTED_ID_SUFFIX = "/"
    ID_ENCODING = "utf-8"
    ID_SORT_KEYS = True
    DEFINITION_PREFIXES = tuple(JsonPointer.reference((key,)) + JsonPointer.SEPARATOR
                                for key in (SchemaKeyword.DEFS, SchemaKeyword.DEFINITIONS))


class SchemaNarrowing:
    MAXIMUM = SchemaKeyword.MAXIMUM
    NUMERIC = frozenset((SchemaType.INTEGER, SchemaType.NUMBER))
    NULLABLE_NUMERIC = NUMERIC | {SchemaType.NULL}


class SchemaError:
    UNKNOWN_FIELD = "Unknown declared field: "
    FIELD_SEPARATOR = ": "
    INTEGER_REQUIRED = " must be an integer"
    OPEN_OWNER = "Open schema requires an explicit semantic owner and category"
    LOCAL_REFERENCE = "Only local schema references are supported"
    IMPLICIT_DEFAULT = "Configuration schemas must not provide implicit defaults"
    IMPLEMENTATION_OBJECT = "Implementation configuration schema must describe an object"
    IMPLEMENTATION_FIELDS = "Implementation configuration schema fields belong under config/policy"
    NON_NULL_SECTION = "Implementation {name} schema must describe a non-null object"
    NARROWING_PATH = "x-narrowing requires an inline numeric property path: "
    RESOURCE_CONFLICT = "Conflicting schema resource {identity!r}: {first} and {second}"
    ANCHOR_CONFLICT = "Conflicting schema anchor {identity!r}: {first} and {second}"

def field(kind, description=SchemaInput.EMPTY_DESCRIPTION, **constraints):
    return {SchemaKeyword.TYPE: kind, SchemaKeyword.DESCRIPTION: description, **constraints}


def validate_declared_fields(values, properties):
    """Validate explicit JSON fields without defaults or lossy numeric coercion.

    Owners supply the same property declarations used by their public Schema.
    Runtime SDK objects must stay outside this JSON-only projection.
    """
    json.dumps(values, allow_nan=JsonObject.ALLOW_NON_FINITE)
    for name, value in values.items():
        if name not in properties:
            raise ValueError(SchemaError.UNKNOWN_FIELD + name)
        spec = properties[name]
        error = next(Draft202012Validator(spec).iter_errors(value), None)
        if error is not None:
            raise ValueError(name + SchemaError.FIELD_SEPARATOR + error.message)
        kinds = spec.get(SchemaKeyword.TYPE, ())
        if value is not JsonObject.NULL and (kinds == SchemaType.INTEGER or isinstance(kinds, list) and SchemaType.INTEGER in kinds):
            if type(value) is not int:
                raise ValueError(name + SchemaError.INTEGER_REQUIRED)


def object_schema(properties=SchemaInput.UNSPECIFIED, **extra):
    return {SchemaKeyword.TYPE: SchemaType.OBJECT, SchemaKeyword.PROPERTIES: properties or {}, SchemaKeyword.ADDITIONAL_PROPERTIES: SchemaBoolean.REJECT, **extra}


def open_schema(owner, *, category, properties=SchemaInput.UNSPECIFIED, **extra):
    """교체 가능한 구현/외부 데이터의 소유자를 명시한다. backend 설정 기본값은 아니다."""
    if not isinstance(owner, str) or not owner.strip() or category not in OpenSchemaKind.ALL:
        raise ValueError(SchemaError.OPEN_OWNER)
    return object_schema(properties, additionalProperties=SchemaBoolean.ACCEPT,
                         **{SchemaExtension.OWNER: owner, SchemaExtension.OPEN_KIND: category}, **extra)


def metadata_schema():
    """Application 데이터. 실행 권한이나 구현체 설정으로 읽지 않는다."""
    return open_schema(SchemaOwnership.APPLICATION_METADATA, category=OpenSchemaKind.METADATA)


def implementation_schema(*, config=SchemaInput.UNSPECIFIED, policy=SchemaInput.UNSPECIFIED, **metadata):
    """구현체 설정의 공통 외형. 필드 의미·필수값은 구현체가 선언하며 값은 만들지 않는다."""
    return object_schema({ImplementationParameters.CONFIG: object_schema() if config is SchemaInput.UNSPECIFIED else config,
                          ImplementationParameters.POLICY: object_schema() if policy is SchemaInput.UNSPECIFIED else policy},
                         additionalProperties=SchemaBoolean.REJECT, **metadata)


def validate_parameters(value, *, scope=ImplementationParameters.DEFAULT_SCOPE):
    """Public envelope validation delegates to its sole config/policy Owner."""
    ImplementationParameters.validate(value, scope=scope)


def mark_host_overrides(schema, values):
    """병합과 같은 leaf 단위로 고정값을 표시한다. 빈 dict는 덮어쓰는 값이 없다."""
    if isinstance(values, dict):
        if values and SchemaKeyword.TYPE not in schema and not any(key in schema for key in SchemaTraversal.REFERENCE_OR_COMPOSITION):
            # 선택 구현체의 opaque 인자에 대한 UI 관찰이다. Backend가 새 옵션 계약을 만들지 않는다.
            schema.update(open_schema(SchemaOwnership.HOST_IMPLEMENTATION, category=OpenSchemaKind.IMPLEMENTATION))
        for key, value in values.items():
            mark_host_overrides(schema.setdefault(SchemaKeyword.PROPERTIES, {}).setdefault(key, {}), value)
    else:
        schema[SchemaExtension.HOST_OVERRIDE] = SchemaExtension.HOST_FIXED


def _schema_children(value):
    """Share the structural vocabulary, retaining declaration paths for errors."""
    for key in SchemaTraversal.MAPPINGS:
        mapping = value.get(key)
        if isinstance(mapping, dict):
            for name, child in mapping.items():
                yield (key, name), child
    for key in SchemaTraversal.SINGLE:
        child = value.get(key)
        if isinstance(child, list):
            for index, item in enumerate(child):
                yield (key, str(index)), item
        else:
            yield (key,), child
    for key in SchemaTraversal.ARRAYS:
        children = value.get(key)
        if isinstance(children, list):
            for index, child in enumerate(children):
                yield (key, str(index)), child
    dependencies = value.get(SchemaKeyword.DEPENDENCIES)
    if isinstance(dependencies, dict):
        for name, child in dependencies.items():
            yield (SchemaKeyword.DEPENDENCIES, name), child


def schema_nodes(value):
    """Yield only JSON Schema objects, never instance data or annotation payloads.

    The vocabulary is structural: property names are opaque, and const/enum/
    examples/default plus extension annotations are data. Local references are
    not expanded; their definitions are visited at their declared locations.
    """
    if not isinstance(value, dict):
        return
    yield value
    for _, child in _schema_children(value):
        yield from schema_nodes(child)


def validate_local_refs(value):
    """Check schema references, never keys inside instance data or annotations."""
    for item in schema_nodes(value):
        for key in SchemaReference.LOCAL_KEYS:
            if key in item and (not isinstance(item[key], str) or not item[key].startswith(JsonPointer.FRAGMENT)):
                raise ValueError(SchemaError.LOCAL_REFERENCE)


def _validate_schema_resources(value):
    """Reject ambiguous resource/anchor declarations before a resolver picks one.

    Resolve relative IDs with the same URI joining used by jsonschema. Traverse
    each occurrence, including aliases under different bases. Equality here is
    canonical JSON identity (with the resource's own ID resolved), not an attempt
    to infer equivalence between different validation rules. Nothing is fetched.
    """
    resources, anchors = {}, {}

    def canonical(node, uri):
        definition = {**node, SchemaKeyword.ID: uri} if SchemaKeyword.ID in node else node
        return json.dumps(definition, sort_keys=SchemaReference.ID_SORT_KEYS,
                          allow_nan=JsonObject.ALLOW_NON_FINITE)

    def declare(index, identity, node, path, uri, error):
        if identity in index:
            previous, previous_path = index[identity]
            if canonical(previous, uri) != canonical(node, uri):
                raise ValueError(error.format(identity=identity, first=JsonPointer.reference(previous_path),
                                              second=JsonPointer.reference(path)))
        else:
            index[identity] = node, path

    def visit(node, path, base):
        if not isinstance(node, dict):
            return
        identifier = node.get(SchemaKeyword.ID)
        uri = urldefrag(urljoin(base, identifier)).url if identifier is not None else base
        if not path or identifier is not None:
            declare(resources, uri, node, path, uri, SchemaError.RESOURCE_CONFLICT)
        for keyword in SchemaReference.ANCHOR_KEYS:
            if keyword in node:
                declare(anchors, uri + JsonPointer.FRAGMENT + node[keyword], node, path, uri,
                        SchemaError.ANCHOR_CONFLICT)
        for location, child in _schema_children(node):
            visit(child, (*path, *location), uri)

    visit(value, (), JsonPointer.ROOT)


def validate_schema(value):
    """Validate syntax/local references, allowing schema-owned defaults.

    Successful proofs are content keyed within the active preparation, which may
    be seeded by a registry's last proof set. This never grants runtime authority.
    Configuration schemas retain their stricter checks in checked_schema().
    """
    proofs = _schema_checks.get()
    key = None
    if proofs is not None:
        try:
            JsonObject.validate({"schema": value})
            key = (validate_schema.__name__, json.dumps(value, allow_nan=JsonObject.ALLOW_NON_FINITE))
        except (TypeError, ValueError):
            pass
        if key is not None and key in proofs:
            return
    validate_local_refs(value)
    Draft202012Validator.check_schema(value)
    if key is not None:
        proofs.add(key)


def checked_schema(value):
    """UI 스키마는 JSON이며 외부 스키마를 내려받지 않는다."""
    encoded = json.dumps(value, allow_nan=JsonObject.ALLOW_NON_FINITE)
    proofs = _schema_checks.get()
    key = None
    if proofs is not None:
        try:
            # Python-only extensions must not borrow a proof for their lossy
            # JSON encoding (e.g. tuple/list or integer/string object keys).
            JsonObject.validate({"schema": value})
            key = encoded
        except (TypeError, ValueError):
            pass
        if key is not None and key in proofs:
            return deepcopy(value)
    Draft202012Validator.check_schema(value)
    validate_local_refs(value)
    for item in schema_nodes(value):
        if SchemaKeyword.DEFAULT in item:
            raise ValueError(SchemaError.IMPLICIT_DEFAULT)
    _validate_schema_resources(value)
    if key is not None:
        proofs.add(key)
    return deepcopy(value)


def nest_schema(value, path):
    """Keep an owner's reference resource intact when embedding its schema.

    A nonempty resource $id already establishes that boundary. Anonymous schemas with
    references/anchors get a deterministic hierarchical resource identity instead of
    rewritten pointers: nested $id, anchors and dynamic scope keep their native
    JSON Schema meaning. No reference (or aliased schema node) is mutated.
    The generated URI is schema composition identity, not a configured value or
    a location to fetch; checked_schema still rejects non-local references.
    """
    result = checked_schema(value)
    if (isinstance(result, dict) and result.get(SchemaKeyword.ID) in SchemaReference.ANONYMOUS_IDS
            and any(key in node for node in schema_nodes(result)
                    for key in (*SchemaReference.LOCAL_KEYS, *SchemaReference.RESOURCE_KEYS))):
        identity = json.dumps((tuple(path), result), sort_keys=SchemaReference.ID_SORT_KEYS,
                              allow_nan=JsonObject.ALLOW_NON_FINITE).encode(SchemaReference.ID_ENCODING)
        result[SchemaKeyword.ID] = (SchemaReference.NESTED_ID_PREFIX + hashlib.sha256(identity).hexdigest()
                                    + SchemaReference.NESTED_ID_SUFFIX)
        _validate_schema_resources(result)
    return result


def checked_implementation_schema(value):
    """확장 구현체도 같은 설정 외형을 공개한다. 각 section의 내용은 구현체가 결정한다."""
    spec = checked_schema(value)
    narrowing_paths(spec)
    if not isinstance(spec, dict) or spec.get(SchemaKeyword.TYPE) != SchemaType.OBJECT:
        raise ValueError(SchemaError.IMPLEMENTATION_OBJECT)
    if spec.get(SchemaKeyword.PROPERTIES, {}).keys() - set(ImplementationParameters.SECTIONS):
        raise ValueError(SchemaError.IMPLEMENTATION_FIELDS)
    for name, section in spec.get(SchemaKeyword.PROPERTIES, {}).items():
        if not isinstance(section, dict) or section.get(SchemaKeyword.TYPE) != SchemaType.OBJECT:
            raise ValueError(SchemaError.NON_NULL_SECTION.format(name=name))
    # 외형 제한을 schema에도 담아 ProjectConfig와 UI 검증이 일치하게 한다.
    spec[SchemaKeyword.ADDITIONAL_PROPERTIES] = SchemaBoolean.REJECT
    return spec


def narrowing_paths(schema):
    """Explicit ownership rule: limits live on numeric properties, not branches.

    JSON Schema validation still supports references/composition. Our additional
    cross-scope narrowing contract deliberately does not infer conditional paths
    or expand references. An unsupported declaration fails at registration and
    resolution rather than silently losing its authority constraint.
    """
    paths = []
    mappings = set(SchemaTraversal.MAPPINGS)
    single = set(SchemaTraversal.SINGLE)
    arrays = set(SchemaTraversal.ARRAYS)

    def visit(spec, path=(), direct=SchemaTraversal.DIRECT):
        if not isinstance(spec, dict):
            return
        if SchemaExtension.NARROWING in spec:
            kinds = spec.get(SchemaKeyword.TYPE)
            kinds = {kinds} if isinstance(kinds, str) else set(kinds or ())
            if (not direct or not path or spec[SchemaExtension.NARROWING] != SchemaNarrowing.MAXIMUM
                    or not kinds.intersection(SchemaNarrowing.NUMERIC)
                    or kinds - SchemaNarrowing.NULLABLE_NUMERIC):
                raise ValueError(SchemaError.NARROWING_PATH + JsonPointer.SEPARATOR.join(path))
            paths.append(path)
        for key in mappings:
            for name, child in spec.get(key, {}).items():
                visit(child, (*path, name), direct and key == SchemaKeyword.PROPERTIES)
        for key in single:
            child = spec.get(key)
            if isinstance(child, list):
                for item in child:
                    visit(item, path, SchemaTraversal.INDIRECT)
            else:
                visit(child, path, SchemaTraversal.INDIRECT)
        for key in arrays:
            for child in spec.get(key, []):
                visit(child, path, SchemaTraversal.INDIRECT)

    visit(schema)
    return tuple(paths)
