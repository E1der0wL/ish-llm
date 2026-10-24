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
    EDIT = "e"
    EDIT_UPPER = "E"
    CLEAR = "d"
    CONTROL_PREFIX = "c-"
    MOVEMENT = (UP, DOWN, LEFT, RIGHT, HOME, END, PAGE_UP, PAGE_DOWN)


class ShortcutAction:
    NONE = ""
    EDITOR = "editor"
    EDIT = "edit"
    DONE = "done"
    CLEAR = "clear"
    NEWLINE = "newline"
    SELECT = "select"
    SCROLL = "scroll"


class ShortcutLabel:
    HIDDEN = ""
    MOVEMENT = "movement"
    ENTER = "Enter"
    CONTROL_SPACE = "Ctrl+Space"
    ALT_ENTER = "Alt+Enter"
    DIRECTIONS = "↑↓←→"
