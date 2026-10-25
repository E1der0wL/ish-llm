"""분기·병렬·반복을 표현하는 버전 1 그래프 문서와 순수 구조 검증. 실행 기능은 없다."""

from collections import deque
from enum import StrEnum
from copy import deepcopy
from typing import Optional
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from llm.components.base import Component
from llm.core.schema import (
    object_schema,
    open_schema,
    metadata_schema,
    field,
    SchemaKeyword,
    SchemaType,
    SchemaExtension,
    OpenSchemaKind,
)
from llm.core.json_data import JsonPointer
from .contracts import (
    WorkflowDocument,
    WorkflowNode,
    WorkflowEdge,
    WorkflowCondition,
    WorkflowCase,
)
from .bindings import WorkflowBindings
from .bindings import pointer_parts, validate_contract


class ControlNodeType(StrEnum):
    BRANCH = "branch"
    PARALLEL = "parallel"
    JOIN = "join"
    LOOP = "loop"
    END = "end"
    WORKFLOW = "workflow"

    @classmethod
    def structural(cls):
        return frozenset(cls) - {cls.WORKFLOW}


class ConditionOperator(StrEnum):
    EQ = "eq"
    NE = "ne"
    LT = "lt"
    LE = "le"
    GT = "gt"
    GE = "ge"
    IN = "in"
    EXISTS = "exists"


class JoinWait(StrEnum):
    ALL = "all"


class LoopLimit(StrEnum):
    CONTINUE = "continue"
    FAIL = "fail"


COMMON_NODE_FIELDS = WorkflowNode.COMMON_FIELDS


class WorkflowValidation:
    ABSENT = None
    ROOT_LABEL = "workflow"
    ROOT_DEPTH = 0
    FIRST_ERROR = 0
    READY_INDEGREE = 0
    SINGLE_SUCCESSOR_COUNT = 1
    SINGLE_EDGE_INDEX = 0
    EDGE_DECREMENT = 1
    MINIMUM_ITERATIONS = 1
    NESTED_DEPTH_INCREMENT = 1
    MINIMUM_BRANCHES = 2
    NONEMPTY_COUNT = 1
    POSITIVE_TIMEOUT = 0
    VALUE_SCHEMA_OWNER = "Workflow value contract author"
    CONDITION_SCHEMA_OWNER = "Workflow condition literal"
    ACTION_SCHEMA_OWNER = "selected Workflow action handler"
    STATE_SCHEMA_OWNER = "Workflow state/input author"
    INVALID_CONDITION = "Condition must be an object"
    INVALID_OPERATOR = "Unsupported condition operator"
    MISSING_CONDITION_VALUE = "Condition requires value"
    INVALID_MEMBERSHIP = "Condition 'in' requires an array"
    BODY_BINDING = "Loop bodies share parent state; put bindings on their action nodes"
    INVALID_COLLECTIONS = "Workflow requires nonempty nodes object and edges array"
    UNKNOWN_ENTRY = "Workflow entry references an unknown node"
    NODE_ID_LABEL = "Node ID"
    NODE_TYPE_LABEL = "Node type"
    INVALID_NODE = "Node must be an object"
    WORKFLOW_REFERENCE_LABEL = "Workflow reference"
    INVALID_PAUSE = "pause_before must be boolean"
    CONTROL_BINDING = "Bindings belong to action nodes, not control nodes"
    INVALID_EDGE = "Edge must be an object"
    UNKNOWN_ENDPOINT = "Edge references an unknown node"
    DUPLICATE_EDGE = "Duplicate workflow edge"
    ENTRY_INCOMING = "Workflow entry cannot have incoming edges"
    UNBOUNDED_CYCLE = "Cycles must be expressed as bounded loop nodes"
    UNREACHABLE = "Workflow contains unreachable nodes"
    END_OUTGOING = "End node cannot have outgoing edges"
    INVALID_CASES = "Branch requires ordered cases"
    INVALID_CASE = "Branch case must be an object"
    CASE_PORT_LABEL = "Case port"
    DEFAULT_PORT_LABEL = "Default port"
    BRANCH_EDGES = "Branch requires one edge for each distinct case/default port"
    PARALLEL_JOIN_LABEL = "Parallel join"
    PARALLEL_JOIN = "Parallel requires its own join node"
    PARALLEL_BRANCHES = "Parallel requires at least two distinct branches"
    PARALLEL_PORT = "Parallel edges do not use condition ports"
    SINGLE_SUCCESSOR = "Action, join and loop nodes require one unlabelled successor"
    JOIN_WAIT = "Join supports wait='all'"
    LOOP_BOUND = "Loop requires positive integer max_iterations"
    LOOP_LIMIT = "Loop requires on_limit='continue' or 'fail'"
    ORPHAN_JOIN = "Join must belong to a parallel node"
    UNREACHABLE_JOIN = "Every parallel branch must reach its join"
    EARLY_MERGE = "Parallel branches must not merge before their join"
    REGION_INCOMING = "Parallel region cannot have external incoming edges"
    DUPLICATE_NODE = "Duplicate node or reserved type option"
    HANDLER_ERROR = "Workflow handler {node_type}/{path}: {message}"
    INVALID_TEXT = "{label} must be a nonempty string"
    INVALID_VERSION = "Workflow requires schema_version {version}"
    SCHEMA_ERROR = "{path}: {message}"
    CONSTRUCTOR_ERROR = "Workflow constructor: {message}"


