"""One definition supplies both PTK dispatch and the visible shortcut legend."""

from hub.locales.keys import MessageKey, MessagePattern

from dataclasses import dataclass
from collections.abc import Callable

from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings

from .keys import KeyName, ShortcutAction, ShortcutLabel

MOVEMENT_KEYS = KeyName.MOVEMENT


class ShortcutOrder:
    ESCAPE = 0
    CONTROL = 1
    ALT = 2
    ORDINARY = 3


@dataclass(slots=True)
class Shortcut:
    ACTIVE = True

    sequences: tuple[tuple[str, ...], ...]
    label: str | Callable
    action: str | Callable
    when: Callable

    def order(self):
        key = self.sequences[0]
        category = (
            ShortcutOrder.ESCAPE
            if key == (KeyName.ESCAPE,)
            else (
                ShortcutOrder.CONTROL
                if key[0].startswith(KeyName.CONTROL_PREFIX)
                else (
                    ShortcutOrder.ALT
                    if key[0] == KeyName.ESCAPE
                    else ShortcutOrder.ORDINARY
                )
            )
        )
        name = (
            ShortcutLabel.MOVEMENT
            if self.label == ShortcutLabel.MOVEMENT
            else key[-1].removeprefix(KeyName.CONTROL_PREFIX).casefold()
        )
        return category, name


class ShortcutRegistry:
    CONTROL_ATTRIBUTE = "hub_shortcuts"
    UNBOUND = None
    EAGER = True
    GLOBAL = False
    VISIBLE = True

    def __init__(self):
        self.bindings = KeyBindings()
        self.shortcuts = []

    def attach(self, control):
        setattr(control, self.CONTROL_ATTRIBUTE, self)
        return self

    @classmethod
    def require(cls, control):
        return getattr(control, cls.CONTROL_ATTRIBUTE)

    @classmethod
    def for_control(cls, control, default=UNBOUND):
        return getattr(control, cls.CONTROL_ATTRIBUTE, default)

    def add(
        self,
        sequences,
        label,
        action,
        handler,
        *,
        when=lambda: Shortcut.ACTIVE,
        eager=EAGER,
        global_=GLOBAL
    ):
        sequences = tuple(
            (keys,) if isinstance(keys, str) else tuple(keys) for keys in sequences
        )
        self.shortcuts.append(Shortcut(sequences, label, action, when))
        for keys in sequences:
            self.bindings.add(
                *keys,
                filter=Condition(when),
                eager=eager and keys != (KeyName.ESCAPE,),
                is_global=global_
            )(handler)

    def scroll(self, handler, *, when=lambda: Shortcut.ACTIVE, visible=VISIBLE):
        self.add(
            [(KeyName.ESCAPE, key) for key in MOVEMENT_KEYS],
            ShortcutLabel.MOVEMENT if visible else ShortcutLabel.HIDDEN,
            ShortcutAction.SCROLL,
            handler,
            when=when,
        )

    def active(self):
        return [
            shortcut
            for shortcut in self.shortcuts
            if shortcut.label and shortcut.when()
        ]


def legend(shortcuts, t):
    result = []
    for shortcut in sorted(shortcuts, key=lambda item: item.order()):
        label = shortcut.label() if callable(shortcut.label) else shortcut.label
        action = shortcut.action() if callable(shortcut.action) else shortcut.action
        pair = (
            (
                t(MessageKey.SHORTCUT_ALT_MOVEMENT)
                if label == ShortcutLabel.MOVEMENT
                else label
            ),
            t(MessagePattern.shortcut(action)),
        )
        if pair not in result:
            result.append(pair)
    return result
