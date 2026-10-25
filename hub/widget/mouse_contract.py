"""Hub-owned mouse metadata on foreign PTK objects; observations confer no authority."""


class MouseBinding:
    CONTROLLER = "hub_mouse"
    HOVER = "hub_mouse_hover"
    CLIP = "hub_mouse_clip"
    PANE = "hub_mouse_pane"
    SCROLL = "hub_mouse_scroll"
    FOCUS = "hub_click_focus"
    INTERACTIVE = "hub_interactive"
    ABSENT = None
    INACTIVE = False
    ACTIVE = True

    @classmethod
    def controller(cls, target):
        return getattr(target, cls.CONTROLLER, cls.ABSENT)

    @classmethod
    def set_controller(cls, target, value):
        setattr(target, cls.CONTROLLER, value)

    @classmethod
    def hovered(cls, target):
        return getattr(target, cls.HOVER, cls.INACTIVE)

    @classmethod
    def set_hovered(cls, target, value):
        setattr(target, cls.HOVER, value)

    @classmethod
    def clip(cls, target):
        return getattr(target, cls.CLIP, cls.ABSENT)

    @classmethod
    def set_clip(cls, target, value):
        setattr(target, cls.CLIP, value)

    @classmethod
    def pane(cls, target):
        return getattr(target, cls.PANE, cls.ABSENT)

    @classmethod
    def set_pane(cls, target, value):
        setattr(target, cls.PANE, value)

    @classmethod
    def scroll(cls, target):
        return getattr(target, cls.SCROLL, cls.ABSENT)

    @classmethod
    def set_scroll(cls, target, value):
        setattr(target, cls.SCROLL, value)

    @classmethod
    def focus(cls, target):
        return getattr(target, cls.FOCUS, cls.ABSENT)

    @classmethod
    def set_focus(cls, target, value):
        setattr(target, cls.FOCUS, value)

    @classmethod
    def interactive(cls, target):
        return getattr(target, cls.INTERACTIVE, cls.INACTIVE)

    @classmethod
    def set_interactive(cls, target, value):
        setattr(target, cls.INTERACTIVE, value)

    @classmethod
    def remove_controller(cls, target):
        delattr(target, cls.CONTROLLER)

    @classmethod
    def register_interactive(cls, target):
        cls.set_interactive(target, cls.ACTIVE)
        return target


class ControlRole:
    FIELD = "hub_role"
    BUTTON = "button"
    CHOICES = "choices"

    @classmethod
    def register(cls, control, role):
        setattr(control, cls.FIELD, role)
