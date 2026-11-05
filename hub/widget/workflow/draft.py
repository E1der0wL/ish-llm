"""Lossless local edits, nested graph navigation and authoring undo history."""

from llm.components.workflows.contracts import WorkflowDocument
from llm.components.workflows.contracts import WorkflowEdge
from llm.components.workflows.contracts import WorkflowNode

from copy import deepcopy
from dataclasses import dataclass
from collections.abc import Callable
from typing import Any
from ...ui.input.json_document import JsonDocument


@dataclass(frozen=True, slots=True)
class NodePosition:
    x: int
    y: int


class WorkflowDraft:
    # UI-only metadata and undo budget, never an execution/resource policy.
    INVALID_GRAPH = 'Workflow draft requires nodes and edges'
    INVALID_NODE = 'A node needs a nonempty ID and JSON object'
    MISSING_ENDPOINT = 'Select existing endpoint nodes'
    DUPLICATE_EDGE = 'Duplicate edge'
    INVALID_NODE_VALUE = 'node must be an object; edit JSON to resolve it'
    INVALID_METADATA = 'metadata must be an object; edit JSON to resolve it'
    INVALID_POSITION = 'metadata.hub_graph must be an object; edit JSON to resolve it'
    MISSING_BODY = 'Select a node with an inline Workflow body'
    UI_KEY = 'hub_graph'
    UI_TITLE = 'title'
    UI_POSITION = 'position'
    HISTORY_LENGTH = 80
    COLUMN_GAP = 31
    ROW_GAP = 7
    POSITION_DIMENSIONS = 2
    ORIGIN_X = 2

    def __init__(self, value: dict[str, Any]):
        self.value = deepcopy(value)
        self.path: tuple[str, ...] = ()
        self.revision = 0
        self.undo_stack: list[tuple[dict[str, Any], tuple[str, ...]]] = []
        self.redo_stack: list[tuple[dict[str, Any], tuple[str, ...]]] = []

    def graph(self) -> dict[str, Any]:
        graph = self.value
        for name in self.path:
            graph = graph[WorkflowDocument.NODES][name][WorkflowNode.BODY]
        if not isinstance(graph.get(WorkflowDocument.NODES), dict) or not isinstance(graph.get(WorkflowDocument.EDGES), list):
            raise ValueError(WorkflowDraft.INVALID_GRAPH)
        return graph

    def change(self, edit: Callable[[dict[str, Any]], object]) -> None:
        before = deepcopy(self.value)
        try:
            edit(self.graph())
        except Exception:
            self.value = before
            raise
        if not JsonDocument.same(before, self.value, missing=object()):
            self.undo_stack.append((before, self.path))
            del self.undo_stack[:-self.HISTORY_LENGTH]
            self.redo_stack.clear()
            self.revision += 1

    def replace(self, value: dict[str, Any]) -> None:
        self.change(lambda _: self._replace(value))
        self.path = ()

    def _replace(self, value: dict[str, Any]) -> None:
        self.value = deepcopy(value)

    def history(self, redo: bool = False) -> bool:
        source, target = (self.redo_stack, self.undo_stack) if redo else (self.undo_stack, self.redo_stack)
        if not source:
            return False
        target.append((deepcopy(self.value), self.path))
        self.value, self.path = source.pop()
        self.revision += 1
        return True

    def put_node(self, name: str, node: dict[str, Any]) -> None:
        if not isinstance(name, str) or not name.strip() or not isinstance(node, dict):
            raise ValueError(WorkflowDraft.INVALID_NODE)
        if not JsonDocument.same(self.graph()[WorkflowDocument.NODES].get(name), node, missing=object()):
            self.change(lambda graph: graph[WorkflowDocument.NODES].__setitem__(name, deepcopy(node)))

    def delete_node(self, name: str) -> None:
        # Never silently rewrite entry, branches or dangling edges.
        self.change(lambda graph: graph[WorkflowDocument.NODES].__delitem__(name))

    def add_edge(self, source: str, target: str, port: str) -> None:
        graph = self.graph()
        if source not in graph[WorkflowDocument.NODES] or target not in graph[WorkflowDocument.NODES]:
            raise ValueError(WorkflowDraft.MISSING_ENDPOINT)
        edge = {WorkflowEdge.SOURCE: source, WorkflowEdge.TARGET: target}
        if port:
            edge[WorkflowEdge.PORT] = port
        if edge in graph[WorkflowDocument.EDGES]:
            raise ValueError(WorkflowDraft.DUPLICATE_EDGE)
        self.change(lambda value: value[WorkflowDocument.EDGES].append(edge))

    def delete_edge(self, index: int) -> None:
        self.change(lambda graph: graph[WorkflowDocument.EDGES].pop(index))

    def set_entry(self, name: str) -> None:
        self.change(lambda graph: graph.__setitem__(WorkflowDocument.ENTRY, name))

    def title(self, name: str) -> str:
        node = self.graph()[WorkflowDocument.NODES][name]
        metadata = node.get(WorkflowNode.METADATA, {}) if isinstance(node, dict) else {}
        ui = metadata.get(self.UI_KEY, {}) if isinstance(metadata, dict) else {}
        return str(ui.get(self.UI_TITLE) or name) if isinstance(ui, dict) else name

    def positions(self) -> dict[str, NodePosition]:
        positions = {}
        for index, (name, node) in enumerate(self.graph()[WorkflowDocument.NODES].items()):
            metadata = node.get(WorkflowNode.METADATA, {}) if isinstance(node, dict) else {}
            ui = metadata.get(self.UI_KEY, {}) if isinstance(metadata, dict) else {}
            position = ui.get(self.UI_POSITION) if isinstance(ui, dict) else None
            valid = (isinstance(position, list) and len(position) == self.POSITION_DIMENSIONS
                     and all(type(item) is int and item >= 0 for item in position))
            positions[name] = NodePosition(*position) if valid else NodePosition(self.ORIGIN_X, 1 + index * self.ROW_GAP)
        return positions

    @classmethod
    def metadata(cls, node: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(node, dict):
            raise ValueError(WorkflowDraft.INVALID_NODE_VALUE)
        metadata = node.setdefault(WorkflowNode.METADATA, {})
        if not isinstance(metadata, dict):
            raise ValueError(WorkflowDraft.INVALID_METADATA)
        ui = metadata.setdefault(cls.UI_KEY, {})
        if not isinstance(ui, dict):
            raise ValueError(WorkflowDraft.INVALID_POSITION)
        return ui

    def move(self, name: str, x: int, y: int) -> None:
        def edit(graph):
            self.metadata(graph[WorkflowDocument.NODES][name])[self.UI_POSITION] = [max(0, x), max(0, y)]
        self.change(edit)

    def arrange(self) -> None:
        graph = self.graph()
        nodes = graph[WorkflowDocument.NODES]
        # Bounded breadth-first layout handles even invalid/cyclic drafts.
        ranks, pending = {}, [(graph.get(WorkflowDocument.ENTRY), 0)]
        outgoing = {name: [] for name in nodes}
        for edge in graph[WorkflowDocument.EDGES]:
            if isinstance(edge, dict) and isinstance(edge.get(WorkflowEdge.SOURCE), str) and edge[WorkflowEdge.SOURCE] in outgoing:
                outgoing[edge[WorkflowEdge.SOURCE]].append(edge.get(WorkflowEdge.TARGET))
        cursor = 0
        while cursor < len(pending):
            name, rank = pending[cursor]
            cursor += 1
            if not isinstance(name, str) or name not in nodes or name in ranks:
                continue
            ranks[name] = rank
            pending.extend((target, rank + 1) for target in outgoing[name])
        counts, positions = {}, {}
        for name in nodes:
            rank = ranks.get(name, 0)
            row = counts.get(rank, 0)
            counts[rank] = row + 1
            positions[name] = [self.ORIGIN_X + rank * self.COLUMN_GAP, 1 + row * self.ROW_GAP]
        def edit(value):
            for name, position in positions.items():
                self.metadata(value[WorkflowDocument.NODES][name])[self.UI_POSITION] = position
        self.change(edit)

    def enter(self, name: str) -> None:
        node = self.graph()[WorkflowDocument.NODES][name]
        body = node.get(WorkflowNode.BODY) if isinstance(node, dict) else None
        if not isinstance(body, dict) or not isinstance(body.get(WorkflowDocument.NODES), dict) or not isinstance(body.get(WorkflowDocument.EDGES), list):
            raise ValueError(WorkflowDraft.MISSING_BODY)
        self.path = (*self.path, name)
        self.revision += 1

    def leave(self) -> None:
        self.path = self.path[:-1]
        self.revision += 1
