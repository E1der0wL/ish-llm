"""Shared horizontal button navigation and responsive, centered action rows."""
from hub.ui.input.registry import ShortcutRegistry


from prompt_toolkit.layout import DynamicContainer, HSplit, VSplit, Window
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Frame
from ..ui.input.keys import KeyName, ShortcutAction, ShortcutLabel


class ButtonNavigation:
    GROUP_ATTRIBUTE = "hub_button_group"
    UNBOUND = None
    SINGLE_ITEM = 1

    @classmethod
    def bound(cls, control, buttons):
        return getattr(control, cls.GROUP_ATTRIBUTE, cls.UNBOUND) is buttons

    @classmethod
    def bind(cls, control, buttons):
        setattr(control, cls.GROUP_ATTRIBUTE, buttons)


class ActionFrame(Frame):
    """Five-row action frame that grows only when buttons actually wrap."""

    PADDING_ROWS = 1

    def __init__(self, actions, *, hint=None, **kwargs):
        # Let the actual available width determine wrapped button rows. An
        # unbounded explicit height would reserve unused space below the frame.
        super().__init__(
            HSplit(
                [
                    Window(height=self.PADDING_ROWS),
                    actions,
                    hint if hint is not None else Window(height=self.PADDING_ROWS),
                ]
            ),
            **kwargs
        )


def bind_button_navigation(buttons):
    """Keep button arrows and their footer hints together, including edge keys."""

    def move(event):
        controls = [button.control for button in buttons]
        current = event.app.layout.current_control
        if current in controls:
            index = controls.index(current) + (
                -1 if event.key_sequence[-1].key == KeyName.LEFT else 1
            )
            event.app.layout.focus(controls[max(0, min(len(controls) - 1, index))])

    for button in buttons:
        if ButtonNavigation.bound(button.control, buttons):
            continue
        ButtonNavigation.bind(button.control, buttons)
        registry = ShortcutRegistry.require(button.control)
        registry.add(
            [KeyName.LEFT, KeyName.RIGHT],
            ShortcutLabel.HORIZONTAL,
            ShortcutAction.SELECT,
            move,
            when=lambda: len(buttons) > ButtonNavigation.SINGLE_ITEM,
        )
        registry.add(
            [KeyName.UP, KeyName.DOWN],
            ShortcutLabel.HIDDEN,
            ShortcutAction.NONE,
            lambda e: None,
        )


class ActionBar(DynamicContainer):
    GAP = 1
    CENTER_WEIGHT = 1
    AVAILABLE_WIDTH = None

    def __init__(self, buttons, width, *, bind_navigation=True):
        self.buttons, self.available_width = buttons, width
        self.bind_navigation = bind_navigation
        if bind_navigation:
            bind_button_navigation(buttons)
        super().__init__(self._container)

    def insert(self, index, button):
        self.buttons.insert(index, button)
        if self.bind_navigation:
            bind_button_navigation(self.buttons)

    def rows(self, width=AVAILABLE_WIDTH):
        width = self.available_width() if width is self.AVAILABLE_WIDTH else width
        rows, row, used = [], [], 0
        for button in self.buttons:
            if row and used + button.width + self.GAP > width:
                rows.append(row)
                row, used = [], 0
            used += button.width + self.GAP * bool(row)
            row.append(button)
        return [*rows, row] if row else rows

    def preferred_width(self, max_available_width):
        minimum = max((button.width for button in self.buttons), default=0)
        return Dimension(
            min=min(minimum, max_available_width),
            preferred=min(self.available_width(), max_available_width),
        )

    def preferred_height(self, width, max_available_height):
        return self._container(width).preferred_height(width, max_available_height)

    def write_to_screen(
        self, screen, mouse_handlers, write_position, parent_style, erase_bg, z_index
    ):
        self._container(write_position.width).write_to_screen(
            screen, mouse_handlers, write_position, parent_style, erase_bg, z_index
        )

    def _container(self, width=AVAILABLE_WIDTH):
        return HSplit(
            [
                VSplit(
                    [
                        Window(width=Dimension(weight=self.CENTER_WEIGHT)),
                        VSplit(
                            row,
                            padding=self.GAP,
                            width=Dimension.exact(
                                sum(button.width for button in row)
                                + self.GAP * (len(row) - 1)
                            ),
                        ),
                        Window(width=Dimension(weight=self.CENTER_WEIGHT)),
                    ]
                )
                for row in self.rows(width)
            ]
        )