class WorkflowHandlerContract:
    DESCRIBE_CONFIG_METHOD = "describe_config"
    VALIDATE_METHOD = "validate"
    DECLARE_METHOD = "workflow_handlers"
    ABSENT = None

    @classmethod
    def describe_snapshot(cls, handler):
        return (
            deepcopy(handler.describe_config())
            if callable(getattr(handler, cls.DESCRIBE_CONFIG_METHOD, cls.ABSENT))
            else cls.ABSENT
        )

    @classmethod
    def declaration(cls, engine):
        return getattr(engine, cls.DECLARE_METHOD, cls.ABSENT)


def workflow_schema():
    """제어 구조는 닫고 action 확장은 선택된 handler가 실행 전에 검증한다."""
    contract = {
        name: object_schema(additionalProperties=field(SchemaType.STRING))
        for name in (WorkflowBindings.INPUTS, WorkflowBindings.OUTPUTS)
    }
    contract.update(
        {
            name: {
                SchemaKeyword.ANY_OF: [
                    field(SchemaType.BOOLEAN),
                    open_schema(
                        WorkflowValidation.VALUE_SCHEMA_OWNER,
                        category=OpenSchemaKind.SCHEMA,
                    ),
                ]
            }
            for name in (
                WorkflowBindings.INPUT_SCHEMA,
                WorkflowBindings.OUTPUT_SCHEMA,
                WorkflowBindings.RESUME_SCHEMA,
            )
        }
    )
    common = {
        **contract,
        WorkflowNode.TYPE: field(
            SchemaType.STRING, minLength=WorkflowValidation.NONEMPTY_COUNT
        ),
        WorkflowNode.METADATA: metadata_schema(),
        WorkflowNode.PAUSE_BEFORE: field(SchemaType.BOOLEAN),
        WorkflowNode.TIMEOUT_SECONDS: field(
            [SchemaType.NUMBER, SchemaType.NULL],
            exclusiveMinimum=WorkflowValidation.POSITIVE_TIMEOUT,
        ),
    }
    condition = object_schema(
        {
            WorkflowCondition.PATH: field(SchemaType.STRING),
            WorkflowCondition.OPERATOR: field(
                SchemaType.STRING, enum=list(ConditionOperator)
            ),
            WorkflowCondition.VALUE: {
                SchemaExtension.OWNER: WorkflowValidation.CONDITION_SCHEMA_OWNER,
                SchemaExtension.OPEN_KIND: OpenSchemaKind.DATA,
            },
        },
        required=[WorkflowCondition.PATH, WorkflowCondition.OPERATOR],
    )
    fields = {
        ControlNodeType.BRANCH: {
            WorkflowNode.CASES: field(
                SchemaType.ARRAY,
                items=object_schema(
                    {
                        WorkflowEdge.PORT: field(SchemaType.STRING),
                        WorkflowCase.WHEN: condition,
                    },
                    required=[WorkflowEdge.PORT, WorkflowCase.WHEN],
                ),
            ),
            WorkflowNode.DEFAULT: field(SchemaType.STRING),
        },
        ControlNodeType.PARALLEL: {WorkflowNode.JOIN: field(SchemaType.STRING)},
        ControlNodeType.JOIN: {
            WorkflowNode.WAIT: field(SchemaType.STRING, enum=list(JoinWait))
        },
        ControlNodeType.LOOP: {
            WorkflowNode.BODY: {SchemaKeyword.REF: JsonPointer.FRAGMENT},
            WorkflowNode.MAX_ITERATIONS: field(
                SchemaType.INTEGER, minimum=WorkflowValidation.NONEMPTY_COUNT
            ),
            WorkflowNode.WHILE: condition,
            WorkflowNode.ON_LIMIT: field(SchemaType.STRING, enum=list(LoopLimit)),
        },
        ControlNodeType.END: {},
        ControlNodeType.WORKFLOW: {
            WorkflowNode.WORKFLOW: field(
                SchemaType.STRING, minLength=WorkflowValidation.NONEMPTY_COUNT
            )
        },
    }
    variants = [
        object_schema(
            {**common, **options, WorkflowNode.TYPE: {SchemaKeyword.CONST: kind}},
            required=[WorkflowNode.TYPE],
        )
        for kind, options in fields.items()
    ]
    variants.append(
        open_schema(
            WorkflowValidation.ACTION_SCHEMA_OWNER,
            category=OpenSchemaKind.IMPLEMENTATION,
            properties={
                **common,
                WorkflowNode.TYPE: field(
                    SchemaType.STRING,
                    minLength=WorkflowValidation.NONEMPTY_COUNT,
                    **{SchemaKeyword.NOT: {SchemaKeyword.ENUM: list(fields)}},
                ),
            },
            required=[WorkflowNode.TYPE],
        )
    )
    return object_schema(
        {
            WorkflowDocument.SCHEMA_VERSION: {
                SchemaKeyword.CONST: WorkflowGraph.SCHEMA_VERSION
            },
            WorkflowDocument.ENTRY: field(
                SchemaType.STRING, minLength=WorkflowValidation.NONEMPTY_COUNT
            ),
            WorkflowDocument.NODES: object_schema(
                additionalProperties={SchemaKeyword.ONE_OF: variants},
                minProperties=WorkflowValidation.NONEMPTY_COUNT,
            ),
            WorkflowDocument.EDGES: field(
                SchemaType.ARRAY,
                items=object_schema(
                    {
                        WorkflowEdge.SOURCE: field(SchemaType.STRING),
                        WorkflowEdge.TARGET: field(SchemaType.STRING),
                        WorkflowEdge.PORT: field(SchemaType.STRING),
                    },
                    required=[WorkflowEdge.SOURCE, WorkflowEdge.TARGET],
                ),
            ),
            WorkflowDocument.INITIAL_STATE: open_schema(
                WorkflowValidation.STATE_SCHEMA_OWNER, category=OpenSchemaKind.DATA
            ),
            **{
                key: value
                for key, value in contract.items()
                if key != WorkflowBindings.RESUME_SCHEMA
            },
            WorkflowNode.METADATA: metadata_schema(),
        },
        required=list(WorkflowGraph.MANAGED_FIELDS),
    )


