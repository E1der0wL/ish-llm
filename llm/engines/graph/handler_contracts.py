"""Optional Graph handler hooks; declaration discovery never executes a handler."""

from llm.components.workflows.graph import WorkflowHandlerContract
from llm.engines.contracts import EngineHooks


class GraphHandlerHooks:
    SELECTION_BINDING = "selection_binding"
    PREPARE_CONTEXT = "prepare_context"
    CHECKPOINT_ENGINE = "checkpoint_engine"
    RESUMABLE_CONTAINER = "resumable_container"
    BINDING = "binding"
    VALIDATE_SELECTION = "validate_selection"
    GRAPH_ENGINE = "graph_engine"
    DISCOVER_GRAPH = "discover_graph"
    GRAPH_CONTEXT = "graph_context"
    INSTRUCTION_ENGINE = "instruction_engine"
    VALIDATE = WorkflowHandlerContract.VALIDATE_METHOD
    ADDITIONAL_CAPABILITIES = EngineHooks.ADDITIONAL_CAPABILITIES
    REQUIRED_CAPABILITIES = EngineHooks.REQUIRED_CAPABILITIES
    ABSENT = None
    UNDECLARED = False
    DECLARED = True
    NO_CAPABILITIES = ()

    @classmethod
    def preparer(cls, handler):
        return getattr(handler, cls.PREPARE_CONTEXT, cls.ABSENT)

    @classmethod
    def checkpoint_engine(cls, handler):
        return getattr(handler, cls.CHECKPOINT_ENGINE, cls.ABSENT)

    @classmethod
    def binder(cls, handler):
        return getattr(handler, cls.BINDING, cls.ABSENT)

    @classmethod
    def selection_validator(cls, handler):
        return getattr(handler, cls.VALIDATE_SELECTION, cls.ABSENT)

    @classmethod
    def graph_engine(cls, handler):
        return getattr(handler, cls.GRAPH_ENGINE, cls.ABSENT)

    @classmethod
    def graph_context(cls, handler):
        return getattr(handler, cls.GRAPH_CONTEXT, cls.ABSENT)

    @classmethod
    def instruction_engine(cls, handler):
        return getattr(handler, cls.INSTRUCTION_ENGINE, cls.ABSENT)

    @classmethod
    def validator(cls, handler):
        return getattr(handler, cls.VALIDATE, cls.ABSENT)

    @classmethod
    def discoverer(cls, handler):
        return getattr(handler, cls.ADDITIONAL_CAPABILITIES, cls.ABSENT)

    @classmethod
    def selection_bound(cls, handler):
        return getattr(handler, cls.SELECTION_BINDING, cls.UNDECLARED) is cls.DECLARED

    @classmethod
    def resumable(cls, handler):
        return getattr(handler, cls.RESUMABLE_CONTAINER, cls.UNDECLARED) is cls.DECLARED

    @classmethod
    def graph_discoverer(cls, handler, default):
        return getattr(handler, cls.DISCOVER_GRAPH, default)

    @classmethod
    def required_names(cls, handler):
        return getattr(handler, cls.REQUIRED_CAPABILITIES, cls.NO_CAPABILITIES)
