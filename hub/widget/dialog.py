"""Shared popup defaults and shortcut context for the page footer."""

from prompt_toolkit.widgets import Dialog as PTKDialog, Box
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.keys import Keys
from prompt_toolkit.application.current import get_app
from prompt_toolkit.layout.containers import to_container, Window, HSplit, DynamicContainer
from prompt_toolkit.layout.layout import walk
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.filters import Condition, to_filter
from prompt_toolkit.layout import ConditionalContainer
from enum import StrEnum

from ..ui.input.registry import ShortcutRegistry
from .actions import ActionBar, bind_button_navigation


class DialogContext:
    DIALOG = "dialog"
    READER = "reader"


class PopupSize(StrEnum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class PopupGeometry:
    MARGIN = 4
    SMALL = (60, 9)
    MEDIUM = (100, 36)

    @classmethod
    def dimensions(cls, size):
        terminal = get_app().output.get_size()
        available = (max(1, terminal.columns - cls.MARGIN),
                     max(1, terminal.rows - cls.MARGIN))
        preferred = (cls.SMALL if size == PopupSize.SMALL else
                     cls.MEDIUM if size == PopupSize.MEDIUM else available)
        return tuple(Dimension.exact(min(want, limit))
                     for want, limit in zip(preferred, available))


class PopupInput:
    """Shared modal key receiver for popups whose body cannot take focus."""

    EMPTY = ""
    FOCUSABLE = True
    SHOW_CURSOR = False
    MODAL = True
    NO_ACTION = None

    def __init__(self):
        self.shortcuts = ShortcutRegistry()
        self.shortcuts.bindings.add(Keys.Any)(lambda event: self.NO_ACTION)
        self.receiver = FormattedTextControl(
            self.EMPTY,
            focusable=self.FOCUSABLE,
            show_cursor=self.SHOW_CURSOR,
            key_bindings=self.shortcuts.bindings,
            modal=self.MODAL,
        )
        self.shortcuts.attach(self.receiver)


class PopupBody(DynamicContainer):
    """Allocate all body space without letting a short widget shrink the border."""

    def preferred_height(self, width, max_available_height):
        natural = super().preferred_height(width, max_available_height)
        return Dimension(min=natural.min, preferred=natural.preferred)

    def write_to_screen(self, screen, mouse_handlers, write_position, parent_style, erase_bg, z_index):
        content = to_container(self.get_container())
        if isinstance(content, Window) and content.height is None:
            content.dont_extend_height = to_filter(False)
        super().write_to_screen(screen, mouse_handlers, write_position, parent_style, erase_bg, z_index)


class Dialog(PTKDialog):
    CONTENT_PADDING = 1
    STATUS_PADDING = 1
    navigation = None
    NAVIGATION = "navigation"
    ABSENT_HOOK = None
    CLOSE_HANDLER = "on_close"
    CANCEL_HANDLER = "on_cancel"
    DISPOSER = "dispose"
    OUTPUT_SURFACE = "output"
    WITH_BACKGROUND = "with_background"
    MODAL = "modal"
    BUTTONS = "buttons"
    DEFAULT_BACKGROUND = False
    DEFAULT_MODAL = False

    @classmethod
    def navigation_for(cls, dialog):
        return getattr(dialog, cls.NAVIGATION, None)

    @classmethod
    def close_handler(cls, dialog):
        return getattr(dialog, cls.CLOSE_HANDLER, cls.ABSENT_HOOK)

    @classmethod
    def cancel_handler(cls, dialog):
        return getattr(dialog, cls.CANCEL_HANDLER, cls.ABSENT_HOOK)

    @classmethod
    def dispose_resources(cls, dialog):
        from .output.text_viewport import TextViewport

        dispose = getattr(dialog, cls.DISPOSER, cls.ABSENT_HOOK)
        if dispose:
            dispose()
        TextViewport.dispose_resize(
            getattr(dialog, cls.OUTPUT_SURFACE, cls.ABSENT_HOOK)
        )

    def __init__(self, *args, shortcut_context=DialogContext.DIALOG,
                 size=PopupSize.MEDIUM, content_padding=CONTENT_PADDING,
                 status_bar=None, status_in_body=False, **kwargs):
        self.shortcut_context = shortcut_context
        self.requested_size = PopupSize(size)
        self.popup_body = kwargs.get("body", args[0] if args else None)
        self.status_bar = status_bar
        self.status_in_body = status_in_body
        if "width" in kwargs or "height" in kwargs:
            raise TypeError("Dialog dimensions are owned by PopupSize")
        kwargs.setdefault(self.WITH_BACKGROUND, self.DEFAULT_BACKGROUND)
        kwargs.setdefault(self.MODAL, self.DEFAULT_MODAL)
        buttons = kwargs.pop(self.BUTTONS, None) or []
        content = PopupBody(lambda: self.body)
        body = Box(content,
                   padding=content_padding, padding_bottom=0)
        if buttons:
            body = HSplit([
                body,
                Box(ActionBar(buttons, lambda: PopupGeometry.dimensions(self.size)[0].preferred - 4,
                              bind_navigation=shortcut_context != DialogContext.READER),
                    padding=Dimension(preferred=1, max=1), padding_left=0, padding_right=0),
            ])
        body = HSplit([body, ConditionalContainer(
            Box(DynamicContainer(lambda: self.status_bar), padding=0,
                padding_left=self.STATUS_PADDING, padding_right=self.STATUS_PADDING),
            Condition(lambda: self.status_bar is not None and not self.status_in_body),
        )])
        if args:
            args = (body, *args[1:])
        else:
            kwargs["body"] = body
        super().__init__(*args, **kwargs,
                         width=lambda: PopupGeometry.dimensions(self.size)[0])
        # Match PTK Dialog.body: callers see (and may replace) the actual body,
        # not the action-row wrapper owned by this common widget.
        self.body = self.popup_body
        self.container = Box(self.container, padding=0,
                             width=lambda: PopupGeometry.dimensions(self.size)[0],
                             height=lambda: PopupGeometry.dimensions(self.size)[1])
        # Read-only help/log dialogs deliberately keep arrows as scroll keys.
        if shortcut_context != DialogContext.READER:
            bind_button_navigation(buttons)

    @property
    def size(self):
        body = getattr(self, "body", self.popup_body)
        if self.requested_size == PopupSize.SMALL and body is not None:
            if any(isinstance(item, Window) and item.content.is_focusable()
                   for item in walk(to_container(body))):
                return PopupSize.MEDIUM
        return self.requested_size