def validate_handler_options(node, handler):
    """Graph는 handler 필드의 의미를 모른다. 선택 구현체의 스키마에 위임한다."""
    describe = getattr(
        handler,
        WorkflowHandlerContract.DESCRIBE_CONFIG_METHOD,
        WorkflowValidation.ABSENT,
    )
    if describe is WorkflowValidation.ABSENT and callable(
        getattr(
            handler, WorkflowHandlerContract.VALIDATE_METHOD, WorkflowValidation.ABSENT
        )
    ):
        return
    schema = (
        describe() if describe is not WorkflowValidation.ABSENT else object_schema()
    )
    Draft202012Validator.check_schema(schema)
    values = {
        key: value for key, value in node.items() if key not in COMMON_NODE_FIELDS
    }
    try:
        Draft202012Validator(schema).validate(values)
    except ValidationError as error:
        path = JsonPointer.SEPARATOR.join(map(str, error.absolute_path))
        raise ValueError(
            WorkflowValidation.HANDLER_ERROR.format(
                node_type=node[WorkflowNode.TYPE], path=path, message=error.message
            )
        ) from error


def _text(value, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(WorkflowValidation.INVALID_TEXT.format(label=label))
    return value


def _condition(value) -> None:
    """조건은 JSON Pointer와 비교 연산 데이터다. Python 표현식을 저장/평가하지 않는다."""
    if not isinstance(value, dict):
        raise ValueError(WorkflowValidation.INVALID_CONDITION)
    path = value.get(WorkflowCondition.PATH)
    pointer_parts(path)
    op = value.get(WorkflowCondition.OPERATOR)
    if op not in tuple(ConditionOperator):
        raise ValueError(WorkflowValidation.INVALID_OPERATOR)
    if op != ConditionOperator.EXISTS and WorkflowCondition.VALUE not in value:
        raise ValueError(WorkflowValidation.MISSING_CONDITION_VALUE)
    if op == ConditionOperator.IN and (
        not isinstance(value[WorkflowCondition.VALUE], list)
    ):
        raise ValueError(WorkflowValidation.INVALID_MEMBERSHIP)


def validate_graph(data: dict, *, _depth: int = WorkflowValidation.ROOT_DEPTH) -> None:
    """닫힌 그래프 구조와 도달성·연결·합류·반복 상한을 검사한다."""
    if (
        not isinstance(data, dict)
        or type(data.get(WorkflowDocument.SCHEMA_VERSION)) is not int
        or data[WorkflowDocument.SCHEMA_VERSION] != WorkflowGraph.SCHEMA_VERSION
    ):
        raise ValueError(
            WorkflowValidation.INVALID_VERSION.format(
                version=WorkflowGraph.SCHEMA_VERSION
            )
        )
    if _depth == WorkflowValidation.ROOT_DEPTH:
        Component.serialize(data)
        errors = list(Draft202012Validator(workflow_schema()).iter_errors(data))
        if errors:
            error = errors[WorkflowValidation.FIRST_ERROR]
            path = (
                JsonPointer.SEPARATOR.join(map(str, error.absolute_path))
                or WorkflowValidation.ROOT_LABEL
            )
            raise ValueError(
                WorkflowValidation.SCHEMA_ERROR.format(path=path, message=error.message)
            ) from error
    validate_contract(data)
    if _depth and any(
        (
            key in data
            for key in (
                WorkflowBindings.INPUTS,
                WorkflowBindings.OUTPUTS,
                WorkflowBindings.INPUT_SCHEMA,
                WorkflowBindings.OUTPUT_SCHEMA,
            )
        )
    ):
        raise ValueError(WorkflowValidation.BODY_BINDING)
    nodes, edges = (data.get(WorkflowDocument.NODES), data.get(WorkflowDocument.EDGES))
    if not isinstance(nodes, dict) or not nodes or (not isinstance(edges, list)):
        raise ValueError(WorkflowValidation.INVALID_COLLECTIONS)
    entry = _text(data.get(WorkflowDocument.ENTRY), WorkflowDocument.ENTRY)
    if entry not in nodes:
        raise ValueError(WorkflowValidation.UNKNOWN_ENTRY)
    outgoing = {name: [] for name in nodes}
    incoming = {name: [] for name in nodes}
    for name, node in nodes.items():
        _text(name, WorkflowValidation.NODE_ID_LABEL)
        if not isinstance(node, dict):
            raise ValueError(WorkflowValidation.INVALID_NODE)
        _text(node.get(WorkflowNode.TYPE), WorkflowValidation.NODE_TYPE_LABEL)
        if node[WorkflowNode.TYPE] == ControlNodeType.WORKFLOW:
            _text(
                node.get(WorkflowNode.WORKFLOW),
                WorkflowValidation.WORKFLOW_REFERENCE_LABEL,
            )
        if (
            WorkflowNode.PAUSE_BEFORE in node
            and type(node[WorkflowNode.PAUSE_BEFORE]) is not bool
        ):
            raise ValueError(WorkflowValidation.INVALID_PAUSE)
        validate_contract(node)
        if node[WorkflowNode.TYPE] in ControlNodeType.structural() and any(
            (
                key in node
                for key in (
                    WorkflowBindings.INPUTS,
                    WorkflowBindings.OUTPUTS,
                    WorkflowBindings.INPUT_SCHEMA,
                    WorkflowBindings.OUTPUT_SCHEMA,
                )
            )
        ):
            raise ValueError(WorkflowValidation.CONTROL_BINDING)
    seen = set()
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError(WorkflowValidation.INVALID_EDGE)
        source, target = (
            _text(edge.get(WorkflowEdge.SOURCE), WorkflowEdge.SOURCE),
            _text(edge.get(WorkflowEdge.TARGET), WorkflowEdge.TARGET),
        )
        if source not in nodes or target not in nodes:
            raise ValueError(WorkflowValidation.UNKNOWN_ENDPOINT)
        port = edge.get(WorkflowEdge.PORT)
        if WorkflowEdge.PORT in edge:
            _text(port, WorkflowEdge.PORT)
        key = (source, target, port)
        if key in seen:
            raise ValueError(WorkflowValidation.DUPLICATE_EDGE)
        seen.add(key)
        outgoing[source].append(edge)
        incoming[target].append(source)
    if incoming[entry]:
        raise ValueError(WorkflowValidation.ENTRY_INCOMING)
    # Detect cycles before reachability; bounded loop bodies are the only repetition syntax.
    counts = {name: len(items) for name, items in incoming.items()}
    ready = deque(
        (
            name
            for name, count in counts.items()
            if count == WorkflowValidation.READY_INDEGREE
        )
    )
    ordered = []
    while ready:
        name = ready.popleft()
        ordered.append(name)
        for edge in outgoing[name]:
            counts[edge[WorkflowEdge.TARGET]] -= WorkflowValidation.EDGE_DECREMENT
            if counts[edge[WorkflowEdge.TARGET]] == WorkflowValidation.READY_INDEGREE:
                ready.append(edge[WorkflowEdge.TARGET])
    if len(ordered) != len(nodes):
        raise ValueError(WorkflowValidation.UNBOUNDED_CYCLE)
    reachable = {entry}
    for name in ordered:
        if name in reachable:
            reachable.update((edge[WorkflowEdge.TARGET] for edge in outgoing[name]))
    if reachable != set(nodes):
        raise ValueError(WorkflowValidation.UNREACHABLE)
    joins = {}
    for name, node in nodes.items():
        kind, links = (node[WorkflowNode.TYPE], outgoing[name])
        if kind == ControlNodeType.END:
            if links:
                raise ValueError(WorkflowValidation.END_OUTGOING)
        elif kind == ControlNodeType.BRANCH:
            cases = node.get(WorkflowNode.CASES)
            if not isinstance(cases, list) or not cases:
                raise ValueError(WorkflowValidation.INVALID_CASES)
            ports = []
            for case in cases:
                if not isinstance(case, dict):
                    raise ValueError(WorkflowValidation.INVALID_CASE)
                ports.append(
                    _text(
                        case.get(WorkflowEdge.PORT), WorkflowValidation.CASE_PORT_LABEL
                    )
                )
                _condition(case.get(WorkflowCase.WHEN))
            ports.append(
                _text(
                    node.get(WorkflowNode.DEFAULT),
                    WorkflowValidation.DEFAULT_PORT_LABEL,
                )
            )
            actual = [edge.get(WorkflowEdge.PORT) for edge in links]
            if len(set(ports)) != len(ports) or sorted(actual, key=str) != sorted(
                ports
            ):
                raise ValueError(WorkflowValidation.BRANCH_EDGES)
        elif kind == ControlNodeType.PARALLEL:
            join = _text(
                node.get(WorkflowNode.JOIN), WorkflowValidation.PARALLEL_JOIN_LABEL
            )
            if (
                join not in nodes
                or nodes[join][WorkflowNode.TYPE] != ControlNodeType.JOIN
                or join in joins
            ):
                raise ValueError(WorkflowValidation.PARALLEL_JOIN)
            joins[join] = name
            if len(links) < WorkflowValidation.MINIMUM_BRANCHES or len(
                {edge[WorkflowEdge.TARGET] for edge in links}
            ) != len(links):
                raise ValueError(WorkflowValidation.PARALLEL_BRANCHES)
            if any((WorkflowEdge.PORT in edge for edge in links)):
                raise ValueError(WorkflowValidation.PARALLEL_PORT)
        else:
            if (
                len(links) != WorkflowValidation.SINGLE_SUCCESSOR_COUNT
                or WorkflowEdge.PORT in links[WorkflowValidation.SINGLE_EDGE_INDEX]
            ):
                raise ValueError(WorkflowValidation.SINGLE_SUCCESSOR)
            if (
                kind == ControlNodeType.JOIN
                and node.get(WorkflowNode.WAIT, JoinWait.ALL) != JoinWait.ALL
            ):
                raise ValueError(WorkflowValidation.JOIN_WAIT)
            if kind == ControlNodeType.LOOP:
                limit = node.get(WorkflowNode.MAX_ITERATIONS)
                if (
                    type(limit) is not int
                    or limit < WorkflowValidation.MINIMUM_ITERATIONS
                ):
                    raise ValueError(WorkflowValidation.LOOP_BOUND)
                if WorkflowNode.WHILE in node:
                    _condition(node[WorkflowNode.WHILE])
                if node.get(WorkflowNode.ON_LIMIT) not in tuple(LoopLimit):
                    raise ValueError(WorkflowValidation.LOOP_LIMIT)
                validate_graph(
                    node.get(WorkflowNode.BODY),
                    _depth=_depth + WorkflowValidation.NESTED_DEPTH_INCREMENT,
                )
    for name, node in nodes.items():
        if node[WorkflowNode.TYPE] == ControlNodeType.JOIN and name not in joins:
            raise ValueError(WorkflowValidation.ORPHAN_JOIN)
    for join, fork in joins.items():
        # Every branch must reach its own join with no early merge or external entry.
        reaches_join = {join}
        for name in reversed(ordered):
            if (
                name != join
                and outgoing[name]
                and all(
                    (
                        edge[WorkflowEdge.TARGET] in reaches_join
                        for edge in outgoing[name]
                    )
                )
            ):
                reaches_join.add(name)
        branch_regions = []
        for edge in outgoing[fork]:
            start = edge[WorkflowEdge.TARGET]
            if start not in reaches_join or start == join:
                raise ValueError(WorkflowValidation.UNREACHABLE_JOIN)
            region, pending = (set(), [start])
            while pending:
                name = pending.pop()
                if name == join or name in region:
                    continue
                region.add(name)
                pending.extend((item[WorkflowEdge.TARGET] for item in outgoing[name]))
            if any((region & previous for previous in branch_regions)):
                raise ValueError(WorkflowValidation.EARLY_MERGE)
            branch_regions.append(region)
        region = set().union(*branch_regions)
        for name in region | {join}:
            if any((parent not in region | {fork} for parent in incoming[name])):
                raise ValueError(WorkflowValidation.REGION_INCOMING)


class WorkflowGraph:
    """검증된 JSON 노드와 연결을 단계적으로 조립하는 빌더."""

    SCHEMA_VERSION = 1
    MANAGED_FIELDS = WorkflowDocument.MANAGED_FIELDS

    def __init__(self, *, entry: str, **fields) -> None:
        properties = {
            key: value
            for key, value in workflow_schema()[SchemaKeyword.PROPERTIES].items()
            if key not in WorkflowGraph.MANAGED_FIELDS
        }
        Component.serialize(fields)
        try:
            Draft202012Validator(object_schema(properties)).validate(fields)
        except ValidationError as error:
            raise ValueError(
                WorkflowValidation.CONSTRUCTOR_ERROR.format(message=error.message)
            ) from error
        self._data = {
            **deepcopy(fields),
            WorkflowDocument.SCHEMA_VERSION: WorkflowGraph.SCHEMA_VERSION,
            WorkflowDocument.ENTRY: _text(entry, WorkflowDocument.ENTRY),
            WorkflowDocument.NODES: {},
            WorkflowDocument.EDGES: [],
        }

    def node(self, identifier: str, kind: str, **options) -> "WorkflowGraph":
        """공통/제어 구조는 to_dict, action 고유 필드는 선택 handler가 검증한다."""
        _text(identifier, WorkflowValidation.NODE_ID_LABEL)
        _text(kind, WorkflowValidation.NODE_TYPE_LABEL)
        if (
            identifier in self._data[WorkflowDocument.NODES]
            or WorkflowNode.TYPE in options
        ):
            raise ValueError(WorkflowValidation.DUPLICATE_NODE)
        self._data[WorkflowDocument.NODES][identifier] = {
            WorkflowNode.TYPE: kind,
            **deepcopy(options),
        }
        return self

    def connect(
        self,
        source: str,
        target: str,
        *,
        port: Optional[str] = WorkflowValidation.ABSENT
    ) -> "WorkflowGraph":
        """노드 간 연결을 추가한다. branch에서만 port로 조건별 경로를 지정한다."""
        edge = {WorkflowEdge.SOURCE: source, WorkflowEdge.TARGET: target}
        if port is not WorkflowValidation.ABSENT:
            edge[WorkflowEdge.PORT] = port
        self._data[WorkflowDocument.EDGES].append(edge)
        return self

    def to_dict(self) -> dict:
        """완성된 그래프를 검증하여 저장 가능한 독립 dict로 반환한다."""
        validate_graph(self._data)
        return deepcopy(self._data)

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowGraph":
        validate_graph(data)
        graph = cls(entry=data[WorkflowDocument.ENTRY])
        graph._data = deepcopy(data)
        return graph
