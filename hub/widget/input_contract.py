"""The Hub writable-control marker at the prompt_toolkit input boundary."""


class EditableControl:
    MARKER = "hub_editable"
    REGISTERED = True
    UNREGISTERED = False
    CONTROL = "control"

    @classmethod
    def register(cls, control):
        setattr(control, cls.MARKER, cls.REGISTERED)

    @classmethod
    def writable(cls, control):
        return (
            getattr(control, cls.MARKER, cls.UNREGISTERED)
            and not control.buffer.read_only()
        )

    @classmethod
    def resolve(cls, widget):
        return getattr(widget, cls.CONTROL, widget)


class ContainerControl:
    """Observe the optional PTK control held by a walked layout container."""

    CONTENT = "content"
    FOCUSABLE = "is_focusable"
    ABSENT = None

    @classmethod
    def resolve(cls, container):
        return getattr(container, cls.CONTENT, cls.ABSENT)

    @classmethod
    def required(cls, container):
        return getattr(container, cls.CONTENT)

    @classmethod
    def has_focusable_content(cls, container):
        return hasattr(container, cls.CONTENT) and hasattr(cls.required(container), cls.FOCUSABLE)


class MouseResult:
    """PTK mouse dispatch result; None consumes an event."""

    HANDLED = None
    UNHANDLED = NotImplemented


class ControlFragment:
    """Reserved prompt_toolkit formatted-text control markers."""

    CURSOR_POSITION = "[SetCursorPosition]"
    ZERO_WIDTH_ESCAPE = "[ZeroWidthEscape]"
    STYLE_POSITION = 0
    TEXT_POSITION = 1

    @classmethod
    def text(cls, fragment):
        return fragment[cls.TEXT_POSITION]

    @classmethod
    def style(cls, fragment):
        return fragment[cls.STYLE_POSITION]
