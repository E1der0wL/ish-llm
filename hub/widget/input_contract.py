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
