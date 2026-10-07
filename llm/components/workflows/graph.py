"""분기·병렬·반복을 표현하는 버전 1 그래프 문서와 순수 구조 검증. 실행 기능은 없다."""

from collections import deque
from copy import deepcopy
from typing import Optional
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from llm.components.base import Component
from llm.core.schema import object_schema, open_schema, metadata_schema, field
from .bindings import pointer_parts, validate_contract


COMMON_NODE_FIELDS = {"type", "inputs", "outputs", "input_schema", "output_schema",
                      "pause_before", "resume_schema", "timeout_seconds", "metadata"}


def workflow_schema():
    """제어 구조는 닫고 action 확장은 선택된 handler가 실행 전에 검증한다."""
    contract = {name: object_schema(additionalProperties=field("string")) for name in ("inputs", "outputs")}
    contract.update({name: {"anyOf": [field("boolean"), open_schema("Workflow value contract author", category="schema")]}
                     for name in ("input_schema", "output_schema", "resume_schema")})
    common = {**contract, "type": field("string", minLength=1), "metadata": metadata_schema(),
              "pause_before": field("boolean"), "timeout_seconds": field(["number", "null"], exclusiveMinimum=0)}
    condition = object_schema({"path": field("string"), "op": field("string", enum=["eq", "ne", "lt", "le", "gt", "ge", "in", "exists"]),
                               "value": {"x-schema-owner": "Workflow condition literal", "x-open-kind": "data"}}, required=["path", "op"])
    fields = {
        "branch": {"cases": field("array", items=object_schema({"port": field("string"), "when": condition}, required=["port", "when"])), "default": field("string")},
        "parallel": {"join": field("string")}, "join": {"wait": field("string", enum=["all"])},
        "loop": {"body": {"$ref": "#"}, "max_iterations": field("integer", minimum=1), "while": condition,
                 "on_limit": field("string", enum=["continue", "fail"])},
        "end": {}, "workflow": {"workflow": field("string", minLength=1)},
    }
    variants = [object_schema({**common, **options, "type": {"const": kind}}, required=["type"])
                for kind, options in fields.items()]
    variants.append(open_schema("selected Workflow action handler", category="implementation", properties={
        **common, "type": field("string", minLength=1, **{"not": {"enum": list(fields)}})}, required=["type"]))
    return object_schema({"schema_version": {"const": 1}, "entry": field("string", minLength=1),
        "nodes": object_schema(additionalProperties={"oneOf": variants}, minProperties=1),
        "edges": field("array", items=object_schema({"source": field("string"), "target": field("string"), "port": field("string")}, required=["source", "target"])),
        "initial_state": open_schema("Workflow state/input author", category="data"),
        **{key: value for key, value in contract.items() if key != "resume_schema"},
        "metadata": metadata_schema()}, required=["schema_version", "entry", "nodes", "edges"])


def validate_handler_options(node, handler):
    """Graph는 handler 필드의 의미를 모른다. 선택 구현체의 스키마에 위임한다."""
    describe = getattr(handler, "configuration_schema", None)
    if describe is None and callable(getattr(handler, "validate", None)):
        return  # 기존 validate(node, context)가 자신의 추가 필드 계약을 소유한다.
    schema = describe() if describe is not None else object_schema()
    Draft202012Validator.check_schema(schema)
    values = {key: value for key, value in node.items() if key not in COMMON_NODE_FIELDS}
    try:
        Draft202012Validator(schema).validate(values)
    except ValidationError as error:
        path = "/".join(map(str, error.absolute_path))
        raise ValueError(f"Workflow handler {node['type']}/{path}: {error.message}") from error


