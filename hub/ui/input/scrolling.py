"""Resolve Alt+movement inside the active Hub surface without moving focus."""

from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout import Window
from prompt_toolkit.layout.containers import to_container
from prompt_toolkit.layout.controls import BufferControl, DummyControl
from prompt_toolkit.layout.layout import walk
from prompt_toolkit.layout.menus import CompletionsMenuControl
from prompt_toolkit.formatted_text.utils import fragment_list_width

from ...widget.controls import ScrollablePane
from ...widget.dialog import Dialog
from ...widget.mouse import window_scroll
from ...widget.mouse_contract import ControlRole
from ...widget.sidebar import SidebarList


class ScrollNavigation:
    """The focused widget wins, followed by the current pane or popup body."""

    def __init__(self, view, context):
        self.view, self.context = view, context

    @staticmethod
    def overflows(window):
        info = window.render_info
        if not info or min(info.window_height, info.window_width) <= 0:
            return False
        if info.content_height > info.window_height:
            return True
        # Only inspect rows that fit in this viewport, including a single long
        # wrapped line. A long list never requires measuring its whole body.
        content = info.ui_content
        if window.wrap_lines():
            return sum(content.get_height_for_line(i, info.window_width, window.get_line_prefix)
                       for i in range(content.line_count)) > info.window_height
        return any(fragment_list_width(content.get_line(i)) > info.window_width
                   for i in range(content.line_count))

    def popup_body(self, dialog):
        target = Dialog.scroll_target_for(dialog)
        if target is not None:
            return target
        # A passive preview cannot acquire focus. The action buttons scroll it;
        # nested/retained dialogs and hidden tabs must never receive these keys.
        body = dialog.popup_body if isinstance(dialog, Dialog) else dialog
        if body is None:
            return None
        for part in walk(to_container(body), skip_hidden=True):
            if isinstance(part, ScrollablePane):
                return part
            if (isinstance(part, Window) and not isinstance(part.content, DummyControl)
                    and not part.content.is_focusable()
                    and self.overflows(part)):
                return part
        return None

    def target(self):
        view = self.view
        if not view.visible:
            return None
        layout = get_app().layout
        window = layout.current_window
        control = window.content
        if isinstance(control, BufferControl) and control.buffer.complete_state:
            menu = next((w for w in layout.visible_windows
                         if isinstance(w.content, CompletionsMenuControl)), None)
            if menu is not None:
                return menu
        dialog = view._dialog
        if isinstance(control, SidebarList) or getattr(control, ControlRole.FIELD, None) == ControlRole.CHOICES:
            if dialog or view.settings_open or view.output.modal_container or self.overflows(window):
                return window
        if dialog is not None:
            if isinstance(control, BufferControl) and self.overflows(window):
                return window
            parent = layout.get_parent(window)
            while parent is not None:
                if isinstance(parent, ScrollablePane):
                    return parent
                parent = layout.get_parent(parent)
            return self.popup_body(dialog)
        if view.output.modal_container is not None:
            sidebar = view.output.participants if view.output.participants.visible else view.output.tags
            return sidebar.window
        if view.settings_open:
            if self.context.settings and not self.context.sidebar:
                if isinstance(control, BufferControl) and self.overflows(window):
                    return window
                return view.settings.main
            return None
        if self.context.chat:
            if self.context.composing and view.question.item:
                return view.question.details
            return view.output
        return None

    def available(self):
        return self.target() is not None

    def handle(self, event):
        target = self.target()
        if isinstance(target, Window):
            target = window_scroll(target)
        if target is not None:
            target.scroll(event.key_sequence[-1].key)
            event.app.invalidate()
