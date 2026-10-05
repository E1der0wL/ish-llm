"""One definition supplies both PTK dispatch and the visible shortcut legend."""

from dataclasses import dataclass
from collections.abc import Callable

from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings

MOVEMENT_KEYS = ("up", "down", "left", "right", "home", "end", "pageup", "pagedown")


@dataclass(slots=True)
class Shortcut:
    sequences: tuple[tuple[str, ...], ...]
    label: str | Callable
    action: str | Callable
    when: Callable

    def order(self):
        key = self.sequences[0]
        category = 0 if key == ("escape",) else 1 if key[0].startswith("c-") else 2 if key[0] == "escape" else 3
        name = "movement" if self.label == "movement" else key[-1].removeprefix("c-").casefold()
        return category, name


class ShortcutRegistry:
    def __init__(self):
        self.bindings = KeyBindings()
        self.shortcuts = []

    def add(self, sequences, label, action, handler, *, when=lambda: True, eager=True, global_=False):
        sequences = tuple((keys,) if isinstance(keys, str) else tuple(keys) for keys in sequences)
        self.shortcuts.append(Shortcut(sequences, label, action, when))
        for keys in sequences:
            self.bindings.add(*keys, filter=Condition(when), eager=eager and keys != ("escape",), is_global=global_)(handler)

    def scroll(self, handler, *, when=lambda: True, visible=True):
        self.add([("escape", key) for key in MOVEMENT_KEYS],
                 "movement" if visible else "", "scroll", handler, when=when)

    def active(self):
        return [shortcut for shortcut in self.shortcuts if shortcut.label and shortcut.when()]


def legend(shortcuts, t):
    result = []
    for shortcut in sorted(shortcuts, key=lambda item: item.order()):
        label = shortcut.label() if callable(shortcut.label) else shortcut.label
        action = shortcut.action() if callable(shortcut.action) else shortcut.action
        pair = (t("shortcut_alt_movement") if label == "movement" else label, t("shortcut_" + action))
        if pair not in result:
            result.append(pair)
    return result
