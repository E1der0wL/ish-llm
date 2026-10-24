"""LangGraph's private frame schema and value Writers; no persisted checkpoint state."""

from typing import Annotated, TypedDict
from .errors import GraphExecutionError


class GraphFrame:
    FRAME_SCHEMA_NAME = "_Frame"
    LOOP_SCHEMA_NAME = "_LoopFrame"
    PARALLEL_SCHEMA_NAME = "_ParallelFrame"
    EMPTY_PORT = ""
    INITIAL_COUNT = 0
    COUNT_INCREMENT = 1
    DUPLICATE_BRANCH = "Duplicate parallel branch result"

    class Field:
        DATA = "data"
        PATH = "path"
        PORT = "port"
        SCOPE = "scope"
        COUNT = "count"
        BRANCHES = "branches"

    def __init__(self, value):
        self._value = value

    @property
    def data(self):
        return self._value[self.Field.DATA]

    @property
    def path(self):
        return self._value[self.Field.PATH]

    @property
    def port(self):
        return self._value[self.Field.PORT]

    @property
    def scope(self):
        return self._value[self.Field.SCOPE]

    @property
    def count(self):
        return self._value[self.Field.COUNT]

    @property
    def branches(self):
        return self._value[self.Field.BRANCHES]

    @classmethod
    def create(cls, data, path, port, scope):
        return {cls.Field.DATA: data, cls.Field.PATH: path, cls.Field.PORT: port, cls.Field.SCOPE: scope}

    @classmethod
    def parallel(cls, data, path, branches, scope):
        return {
            cls.Field.DATA: data,
            cls.Field.PATH: path,
            cls.Field.BRANCHES: branches,
            cls.Field.SCOPE: scope,
        }

    @classmethod
    def loop(cls, data, path, count, scope):
        return {cls.Field.DATA: data, cls.Field.PATH: path, cls.Field.COUNT: count, cls.Field.SCOPE: scope}

    @classmethod
    def branch_result(cls, start, data):
        return {cls.Field.BRANCHES: {start: data}}

    @classmethod
    def loop_result(cls, data, count):
        return {cls.Field.DATA: data, cls.Field.COUNT: count}

    @classmethod
    def publish_branches(cls, state, branches):
        state[cls.Field.BRANCHES] = branches


def _merge_branches(left: dict, right: dict) -> dict:
    if left.keys() & right.keys():
        raise GraphExecutionError(GraphFrame.DUPLICATE_BRANCH)
    return {**left, **right}


# LangGraph receives exactly the original TypedDict fields and reducer annotation.
# The field names above are also used by every frame Writer and query.
_Frame = TypedDict(
    GraphFrame.FRAME_SCHEMA_NAME,
    {
        GraphFrame.Field.DATA: dict,
        GraphFrame.Field.PATH: str,
        GraphFrame.Field.PORT: str,
        GraphFrame.Field.SCOPE: list,
    },
)
_LoopFrame = TypedDict(
    GraphFrame.LOOP_SCHEMA_NAME,
    {
        GraphFrame.Field.DATA: dict,
        GraphFrame.Field.PATH: str,
        GraphFrame.Field.COUNT: int,
        GraphFrame.Field.SCOPE: list,
    },
)
_ParallelFrame = TypedDict(
    GraphFrame.PARALLEL_SCHEMA_NAME,
    {
        GraphFrame.Field.DATA: dict,
        GraphFrame.Field.PATH: str,
        GraphFrame.Field.BRANCHES: Annotated[dict, _merge_branches],
        GraphFrame.Field.SCOPE: list,
    },
)


class LangGraphInvocation:
    MODULE = "langgraph.graph"
    RECURSION_LIMIT = "recursion_limit"
    RECURSION_FACTOR = 3
    RECURSION_OVERHEAD = 10
    ABSENT = None
    NO_RETRY = None
    CHECKPOINTER = False
    LOOP_GATE = "gate"
    LOOP_BODY = "body"
    NODE_IDENTIFIER = "node_{index}"
    BRANCH_IDENTIFIER = "branch_{index}"

    @classmethod
    def options(cls, engine):
        return (
            {}
            if engine.max_steps is cls.ABSENT
            else {cls.RECURSION_LIMIT: engine.max_steps * cls.RECURSION_FACTOR + cls.RECURSION_OVERHEAD}
        )

    @classmethod
    def node_identifier(cls, index):
        return cls.NODE_IDENTIFIER.format(index=index)

    @classmethod
    def branch_identifier(cls, index):
        return cls.BRANCH_IDENTIFIER.format(index=index)
