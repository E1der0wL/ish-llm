"""PTK key names used at the Hub input boundary, including its public aliases."""

from prompt_toolkit.keys import Keys


class KeyName:
    N = "n"
    N_UPPER = "N"
    P = "p"
    SLASH = "/"
    CONTROL_N = Keys.ControlN.value
    # These aliases intentionally retain the spelling accepted by existing Hub
    # callbacks. PTK's canonical ControlI/ControlM names are not substituted.
    SPACE = " "
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
    CONTROL_Q = Keys.ControlQ.value
    CONTROL_W = Keys.ControlW.value
    CONTROL_P = Keys.ControlP.value
    CONTROL_C = Keys.ControlC.value
    CONTROL_V = Keys.ControlV.value
    CONTROL_S = Keys.ControlS.value
    CONTROL_F = Keys.ControlF.value
    CONTROL_G = Keys.ControlG.value
    CONTROL_L = Keys.ControlL.value
    CONTROL_T = Keys.ControlT.value
    CONTROL_D = Keys.ControlD.value
    CONTROL_O = Keys.ControlO.value
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
    NEXT_MATCH = "next_match"
    PREVIOUS_MATCH = "previous_match"
    OBJECT_RAW = "object_raw"
    CANCEL = "cancel"
    EXPAND = "expand"
    ACTIVATE = "activate"
    DELETE = "delete"
    RETRY = "retry"
    CLOSE = "close"
    RETURN = "return"
    TABS = "tabs"
    MAIN = "main"
    NONE = ""
    EDITOR = "editor"
    EDIT = "edit"
    DONE = "done"
    CLEAR = "clear"
    NEWLINE = "newline"
    SELECT = "select"
    SCROLL = "scroll"
    SIDEBAR = "sidebar"


    MINIMIZE = 'minimize'
    RESTART = 'restart'
    PROFILE = 'profile'
    COPY = 'copy'
    PASTE = 'paste'
    MOVE = 'move'
    AUXILIARY = 'auxiliary'
    FIND = 'find'
    STOP_RESUME = 'stop_resume'
    REGIONS = 'regions'
    ITEMS = 'items'
    WIDTH = 'width'
    CREATE = 'create'
    RENAME = 'rename'
    CLONE = 'clone'
    CATEGORIES = 'categories'
    CONFIRM = 'confirm'
    SEND = 'send'
    APPROVAL_DETAILS = 'approval_details'


class ShortcutLabel:
    ESCAPE_CONTROL_T = "ESC/Ctrl+T"
    CONTROL_F_SLASH = "Ctrl+F / /"
    ENTER_TAB = "Enter/Tab"
    CONTROL_T = "Ctrl+T"
    N_CONTROL_N = "n/Ctrl+N"
    N_CONTROL_P = "N/Ctrl+P"
    ALT_P = "Alt+P"
    TAB_SPACE_ENTER = "Tab/Space/Enter"
    ENTER_SPACE = "Enter/Space"
    HIDDEN = ""
    MOVEMENT = "movement"
    ENTER = "Enter"
    CONTROL_SPACE = "Ctrl+Space"
    ALT_ENTER = "Alt+Enter"
    DIRECTIONS = "↑↓←→"
    HORIZONTAL = "←→"
    ESCAPE = "ESC"

    TAB_SHIFT_TAB = 'Tab/Shift+Tab'
    VERTICAL = '↑↓'
    CONTROL_HORIZONTAL = 'Ctrl+←→'
    SPACE_ENTER_RIGHT = 'Space/Enter/→'
    CONTROL_Q = 'Ctrl+Q'
    CONTROL_W = 'Ctrl+W'
    CONTROL_P = 'Ctrl+P'
    CONTROL_C = 'Ctrl+C'
    CONTROL_S = 'Ctrl+S'
    CONTROL_F = 'Ctrl+F'
    CONTROL_D = 'Ctrl+D'
    CONTROL_O = 'Ctrl+O'
    CONTROL_L = 'Ctrl+L'
    LEFT = '←'
    TAB = 'Tab'
