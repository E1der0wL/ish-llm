"""Loop transcript and output objects inside the common engine output frame."""

from hub.ui.output.worker import RenderSlot

from hub.widget.mouse_contract import MouseBinding


from prompt_toolkit.layout import DynamicContainer
from prompt_toolkit.layout.dimension import Dimension

from ..transcript.conversation import ConversationView
from ..transcript.search import SessionSearch
from ..welcome import welcome_window
from ..output_inspector import SearchableOutputViewport
from ...ui.output import OutputUIContext
from ...config.view_state import ReadingPosition
from .engine import EngineOutput
from .tag_bar import OutputTag
from ..transcript.identity import TranscriptIdentity
from llm.core.models import MessageRole


class LoopOutput(EngineOutput):
    ABSENT = None
    MIN_HEIGHT = 1
    FLEX_WEIGHT = 1
    HEADING_SEPARATOR = "  •  "
    OBJECT_INDENT = 2
    MESSAGE_MARGIN = 1
    ANCHOR_OFFSET = 0
    FOLLOW_TAIL = True
    MANUAL_POSITION = False
    EMPTY_QUERY = ""

    def __init__(self, view):
        self.view = view
        self.transcript = ConversationView(
            view.sessions[view.selected].messages, view.theme, language=view.t
        )
        MouseBinding.set_focus(self.transcript.window, view._focus_composer)
        self.transcript.window.height = Dimension(
            min=LoopOutput.MIN_HEIGHT, weight=LoopOutput.FLEX_WEIGHT
        )
        control = self.transcript.control
        control.render_worker = view.render_worker
        control.on_position_changed = self.position_changed
        control.file_root = view.file_root
        self.renderers = control.renderers
        self.images = control.images
        self.search = SessionSearch(view, self.transcript)
        self.welcome = welcome_window(view)
        super().__init__(
            view,
            DynamicContainer(
                lambda: self.welcome if view.no_sessions else self.transcript
            ),
            heading=self.heading,
            shortcuts=self.search.shortcuts,
        )

    def heading(self):
        session = self.view.sessions[self.view.selected]
        return self.view._focus_heading(
            self.transcript.control,
            session.title + LoopOutput.HEADING_SEPARATOR + session.status,
        )

    @property
    def floating_dialog(self):
        return self.search.dialog if self.search.active else LoopOutput.ABSENT

    @property
    def objects(self):
        return tuple(self.transcript.control.objects)

    def tag_entries(self):
        control = self.transcript.control
        anchors = control.anchor_positions()
        entries = [
            OutputTag(
                item.id,
                item.title,
                item.start_line,
                item.icon,
                item.raw,
                indent=LoopOutput.OBJECT_INDENT,
            )
            for item in control.objects
        ]
        for index, message in enumerate(control.messages):
            identifier = TranscriptIdentity.message(message, index)
            if message.role == MessageRole.USER and identifier in anchors:
                entries.append(
                    OutputTag(
                        identifier,
                        message.text,
                        anchors[identifier],
                        margin_top=LoopOutput.MESSAGE_MARGIN,
                    )
                )
        return tuple(entries)

    def select_tag(self, tag):
        self.restore_position(ReadingPosition(tag.id, LoopOutput.ANCHOR_OFFSET, tag.position))

    def expand_tag(self, tag):
        item = next((item for item in self.objects if item.id == tag.id), LoopOutput.ABSENT)
        if item is LoopOutput.ABSENT:
            return
        view = self.view
        context = OutputUIContext(
            view.t,
            view._rows,
            view._columns,
            view.close_dialog,
            lambda obj: SearchableOutputViewport(obj, self),
            view.render_worker,
        )
        popup = self.renderers.create_interaction(item, context)
        self.inspector = popup
        view.dialogs.show(popup, popup.receiver)

    def set_messages(self, messages, position=ABSENT):
        self.transcript.set_messages(messages, position)

    def update_messages(self, messages, *, auto_scroll=ABSENT):
        control = self.transcript.control
        following = not control.restoring and (
            not control.messages
            or control.top_line + control._height >= len(control._lines) - 1
        )
        changed = control.messages != messages
        control.messages = messages
        if auto_scroll and following and changed and not control.search_query:
            control.follow_tail = LoopOutput.FOLLOW_TAIL
        elif auto_scroll is LoopOutput.MANUAL_POSITION:
            control.follow_tail = LoopOutput.MANUAL_POSITION

    def reading_position(self):
        return self.transcript.control.reading_position()

    def restore_position(self, position):
        self.transcript.control.restore_position(position)

    def set_follow_tail(self, enabled):
        self.transcript.control.follow_tail = enabled

    def set_theme(self, theme):
        self.transcript.control.theme = theme

    def set_file_root(self, path):
        self.transcript.control.file_root = path

    def scroll(self, key):
        self.transcript.control.scroll(key)

    def find(self):
        self.search.open()

    def clear_search(self):
        self.transcript.control.set_search(LoopOutput.EMPTY_QUERY)

    def release(self):
        control = self.transcript.control
        if control._resize_timer:
            control._resize_timer.cancel()
            control._resize_timer = LoopOutput.ABSENT
        self.view.render_worker.cancel(RenderSlot.TRANSCRIPT)
        self.view.render_worker.cancel(RenderSlot.SEARCH)
        self.renderers.close()
