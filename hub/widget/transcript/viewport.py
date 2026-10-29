"""Cheap off-screen geometry; Rich and resource rendering stay near the reader."""

from bisect import bisect_right
from collections.abc import Sequence

from ...asset.icon import for_style
from ...ui.output import OutputObject
from ...ui.output.labels import block_title
from .identity import TranscriptAnchor, TranscriptIdentity
from .render_contract import TranscriptLayout


class DeferredRows(Sequence):
    """A height estimate without allocating or rendering off-screen cells."""

    def __init__(self, height):
        self.height = height

    def __len__(self):
        return self.height

    def __getitem__(self, index):
        if isinstance(index, slice):
            return tuple(self[i] for i in range(*index.indices(self.height)))
        if not -self.height <= index < self.height:
            raise IndexError(index)
        return TranscriptLayout.BLANK_LINE


class TranscriptViewport:
    NEIGHBORS = 4
    SMALL_HISTORY = 24

    def __init__(self):
        self._messages = self._starts = None
        self._identifiers = ()
        self._positions = ()
        self._indices = {}

    def select(self, messages, starts, top, height, *, follow=False, pending=None, search=False):
        if search or follow or len(messages) <= self.SMALL_HISTORY:
            # No index is needed in these modes; do not retain a previous Session.
            self._messages = self._starts = None
            self._identifiers = self._positions = ()
            self._indices.clear()
        if search or len(messages) <= self.SMALL_HISTORY:
            return None
        if follow:
            return max(0, len(messages) - self.SMALL_HISTORY), len(messages)
        if self._messages is not messages:
            self._messages = messages
            self._identifiers = tuple(TranscriptIdentity.message(message, index)
                                      for index, message in enumerate(messages))
            self._indices = {identifier: index for index, identifier in enumerate(self._identifiers)}
            self._starts = None
        if pending is not None and pending.message_id:
            target = TranscriptIdentity.message_id(pending.message_id)
            if target in self._indices:
                index = self._indices[target]
                return max(0, index - self.NEIGHBORS), min(len(messages), index + self.SMALL_HISTORY)
        if self._starts is not starts:
            positions, previous = [], 0
            for identifier in self._identifiers:
                previous = max(previous, starts.get(identifier, previous))
                positions.append(previous)
            self._positions, self._starts = tuple(positions), starts
        first = max(0, bisect_right(self._positions, max(0, top - height)) - 1)
        last = min(len(messages), bisect_right(self._positions, top + height * 2) + 1)
        if not starts:
            first, last = 0, self.SMALL_HISTORY
        return max(0, first - self.NEIGHBORS), min(len(messages), max(last, first + self.NEIGHBORS) + self.NEIGHBORS)

    @staticmethod
    def deferred(message, index, blocks, width, theme, renderers):
        from .message_cache import Frame

        inner = TranscriptLayout.inner_width(width)
        height = max(2, message.text.count('\n') + len(message.text) // inner + 3)
        identifier = TranscriptIdentity.message(message, index)
        anchors = [TranscriptAnchor(identifier, 0)]
        objects = []
        icons = for_style(theme.icon_style)
        source_offset, newlines = 0, 0
        for block in blocks:
            block_id = TranscriptIdentity.block(message, index, block.start)
            newlines += message.text.count('\n', source_offset, block.start)
            source_offset = block.start
            row = min(height - 1, newlines + block.start // inner + 1)
            anchors.append(TranscriptAnchor(block_id, row))
            if renderers.is_object(block):
                objects.append(OutputObject(block_id, message.id, block, block_title(block), (),
                    row, min(height, row + max(1, block.text.count('\n'))), icons.for_output(block.kind)))
        return Frame(DeferredRows(height), tuple(anchors), tuple(objects), width)
