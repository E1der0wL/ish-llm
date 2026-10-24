"""Graph-owned payload declarations, Writers and projections; no storage or policy defaults."""

from .configuration import GraphRegistration, GraphParameters


class GraphExecutionBinding:
    """Graph execution identity snapshot; nested Tool bindings remain opaque."""

    WORKFLOW = "workflow"
    WORKFLOW_ID = "workflow_id"
    DEFINITION = "definition"
    PROJECT_CONFIG = "project_config"
    SESSION_CONFIG = "session_config"
    REVISION = GraphRegistration.REVISION
    HANDLERS = GraphRegistration.HANDLERS
    CONFIG_KEYS = GraphRegistration.CONFIG_KEYS
    MAX_STEPS = GraphParameters.MAX_STEPS
    MAX_PARALLELISM = GraphParameters.MAX_PARALLELISM
    MAX_NESTED_DEPTH = GraphParameters.MAX_NESTED_DEPTH
    TIMEOUT_SECONDS = GraphParameters.TIMEOUT_SECONDS
    CLEANUP_TIMEOUT = GraphParameters.CLEANUP_TIMEOUT
    HANDLER_BINDINGS = "handler_bindings"
    NESTED_GRAPHS = "nested_graphs"
    COMPONENTS = "components"
    BINDINGS = "bindings"

    def __init__(self, value):
        self._value = value

    @property
    def workflow_id(self):
        return self._value[self.WORKFLOW_ID]

    @property
    def revision(self):
        return self._value[self.REVISION]

    @classmethod
    def create(
        cls,
        workflow,
        workflow_id,
        revision,
        handler_bindings,
        nested_graphs,
        handlers,
        max_steps,
        max_parallelism,
        timeout_seconds,
        project_config,
        session_config,
        components,
        extension,
    ):
        return {
            cls.WORKFLOW: workflow,
            cls.WORKFLOW_ID: workflow_id,
            cls.REVISION: revision,
            cls.HANDLER_BINDINGS: handler_bindings,
            cls.NESTED_GRAPHS: nested_graphs,
            cls.HANDLERS: handlers,
            cls.MAX_STEPS: max_steps,
            cls.MAX_PARALLELISM: max_parallelism,
            cls.TIMEOUT_SECONDS: timeout_seconds,
            cls.PROJECT_CONFIG: project_config,
            cls.SESSION_CONFIG: session_config,
            cls.COMPONENTS: components,
            **extension,
        }

    @classmethod
    def handler_payload(cls, bindings):
        return {cls.BINDINGS: bindings}

    @classmethod
    def nested(
        cls,
        workflow_id,
        definition,
        project_config,
        revision,
        handlers,
        max_steps,
        max_parallelism,
        max_nested_depth,
        timeout_seconds,
        cleanup_timeout,
        config_keys,
    ):
        return {
            cls.WORKFLOW_ID: workflow_id,
            cls.DEFINITION: definition,
            cls.PROJECT_CONFIG: project_config,
            cls.REVISION: revision,
            cls.HANDLERS: handlers,
            cls.MAX_STEPS: max_steps,
            cls.MAX_PARALLELISM: max_parallelism,
            cls.MAX_NESTED_DEPTH: max_nested_depth,
            cls.TIMEOUT_SECONDS: timeout_seconds,
            cls.CLEANUP_TIMEOUT: cleanup_timeout,
            cls.CONFIG_KEYS: config_keys,
        }


class GraphEnvironment:
    """Selected Graph handler environment; explicit Agent options remain unmodified."""

    WORKFLOW = "workflow"
    REVISION = GraphRegistration.REVISION
    PARAMETER_KEY = "parameter_key"
    OPTIONS = "options"
    HANDLERS = GraphRegistration.HANDLERS

    def __init__(self, value):
        self._value = value

    @classmethod
    def create(cls, workflow, revision, parameter_key, options, handlers):
        return {
            cls.WORKFLOW: workflow,
            cls.REVISION: revision,
            cls.PARAMETER_KEY: parameter_key,
            cls.OPTIONS: options,
            cls.HANDLERS: handlers,
        }
