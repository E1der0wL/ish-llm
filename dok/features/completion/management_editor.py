"""Editable completion drafts; final validation stays with Candidate."""
from enum import StrEnum
from ...rendering.input import EditBuffer

from ...locales import Message as M
from .models import Argument, Candidate, ItemKind, Join


class ItemField(StrEnum):
    NAME = "name"
    HELP = "help"
    KIND = "kind"
    ARGUMENT = "argument"
    JOIN = "join"
    VALUES = "values"
    LOCKED = "locked"

    def label(self, locale):
        return locale.text({self.NAME: M.COMPLETION_NAME, self.HELP: M.COMPLETION_HELP,
                            self.KIND: M.COMPLETION_KIND, self.ARGUMENT: M.COMPLETION_ARGUMENT,
                            self.JOIN: M.COMPLETION_JOIN, self.VALUES: M.COMPLETION_VALUES,
                            self.LOCKED: M.COMPLETION_LOCK}[self])


class ItemDraft:
    def __init__(self, item=None):
        self.original = item
        self.kind = item.kind if item else ItemKind.OPTION
        self.argument = item.argument if item else Argument.NONE
        self.join = item.join if item else Join.SPACE
        self.locked = item.locked if item else False
        self._buffers = {ItemField.NAME: EditBuffer(item.name if item else ""),
                         ItemField.HELP: EditBuffer(item.help if item else ""),
                         ItemField.VALUES: EditBuffer("\n".join(item.values) if item else "", multiline=True)}
        self._initial_values = self._editable_values()

    def _editable_values(self):
        return tuple((field, self._buffers[field].text if field in self._buffers else self.value(field))
                     for field in self.fields())

    @property
    def dirty(self):
        """Compare effective editable fields; evidence is not an editable value."""
        return self._editable_values() != self._initial_values

    def fields(self):
        return tuple(field for field in ItemField if
                     field not in (ItemField.ARGUMENT, ItemField.JOIN, ItemField.VALUES) or
                     self.kind == ItemKind.OPTION and (field == ItemField.ARGUMENT or self.argument != Argument.NONE))

    def buffer(self, field):
        return self._buffers.get(field)

    def choices(self, field):
        return {ItemField.KIND: ((ItemKind.OPTION, M.COMPLETION_OPTION), (ItemKind.COMMAND, M.COMPLETION_SUBCOMMAND)),
                ItemField.ARGUMENT: ((Argument.NONE, M.COMPLETION_NONE), (Argument.REQUIRED, M.COMPLETION_REQUIRED),
                                     (Argument.OPTIONAL, M.COMPLETION_OPTIONAL)),
                ItemField.JOIN: ((Join.SPACE, M.COMPLETION_SPACE), (Join.EQUAL, M.COMPLETION_EQUALS)),
                ItemField.LOCKED: ((False, M.COMPLETION_UNLOCK), (True, M.COMPLETION_LOCKED))}.get(field, ())

    def choose(self, field, value):
        if value not in tuple(choice for choice, _ in self.choices(field)):
            raise ValueError("Invalid completion editor choice")
        setattr(self, field.value, value)

    def value(self, field):
        return getattr(self, field.value)

    def build(self):
        if self.original is not None and not self.dirty:
            return self.original
        argument = self.argument if self.kind == ItemKind.OPTION else Argument.NONE
        has_value = argument != Argument.NONE
        return Candidate(name=self._buffers[ItemField.NAME].text, help=self._buffers[ItemField.HELP].text,
                         kind=self.kind, argument=argument, join=self.join if has_value else Join.SPACE,
                         values=tuple(self._buffers[ItemField.VALUES].text.splitlines()) if has_value else (),
                         locked=self.locked)
