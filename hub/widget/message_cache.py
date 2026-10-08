"""Reuse unchanged message layouts while rebuilding absolute scroll anchors."""

from dataclasses import replace


class MessageRenderCache:
    def __init__(self):
        self.context = None
        self.items = {}

    def render(self, messages, width, theme, language, *, render, renderers, root, invalidate):
        context = (width, theme, language.code, str(root), renderers.version)
        if context != self.context:
            self.items.clear()
            self.context = context
        if not messages:
            self.items.clear()
            return render((), width, theme, language), [], []
        lines, anchors, objects, retained = [], [], [], {}
        for index, message in enumerate(messages):
            if index and message.role == "user" and messages[index - 1].role in ("assistant", "reasoning"):
                lines.extend([[('', '')], [('', '')]])
            # The label displays tenths; sub-tenth backend timing noise does not
            # require re-rendering otherwise unchanged Markdown.
            display = replace(message, elapsed_seconds=round(message.elapsed_seconds, 1)) if message.elapsed_seconds is not None else message
            identifier = message.id or f"index:{index}"
            entry = self.items.get(identifier)
            if entry is None or entry[0] != display:
                local_anchors, local_objects = [], []
                body = render((display,), width, theme, language, anchors=local_anchors,
                    objects=local_objects, renderers=renderers, root=root, invalidate=invalidate,
                    index_offset=index)
                entry = (display, body, local_anchors, local_objects)
            retained[identifier] = entry
            offset = len(lines)
            lines.extend(entry[1])
            anchors.extend((name, row + offset) for name, row in entry[2])
            objects.extend(replace(obj, start_line=obj.start_line + offset, end_line=obj.end_line + offset)
                           for obj in entry[3])
        self.items = retained
        return lines or [[("", "")]], anchors, objects
