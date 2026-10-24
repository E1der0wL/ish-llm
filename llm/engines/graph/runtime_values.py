"""Graph runtime identity and prepared traversal values, separate from persisted records."""

import json
from typing import NamedTuple, Any
from llm.core.steering import SteeringRoute


class GraphScope:
    WORKFLOW = "workflow"
    BRANCH = "branch"
    ENSURE_ASCII = False
    SEPARATORS = (",", ":")
    PATH_SEPARATOR = "/"
    NODE_PATH = "{path}/{name}"
    LOOP_PATH = "{path}[{count}]"

    @classmethod
    def key(cls, scope):
        return json.dumps(scope, ensure_ascii=cls.ENSURE_ASCII, separators=cls.SEPARATORS)

    @classmethod
    def route_key(cls, workflow, scope):
        return SteeringRoute.encode_path([workflow, *(v for v in scope if isinstance(v, str))])

    @classmethod
    def node_path(cls, path, name):
        return cls.NODE_PATH.format(path=path, name=name)

    @classmethod
    def loop_path(cls, path, count):
        return cls.LOOP_PATH.format(path=path, count=count)


class PreparedGraph(NamedTuple):
    engine: Any
    document: dict | None
    context: Any
    selected: bool


class GraphPreparation:
    SELECTED = True
    STRUCTURAL = False
    ABSENT = None
    MINIMUM_DEPTH = 0
    DEPTH_INCREMENT = 1
    ONE_DEFINITION = 1


class GraphExecutionObservation:
    ROOT_STEP = "graph"
    NODE_STEP = "graph_node"
    NESTED_STEP = "subgraph"
    RUNTIME = "langgraph"
    STATE_FIELD = "graph"

    @classmethod
    def publish(cls, state, output):
        state[cls.STATE_FIELD] = output
