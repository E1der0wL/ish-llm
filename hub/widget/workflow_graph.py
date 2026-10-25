"""Pure, bounded Workflow diagram preview. Never executes workflow content."""

import json
from llm.components.workflows.contracts import (
    WorkflowDocument,
    WorkflowNode,
    WorkflowEdge,
)
from typing import NamedTuple
from ..ui.output.labels import ellipsize
from prompt_toolkit.utils import get_cwidth


class Diagram:
    DEFAULT_WIDTH = 48
    MAX_NODES = 200
    MAX_EDGES = 400
    MIN_BOX_WIDTH = 12
    MAX_BOX_WIDTH = 36
    OUTER_MARGIN = 10
    ORIGIN_X = 8
    NODE_HEIGHT = 5
    OUTGOING_ROW = 2
    INCOMING_ROW = 1
    LOWER_BLANK_ROW = 2
    LOWER_BORDER_ROW = 3
    BORDER_WIDTH = 2
    LANES = 5
    FIRST_LANE = 1
    MIN_HEIGHT = 1
    EMPTY = ""
    SPACE = " "
    NEWLINE = "\n"
    VERTICAL = "│"
    HORIZONTAL = "─"
    TOP_LEFT = "┌"
    BOTTOM_LEFT = "└"
    TOP_RIGHT = "┐"
    BOTTOM_RIGHT = "┘"
    OUTGOING = "◀"
    INCOMING = "▶"
    ENTRY_MARK = "▶ "
    CAPTION_SEPARATOR = " · "
    UNKNOWN_KIND = "?"
    INVALID_GRAPH = "Workflow requires nodes (object) and edges (array)"
    TOO_LARGE = "Preview supports up to 200 nodes and 400 edges"
    INVALID_EDGE = "Each edge must be an object"
    UNKNOWN_ENDPOINT = "Unknown edge endpoint: {source} → {target}"
    INVALID_NODE = "Node {name} must be an object"
    LEGEND = "{source} ─{port}→ {target}"


class PreviewGraph(NamedTuple):
    nodes: dict
    edges: list
    entry: object

    @classmethod
    def read(cls, value):
        if (
            not isinstance(value, dict)
            or not isinstance(value.get(WorkflowDocument.NODES), dict)
            or not isinstance(value.get(WorkflowDocument.EDGES), list)
        ):
            raise ValueError(Diagram.INVALID_GRAPH)
        nodes, edges = value[WorkflowDocument.NODES], value[WorkflowDocument.EDGES]
        if len(nodes) > Diagram.MAX_NODES or len(edges) > Diagram.MAX_EDGES:
            raise ValueError(Diagram.TOO_LARGE)
        return cls(nodes, edges, value.get(WorkflowDocument.ENTRY))


def workflow_diagram(text, width=Diagram.DEFAULT_WIDTH, *, states=None):
    graph = PreviewGraph.read(json.loads(text))
    nodes, edges, entry = graph
    names = list(nodes)
    if entry in names:
        names.remove(entry)
        names.insert(0, entry)
    box_width = max(
        Diagram.MIN_BOX_WIDTH, min(Diagram.MAX_BOX_WIDTH, width - Diagram.OUTER_MARGIN)
    )
    canvas = [
        [Diagram.SPACE] * (box_width + Diagram.ORIGIN_X)
        for _ in range(max(Diagram.MIN_HEIGHT, len(names) * Diagram.NODE_HEIGHT))
    ]

    def put(y, x, text):
        canvas[y][x : x + len(text)] = text

    positions = {name: i * Diagram.NODE_HEIGHT for i, name in enumerate(names)}
    legends = []
    for i, edge in enumerate(edges):
        if not isinstance(edge, dict):
            raise ValueError(Diagram.INVALID_EDGE)
        source, target = edge.get(WorkflowEdge.SOURCE), edge.get(WorkflowEdge.TARGET)
        if source not in positions or target not in positions:
            raise ValueError(
                Diagram.UNKNOWN_ENDPOINT.format(source=source, target=target)
            )
        # Separate outgoing and incoming rows, including a node's self-loop.
        # A node with both kinds must not overwrite its incoming arrow.
        a, b = (
            positions[source] + Diagram.OUTGOING_ROW,
            positions[target] + Diagram.INCOMING_ROW,
        )
        lane = i % Diagram.LANES + Diagram.FIRST_LANE
        for y in range(min(a, b), max(a, b) + 1):
            canvas[y][lane] = Diagram.VERTICAL
        for x in range(lane, Diagram.ORIGIN_X):
            canvas[a][x] = canvas[b][x] = Diagram.HORIZONTAL
        canvas[a][lane], canvas[b][lane] = (
            (Diagram.TOP_LEFT, Diagram.BOTTOM_LEFT)
            if a < b
            else (Diagram.BOTTOM_LEFT, Diagram.TOP_LEFT)
        )
        canvas[a][Diagram.ORIGIN_X - 1], canvas[b][Diagram.ORIGIN_X - 1] = (
            Diagram.OUTGOING,
            Diagram.INCOMING,
        )
        legends.append(
            Diagram.LEGEND.format(
                source=source,
                port=edge.get(WorkflowEdge.PORT, Diagram.EMPTY),
                target=target,
            )
        )
    for i, name in enumerate(names):
        node = nodes[name]
        if not isinstance(node, dict):
            raise ValueError(Diagram.INVALID_NODE.format(name=name))
        caption = (
            (Diagram.ENTRY_MARK if name == entry else Diagram.EMPTY)
            + name
            + Diagram.CAPTION_SEPARATOR
            + str(node.get(WorkflowNode.TYPE, Diagram.UNKNOWN_KIND))
        )
        if states and name in states:
            caption = states[name] + Diagram.SPACE + caption
        # Rich/PTK own cell-width clipping; pad by terminal width, not bytes.
        caption = ellipsize(caption, box_width - Diagram.BORDER_WIDTH)
        put(
            i * Diagram.NODE_HEIGHT,
            Diagram.ORIGIN_X,
            Diagram.TOP_LEFT
            + Diagram.HORIZONTAL * (box_width - Diagram.BORDER_WIDTH)
            + Diagram.TOP_RIGHT,
        )
        put(
            i * Diagram.NODE_HEIGHT + Diagram.INCOMING_ROW,
            Diagram.ORIGIN_X,
            Diagram.VERTICAL
            + caption
            + Diagram.SPACE * (box_width - Diagram.BORDER_WIDTH - get_cwidth(caption))
            + Diagram.VERTICAL,
        )
        put(
            i * Diagram.NODE_HEIGHT + Diagram.LOWER_BLANK_ROW,
            Diagram.ORIGIN_X,
            Diagram.VERTICAL
            + Diagram.SPACE * (box_width - Diagram.BORDER_WIDTH)
            + Diagram.VERTICAL,
        )
        put(
            i * Diagram.NODE_HEIGHT + Diagram.LOWER_BORDER_ROW,
            Diagram.ORIGIN_X,
            Diagram.BOTTOM_LEFT
            + Diagram.HORIZONTAL * (box_width - Diagram.BORDER_WIDTH)
            + Diagram.BOTTOM_RIGHT,
        )
    return (
        Diagram.NEWLINE.join(Diagram.EMPTY.join(row).rstrip() for row in canvas)
        + Diagram.NEWLINE
        + Diagram.NEWLINE.join(legends)
    )
