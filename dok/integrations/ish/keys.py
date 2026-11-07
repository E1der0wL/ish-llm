"""Keyboard sequence interpretation and ownership-aware host registration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings, DynamicKeyBindings, merge_key_bindings
from prompt_toolkit.key_binding.key_bindings import KeyBindingsBase
from prompt_toolkit.keys import Keys

from ...core.records import Rule, SettingsError


@dataclass(frozen=True, slots=True)
class KeySequence:
    keys: tuple[Keys | str, ...]

    def __post_init__(self):
        if not isinstance(self.keys, (list, tuple)) or not self.keys:
            raise SettingsError("Key sequence must be a nonempty array")
        if self.keys[0] in ("enter", "tab", "backspace"):
            raise SettingsError("Use an explicit Ctrl combination instead of an editing-key alias")
        candidate = KeyBindings()
        try:
            candidate.add(*self.keys)(lambda event: None)
        except (ValueError, TypeError) as exc:
            raise SettingsError(f"Invalid key sequence: {self.keys}") from exc
        normalized = candidate.bindings[0].keys
        first = normalized[0]
        # Explicit Ctrl combinations are allowed, including terminal aliases.
        if first not in self.prefix_keys():
            raise SettingsError("Use a function key or a modified key sequence")
        if first == Keys.Escape and len(normalized) == 1:
            raise SettingsError("Escape alone is reserved for shell editing")
        object.__setattr__(self, "keys", normalized)

    @staticmethod
    def prefix_keys():
        return frozenset(
            key for key in Keys
            if (key.name.startswith("Control") or key.name.startswith("F") and key.name[1:].isdigit()
                or key == Keys.Escape)
        )

    def overlaps(self, other: tuple) -> bool:
        length = min(len(self.keys), len(other))
        return self.keys[:length] == tuple(other[:length])

    @staticmethod
    def validate_unique(sequences) -> None:
        seen = []
        for sequence in sequences:
            if any(sequence.overlaps(previous.keys) for previous in seen):
                raise SettingsError(f"Overlapping dok keys: {sequence.to_list()}")
            seen.append(sequence)

    def to_list(self):
        return [key.value if isinstance(key, Keys) else key for key in self.keys]


class KeyRule(Rule):
    def __init__(self, *, optional=False):
        self.optional = optional

    def decode(self, value):
        if self.optional and value is None:
            return None
        return value if isinstance(value, KeySequence) else KeySequence(value)

    def encode(self, value):
        return None if value is None else value.to_list()

    def schema(self):
        result = {"type": "array", "minItems": 1, "items": {"type": "string"},
                  "description": "Function or modified sequence; interpreted by KeySequence"}
        return {"anyOf": [result, {"type": "null"}]} if self.optional else result


@dataclass(frozen=True, slots=True)
class KeyRegistration:
    sequence: KeySequence
    handler: Callable
    enabled: Callable[[], bool]


class HostBindingView(KeyBindingsBase):
    """Keep original handlers, suppressing only keys shadowed by an active dok key."""

    def __init__(self, original, entries, suspended=lambda: False):
        self._original = original
        self._entries = entries
        self._suspended = suspended
        self._cached_version = None
        self._cache = KeyBindings()

    @property
    def _version(self):
        return self._original._version

    def _blocked(self, keys):
        return self._suspended() or any(entry.enabled() and entry.sequence.overlaps(keys) for entry in self._entries())

    def _update(self):
        if self._cached_version != self._version:
            candidate = KeyBindings()
            for binding in self._original.bindings:
                candidate.add(
                    *binding.keys, filter=binding.filter & ~Condition(lambda keys=binding.keys: self._blocked(keys)),
                    eager=binding.eager, is_global=binding.is_global, save_before=binding.save_before,
                    record_in_macro=binding.record_in_macro,
                )(binding.handler)
            self._cache = candidate
            self._cached_version = self._version

    @property
    def bindings(self):
        self._update()
        return self._cache.bindings

    def get_bindings_for_keys(self, keys):
        self._update()
        return self._cache.get_bindings_for_keys(keys)

    def get_bindings_starting_with_keys(self, keys):
        self._update()
        return self._cache.get_bindings_starting_with_keys(keys)


class OwnedBindings:
    """A conditional application layer with priority over preserved host bindings."""

    def __init__(self, prompt, *, suspended=lambda: False):
        self._prompt = prompt
        self._suspended = suspended
        self._bindings = KeyBindings()
        self._previous = None
        self._layer = None
        self._entries = ()

    def replace(self, entries):
        candidate = KeyBindings()
        for entry in entries:
            candidate.add(*entry.sequence.keys, filter=Condition(entry.enabled), eager=True)(entry.handler)
        if self._layer is None:
            self._previous = self._prompt.app.key_bindings
            self._layer = merge_key_bindings([
                HostBindingView(self._previous if self._previous is not None else KeyBindings(),
                                lambda: self._entries, self._suspended),
                DynamicKeyBindings(lambda: self._bindings),
            ])
            self._prompt.app.key_bindings = self._layer
        self._bindings = candidate
        self._entries = tuple(entries)

    def clear(self):
        self._bindings = KeyBindings()
        self._entries = ()
        if self._prompt.app.key_bindings is self._layer:
            self._prompt.app.key_bindings = self._previous
        self._layer = None
        self._previous = None
