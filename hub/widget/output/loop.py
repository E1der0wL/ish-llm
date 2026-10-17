"""Loop transcript and output objects inside the common engine output frame."""

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


class LoopOutput(EngineOutput):
    def __init__(self, view):
        self.view = view
        self.transcript = ConversationView(view.sessions[view.selected].messages, view.theme, language=view.t)
        self.transcript.window.hub_click_focus = view._focus_composer
        self.transcript.window.height = Dimension(min=1, weight=1)
        control = self.transcript.control
        control.render_worker = view.render_worker
        control.on_position_changed = self.position_changed
        control.file_root = view.file_root
        self.renderers = control.renderers
        self.images = control.images
        self.search = SessionSearch(view, self.transcript)
        self.welcome = welcome_window(view)
        super().__init__(view, DynamicContainer(lambda: self.welcome if view.no_sessions else self.transcript),
                         heading=self.heading, shortcuts=self.search.shortcuts)

    def heading(self):
        session = self.view.sessions[self.view.selected]
        return self.view._focus_heading(self.transcript.control, session.title + "  ·  " + session.status)

    @property
    def floating_dialog(self):
        return self.search.dialog if self.search.active else None

    @property
    def objects(self):
        return tuple(self.transcript.control.objects)

    def tag_entries(self):
        control = self.transcript.control
        anchors = dict(control._anchors)
        entries = [OutputTag(item.id, item.title, item.start_line, item.icon, item.raw, indent=2)
                   for item in control.objects]
        for index, message in enumerate(control.messages):
            identifier = message.id or f"index:{index}"
            if message.role == "user" and identifier in anchors:
                entries.append(OutputTag(identifier, message.text, anchors[identifier], margin_top=1))
        return tuple(entries)

    def select_tag(self, tag):
        self.restore_position(ReadingPosition(tag.id, 0, tag.position))

    def expand_tag(self, tag):
        item = next((item for item in self.objects if item.id == tag.id), None)
        if item is None:
            return
        view = self.view
        context = OutputUIContext(view.t, view._rows, view._columns, view.close_dialog,
                                  lambda obj: SearchableOutputViewport(obj, self), view.render_worker)
        popup = self.renderers.create_interaction(item, context)
        self.inspector = popup
        view.dialogs.show(popup, popup.receiver)

    def set_messages(self, messages, position=None):
        self.transcript.set_messages(messages, position)

    def update_messages(self, messages, *, auto_scroll=None):
        control = self.transcript.control
        following = not control.restoring and (
            not control.messages or control.top_line + control._height >= len(control._lines) - 1)
        changed = control.messages != messages
        control.messages = messages
        if auto_scroll and following and changed and not control.search_query:
            control.follow_tail = True
        elif auto_scroll is False:
            control.follow_tail = False

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
        self.transcript.control.set_search("")

    def release(self):
        control = self.transcript.control
        if control._resize_timer:
            control._resize_timer.cancel()
            control._resize_timer = None
        self.view.render_worker.cancel("transcript")
        self.view.render_worker.cancel("search")
        self.renderers.close()