def _text(value, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _condition(value) -> None:
    """조건은 JSON Pointer와 비교 연산 데이터다. Python 표현식을 저장/평가하지 않는다."""
    if not isinstance(value, dict):
        raise ValueError("Condition must be an object")
    path = value.get("path")
    pointer_parts(path)
    op = value.get("op")
    if op not in ("eq", "ne", "lt", "le", "gt", "ge", "in", "exists"):
        raise ValueError("Unsupported condition operator")
    if op != "exists" and "value" not in value:
        raise ValueError("Condition requires value")
    if op == "in" and not isinstance(value["value"], list):
        raise ValueError("Condition 'in' requires an array")


def validate_graph(data: dict, *, _depth: int = 0) -> None:
    """닫힌 그래프 구조와 도달성·연결·합류·반복 상한을 검사한다."""
    if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("Workflow requires schema_version 1")
    if _depth == 0:
        Component.serialize(data)  # 런타임 객체와 비유한 수는 그래프에도 저장할 수 없다.
        errors = list(Draft202012Validator(workflow_schema()).iter_errors(data))
        if errors:
            error = errors[0]
            path = "/".join(map(str, error.absolute_path)) or "workflow"
            raise ValueError(f"{path}: {error.message}") from error
    validate_contract(data)
    if _depth and any(key in data for key in ("inputs", "outputs", "input_schema", "output_schema")):
        raise ValueError("Loop bodies share parent state; put bindings on their action nodes")
    nodes, edges = data.get("nodes"), data.get("edges")
    if not isinstance(nodes, dict) or not nodes or not isinstance(edges, list):
        raise ValueError("Workflow requires nonempty nodes object and edges array")
    entry = _text(data.get("entry"), "entry")
    if entry not in nodes:
        raise ValueError("Workflow entry references an unknown node")
    outgoing = {name: [] for name in nodes}
    incoming = {name: [] for name in nodes}
    for name, node in nodes.items():
        _text(name, "Node ID")
        if not isinstance(node, dict):
            raise ValueError("Node must be an object")
        _text(node.get("type"), "Node type")
        if node["type"] == "workflow":
            _text(node.get("workflow"), "Workflow reference")
        if "pause_before" in node and type(node["pause_before"]) is not bool:
            raise ValueError("pause_before must be boolean")
        validate_contract(node)
        if node["type"] in ("branch", "parallel", "join", "loop", "end") and any(
                key in node for key in ("inputs", "outputs", "input_schema", "output_schema")):
            raise ValueError("Bindings belong to action nodes, not control nodes")
    seen = set()
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError("Edge must be an object")
        source, target = _text(edge.get("source"), "source"), _text(edge.get("target"), "target")
        if source not in nodes or target not in nodes:
            raise ValueError("Edge references an unknown node")
        port = edge.get("port")
        if "port" in edge:
            _text(port, "port")
        key = (source, target, port)
        if key in seen:
            raise ValueError("Duplicate workflow edge")
        seen.add(key)
        outgoing[source].append(edge)
        incoming[target].append(source)
    if incoming[entry]:
        raise ValueError("Workflow entry cannot have incoming edges")

    # 자유로운 역방향 간선 대신 loop.body를 사용한다. 반복 범위와 종료 조건이 명확해진다.
    counts = {name: len(items) for name, items in incoming.items()}
    ready = deque(name for name, count in counts.items() if count == 0)
    ordered = []
    while ready:
        name = ready.popleft()
        ordered.append(name)
        for edge in outgoing[name]:
            counts[edge["target"]] -= 1
            if counts[edge["target"]] == 0:
                ready.append(edge["target"])
    if len(ordered) != len(nodes):
        raise ValueError("Cycles must be expressed as bounded loop nodes")
    reachable = {entry}
    for name in ordered:
        if name in reachable:
            reachable.update(edge["target"] for edge in outgoing[name])
    if reachable != set(nodes):
        raise ValueError("Workflow contains unreachable nodes")

    joins = {}
    for name, node in nodes.items():
        kind, links = node["type"], outgoing[name]
        if kind == "end":
            if links:
                raise ValueError("End node cannot have outgoing edges")
        elif kind == "branch":
            cases = node.get("cases")
            if not isinstance(cases, list) or not cases:
                raise ValueError("Branch requires ordered cases")
            ports = []
            for case in cases:
                if not isinstance(case, dict):
                    raise ValueError("Branch case must be an object")
                ports.append(_text(case.get("port"), "Case port"))
                _condition(case.get("when"))
            ports.append(_text(node.get("default"), "Default port"))
            actual = [edge.get("port") for edge in links]
            if len(set(ports)) != len(ports) or sorted(actual, key=str) != sorted(ports):
                raise ValueError("Branch requires one edge for each distinct case/default port")
        elif kind == "parallel":
            join = _text(node.get("join"), "Parallel join")
            if join not in nodes or nodes[join]["type"] != "join" or join in joins:
                raise ValueError("Parallel requires its own join node")
            joins[join] = name
            if len(links) < 2 or len({edge["target"] for edge in links}) != len(links):
                raise ValueError("Parallel requires at least two distinct branches")
            if any("port" in edge for edge in links):
                raise ValueError("Parallel edges do not use condition ports")
        else:
            if len(links) != 1 or "port" in links[0]:
                raise ValueError("Action, join and loop nodes require one unlabelled successor")
            if kind == "join" and node.get("wait", "all") != "all":
                raise ValueError("Join supports wait='all'")
            if kind == "loop":
                limit = node.get("max_iterations")
                if type(limit) is not int or limit < 1:
                    raise ValueError("Loop requires positive integer max_iterations")
                if "while" in node:
                    _condition(node["while"])
                if node.get("on_limit") not in ("continue", "fail"):
                    raise ValueError("Loop requires on_limit='continue' or 'fail'")
                validate_graph(node.get("body"), _depth=_depth + 1)

    for name, node in nodes.items():
        if node["type"] == "join" and name not in joins:
            raise ValueError("Join must belong to a parallel node")
    for join, fork in joins.items():
        # 모든 경로가 지정된 join에 도달해야 한다. 조기 종료나 외부 진입은 교착의 원인이 된다.
        reaches_join = {join}
        for name in reversed(ordered):
            if name != join and outgoing[name] and all(
                    edge["target"] in reaches_join for edge in outgoing[name]):
                reaches_join.add(name)
        branch_regions = []
        for edge in outgoing[fork]:
            start = edge["target"]
            if start not in reaches_join or start == join:
                raise ValueError("Every parallel branch must reach its join")
            region, pending = set(), [start]
            while pending:
                name = pending.pop()
                if name == join or name in region:
                    continue
                region.add(name)
                pending.extend(item["target"] for item in outgoing[name])
            if any(region & previous for previous in branch_regions):
                raise ValueError("Parallel branches must not merge before their join")
            branch_regions.append(region)
        region = set().union(*branch_regions)
        for name in region | {join}:
            if any(parent not in region | {fork} for parent in incoming[name]):
                raise ValueError("Parallel region cannot have external incoming edges")


class WorkflowGraph:
    """검증된 JSON 노드와 연결을 단계적으로 조립하는 빌더."""

    def __init__(self, *, entry: str, **fields) -> None:
        # 미완성 nodes/edges는 to_dict에서 검증하되, 최상위 키는 생성 즉시 검증한다.
        # 사용자 확장은 metadata 객체에만 속한다. 저장 스키마를 그대로 재사용한다.
        properties = {key: value for key, value in workflow_schema()["properties"].items()
                      if key not in {"schema_version", "entry", "nodes", "edges"}}
        Component.serialize(fields)
        try:
            Draft202012Validator(object_schema(properties)).validate(fields)
        except ValidationError as error:
            raise ValueError(f"Workflow constructor: {error.message}") from error
        self._data = {**deepcopy(fields), "schema_version": 1,
                      "entry": _text(entry, "entry"), "nodes": {}, "edges": []}

    def node(self, identifier: str, kind: str, **options) -> "WorkflowGraph":
        """공통/제어 구조는 to_dict, action 고유 필드는 선택 handler가 검증한다."""
        _text(identifier, "Node ID")
        _text(kind, "Node type")
        if identifier in self._data["nodes"] or "type" in options:
            raise ValueError("Duplicate node or reserved type option")
        self._data["nodes"][identifier] = {"type": kind, **deepcopy(options)}
        return self

    def connect(self, source: str, target: str, *, port: Optional[str] = None) -> "WorkflowGraph":
        """노드 간 연결을 추가한다. branch에서만 port로 조건별 경로를 지정한다."""
        edge = {"source": source, "target": target}
        if port is not None:
            edge["port"] = port
        self._data["edges"].append(edge)
        return self

    def to_dict(self) -> dict:
        """완성된 그래프를 검증하여 저장 가능한 독립 dict로 반환한다."""
        validate_graph(self._data)
        return deepcopy(self._data)

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowGraph":
        validate_graph(data)
        graph = cls(entry=data["entry"])
        graph._data = deepcopy(data)
        return graph
