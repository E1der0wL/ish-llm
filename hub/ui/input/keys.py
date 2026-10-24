"""PTK key names used at the Hub input boundary, including its public aliases."""

from prompt_toolkit.keys import Keys


class KeyName:
    # These aliases intentionally retain the spelling accepted by existing Hub
    # callbacks. PTK's canonical ControlI/ControlM names are not substituted.
    TAB = "tab"
    SHIFT_TAB = "s-tab"
    ENTER = "enter"
    CONTROL_SPACE = "c-space"
    ESCAPE = Keys.Escape.value
    UP = Keys.Up.value
    DOWN = Keys.Down.value
    LEFT = Keys.Left.value
    RIGHT = Keys.Right.value
    HOME = Keys.Home.value
    END = Keys.End.value
    PAGE_UP = Keys.PageUp.value
    PAGE_DOWN = Keys.PageDown.value
    C = "c"
    R = "r"
    EDIT = "e"
    EDIT_UPPER = "E"
    CLEAR = "d"
    BACKSPACE = "backspace"
    DELETE = Keys.Delete.value
    CONTROL_A = Keys.ControlA.value
    CONTROL_X = Keys.ControlX.value
    CONTROL_Z = Keys.ControlZ.value
    CONTROL_Y = Keys.ControlY.value
    CONTROL_LEFT = Keys.ControlLeft.value
    CONTROL_RIGHT = Keys.ControlRight.value
    CONTROL_HOME = Keys.ControlHome.value
    CONTROL_END = Keys.ControlEnd.value
    CONTROL_PREFIX = "c-"
    SHIFT_PREFIX = "s-"
    CONTROL_SHIFT_PREFIX = CONTROL_PREFIX + SHIFT_PREFIX
    NO_MODIFIER = ""
    MOVEMENT = (UP, DOWN, LEFT, RIGHT, HOME, END, PAGE_UP, PAGE_DOWN)

    @classmethod
    def shifted(cls, key):
        return (cls.CONTROL_SHIFT_PREFIX + key[len(cls.CONTROL_PREFIX):]
                if key.startswith(cls.CONTROL_PREFIX) else cls.SHIFT_PREFIX + key)

    @classmethod
    def unshifted(cls, key):
        return key.replace(cls.SHIFT_PREFIX, cls.NO_MODIFIER)


class ShortcutAction:
    NONE = ""
    EDITOR = "editor"
    EDIT = "edit"
    DONE = "done"
    CLEAR = "clear"
    NEWLINE = "newline"
    SELECT = "select"
    SCROLL = "scroll"
    SIDEBAR = "sidebar"


class ShortcutLabel:
    HIDDEN = ""
    MOVEMENT = "movement"
    ENTER = "Enter"
    CONTROL_SPACE = "Ctrl+Space"
    ALT_ENTER = "Alt+Enter"
    DIRECTIONS = "↑↓←→"
    HORIZONTAL = "←→"
    ESCAPE = "ESC"
