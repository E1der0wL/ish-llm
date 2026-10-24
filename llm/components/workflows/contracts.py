"""Workflow document, node, edge and condition fields; handler options remain opaque."""

from .bindings import WorkflowBindings


class WorkflowEdge:
    SOURCE = "source"
    TARGET = "target"
    PORT = "port"


class WorkflowCondition:
    PATH = "path"
    OPERATOR = "op"
    VALUE = "value"


class WorkflowCase:
    PORT = WorkflowEdge.PORT
    WHEN = "when"


class WorkflowNode:
    TYPE = "type"
    METADATA = "metadata"
    PAUSE_BEFORE = "pause_before"
    TIMEOUT_SECONDS = "timeout_seconds"
    CASES = "cases"
    DEFAULT = "default"
    JOIN = "join"
    WAIT = "wait"
    BODY = "body"
    MAX_ITERATIONS = "max_iterations"
    WHILE = "while"
    ON_LIMIT = "on_limit"
    WORKFLOW = "workflow"
    COMMON_FIELDS = {
        TYPE,
        *WorkflowBindings.MAPPINGS,
        *WorkflowBindings.SCHEMAS,
        PAUSE_BEFORE,
        TIMEOUT_SECONDS,
        METADATA,
    }

    def __init__(self, value):
        self._value = value

    @property
    def kind(self):
        return self._value[self.TYPE]

    @property
    def body(self):
        return WorkflowDocument(self._value[self.BODY])

    @property
    def definition(self):
        """The selected handler receives the same validated definition object."""
        return self._value


class WorkflowDocument:
    COMPONENT = "workflows"
    SCHEMA_VERSION = "schema_version"
    ENTRY = "entry"
    NODES = "nodes"
    EDGES = "edges"
    INITIAL_STATE = "initial_state"
    METADATA = WorkflowNode.METADATA
    MANAGED_FIELDS = (SCHEMA_VERSION, ENTRY, NODES, EDGES)

    def __init__(self, value):
        self._value = value

    def iter_nodes(self):
        for value in self._value[self.NODES].values():
            yield WorkflowNode(value)
