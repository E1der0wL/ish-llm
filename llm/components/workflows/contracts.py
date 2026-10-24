"""Workflow document, node, edge and condition fields; handler options remain opaque."""

from .bindings import WorkflowBindings


class WorkflowEdge:
    SOURCE = "source"
    TARGET = "target"
    PORT = "port"

    def __init__(self, value):
        self._value = value

    @property
    def source(self):
        return self._value[self.SOURCE]

    @property
    def target(self):
        return self._value[self.TARGET]

    @property
    def port(self):
        return self._value[self.PORT]


class WorkflowCondition:
    PATH = "path"
    OPERATOR = "op"
    VALUE = "value"

    def __init__(self, value):
        self._value = value

    @property
    def path(self):
        return self._value[self.PATH]

    @property
    def operator(self):
        return self._value[self.OPERATOR]

    @property
    def value(self):
        return self._value[self.VALUE]


class WorkflowCase:
    PORT = WorkflowEdge.PORT
    WHEN = "when"

    def __init__(self, value):
        self._value = value

    @property
    def port(self):
        return self._value[self.PORT]

    @property
    def condition(self):
        return self._value[self.WHEN]


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
    NO_PAUSE = False
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

    @property
    def cases(self):
        return self._value[self.CASES]

    @property
    def default_port(self):
        return self._value[self.DEFAULT]

    @property
    def loop_condition(self):
        return self._value.get(self.WHILE)

    @property
    def max_iterations(self):
        return self._value[self.MAX_ITERATIONS]

    @property
    def on_limit(self):
        return self._value[self.ON_LIMIT]

    @property
    def pause_before(self):
        return self._value.get(self.PAUSE_BEFORE, self.NO_PAUSE)

    @property
    def body_definition(self):
        return self._value[self.BODY]

    @property
    def join(self):
        return self._value[self.JOIN]

    @property
    def workflow(self):
        return self._value[self.WORKFLOW]

    @property
    def timeout_seconds(self):
        return self._value.get(self.TIMEOUT_SECONDS)


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

    @property
    def entry(self):
        return self._value[self.ENTRY]

    @property
    def nodes(self):
        return self._value[self.NODES]

    @property
    def edges(self):
        return self._value[self.EDGES]

    @property
    def initial_state(self):
        return self._value.get(self.INITIAL_STATE, {})
