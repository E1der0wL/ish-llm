"""Pure, bounded Workflow diagram preview. Never executes workflow content."""

import json
from ..ui.output.labels import ellipsize
from prompt_toolkit.utils import get_cwidth


def workflow_diagram(text, width=48):
    value = json.loads(text)
    if not isinstance(value, dict) or not isinstance(value.get("nodes"), dict) or not isinstance(value.get("edges"), list):
        raise ValueError("Workflow requires nodes (object) and edges (array)")
    nodes, edges = value["nodes"], value["edges"]
    if len(nodes) > 200 or len(edges) > 400:
        raise ValueError("Preview supports up to 200 nodes and 400 edges")
    names = list(nodes)
    entry = value.get("entry")
    if entry in names:
        names.remove(entry)
        names.insert(0, entry)
    box_width = max(12, min(36, width - 10))
    canvas = [[" "] * (box_width + 8) for _ in range(max(1, len(names) * 5))]
    def put(y, x, text):
        canvas[y][x:x + len(text)] = text
    positions = {name: i * 5 for i, name in enumerate(names)}
    legends = []
    for i, edge in enumerate(edges):
        if not isinstance(edge, dict):
            raise ValueError("Each edge must be an object")
        source, target = edge.get("source"), edge.get("target")
        if source not in positions or target not in positions:
            raise ValueError(f"Unknown edge endpoint: {source} → {target}")
        # Separate outgoing and incoming rows, including a node's self-loop.
        # A node with both kinds must not overwrite its incoming arrow.
        a, b = positions[source] + 2, positions[target] + 1
        lane = i % 5 + 1
        for y in range(min(a, b), max(a, b) + 1):
            canvas[y][lane] = "│"
        for x in range(lane, 8):
            canvas[a][x] = canvas[b][x] = "─"
        canvas[a][lane], canvas[b][lane] = ("┌", "└") if a < b else ("└", "┌")
        canvas[a][7], canvas[b][7] = "◀", "▶"
        legends.append(f"{source} ─{edge.get('port', '')}→ {target}")
    for i, name in enumerate(names):
        node = nodes[name]
        if not isinstance(node, dict):
            raise ValueError(f"Node {name} must be an object")
        caption = ("▶ " if name == entry else "") + name + " · " + str(node.get("type", "?"))
        # Rich/PTK own cell-width clipping; pad by terminal width, not bytes.
        caption = ellipsize(caption, box_width - 2)
        put(i * 5, 8, "┌" + "─" * (box_width - 2) + "┐")
        put(i * 5 + 1, 8, "│" + caption + " " * (box_width - 2 - get_cwidth(caption)) + "│")
        put(i * 5 + 2, 8, "│" + " " * (box_width - 2) + "│")
        put(i * 5 + 3, 8, "└" + "─" * (box_width - 2) + "┘")
    return "\n".join("".join(row).rstrip() for row in canvas) + "\n" + "\n".join(legends)
