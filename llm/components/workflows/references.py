"""Workflow-owned traversal of retained action references; handlers own action meaning."""

from llm.core.resource_references import AgentUse, AgentReferenceReader
from .contracts import WorkflowDocument
from .graph import ControlNodeType, WorkflowHandlerContract


class WorkflowAgentReferences:
    ABSENT = None
    NON_ACTIONS = (
        ControlNodeType.BRANCH,
        ControlNodeType.PARALLEL,
        ControlNodeType.JOIN,
        ControlNodeType.END,
        ControlNodeType.WORKFLOW,
    )
    UNAVAILABLE = "Agent may be referenced; Workflow handler owner is unavailable"

    @classmethod
    def collect(cls, component, project, engines):
        handlers = {}
        for name in engines.names():
            declare = WorkflowHandlerContract.declaration(engines.get(name))
            if declare is not cls.ABSENT:
                for kind, handler in declare().items():
                    handlers.setdefault(kind, []).append(handler)

        def selected(graph):
            for node in graph.iter_nodes():
                kind = node.kind
                if kind == ControlNodeType.LOOP:
                    yield from selected(node.body)
                elif kind not in cls.NON_ACTIONS:
                    if kind not in handlers:
                        raise ValueError(cls.UNAVAILABLE)
                    for handler in handlers[kind]:
                        reader = AgentReferenceReader.from_owner(handler)
                        if reader is not cls.ABSENT:
                            yield from reader(node.definition)

        return [
            AgentUse(agent, identifier).to_dict()
            for identifier, graph in component.list(project).items()
            for agent in sorted(set(selected(WorkflowDocument(graph))))
        ]
