"""Completion records, evidence validation and registration transitions."""
from dataclasses import dataclass, replace
from enum import StrEnum
import re

from ...core.records import Boolean, Choice, Nested, Number, Record, Sequence, Text, setting


class ItemKind(StrEnum):
    OPTION = "option"
    COMMAND = "command"


class Argument(StrEnum):
    NONE = "none"
    REQUIRED = "required"
    OPTIONAL = "optional"


class Join(StrEnum):
    SPACE = "space"
    EQUAL = "equal"


class MergeMode(StrEnum):
    MERGE = "merge"
    REPLACE = "replace"


@dataclass(frozen=True, slots=True)
class Candidate(Record):
    name: str = setting(Text(nonblank=True, single_line=True))
    help: str = setting(Text(single_line=True), default="")
    kind: ItemKind = setting(Choice(ItemKind), default=ItemKind.OPTION)
    argument: Argument = setting(Choice(Argument), default=Argument.NONE)
    join: Join = setting(Choice(Join), default=Join.SPACE)
    values: tuple[str, ...] = setting(Sequence(Text(nonblank=True, single_line=True)), default=())
    evidence: str = setting(Text(single_line=True), default="")
    locked: bool = setting(Boolean(), default=False)

    def _check(self):
        pattern = r"--?[\w][\w-]*" if self.kind == ItemKind.OPTION else r"[\w][\w.:-]*"
        if not re.fullmatch(pattern, self.name):
            raise ValueError("Expected a literal option or subcommand name")
        if self.argument == Argument.NONE and (self.values or self.join != Join.SPACE):
            raise ValueError("A valueless option cannot declare a value separator or choices")
        if self.kind == ItemKind.COMMAND and self.argument != Argument.NONE:
            raise ValueError("Subcommand arguments belong to its own prefix")
        if len(set(self.values)) != len(self.values):
            raise ValueError("Duplicate argument choices")

    def verify(self, source: str):
        if not self.evidence or self.evidence not in source:
            raise ValueError("Missing source evidence")
        if not re.search(r"(?<![\w-])" + re.escape(self.name) + r"(?![\w-])", self.evidence):
            raise ValueError("The candidate is absent from its evidence")
        if any(value not in self.evidence for value in self.values):
            raise ValueError("Argument choices must appear in the evidence")
        return self


@dataclass(frozen=True, slots=True)
class Analysis(Record):
    items: tuple[Candidate, ...] = setting(Sequence(Nested(Candidate)), default=())

    def _check(self):
        if len({item.name for item in self.items}) != len(self.items):
            raise ValueError("Duplicate completion names")

    def verified(self, source):
        return type(self)(tuple(item.verify(source) for item in self.items))


@dataclass(frozen=True, slots=True)
class Registration(Record):
    prefix: tuple[str, ...] = setting(Sequence(Text(nonblank=True, single_line=True), minimum=1))
    command: str = setting(Text(single_line=True), default="")
    items: tuple[Candidate, ...] = setting(Sequence(Nested(Candidate)), default=())
    locked: bool = setting(Boolean(), default=False)

    def _check(self):
        Analysis(self.items)

    def item(self, name):
        return next((item for item in self.items if item.name == name), None)

    def edit_item(self, previous, candidate):
        if candidate.name != previous and self.item(candidate.name) is not None:
            raise ValueError("An entry with this name already exists")
        return replace(self, items=tuple(candidate if item.name == previous else item for item in self.items)
                       if self.item(previous) is not None else (*self.items, candidate))

    def remove_item(self, name):
        return replace(self, items=tuple(item for item in self.items if item.name != name))

    def collection_items(self, analysis):
        if self.locked:
            return ()
        return tuple(replace(item, locked=False) for item in analysis.items
                     if not ((existing := self.item(item.name)) and existing.locked))

    def apply(self, analysis: Analysis, mode: MergeMode, command: str):
        mode = MergeMode(mode)
        if self.locked:
            return self
        items = {item.name: item for item in self.items
                 if mode == MergeMode.MERGE or item.locked}
        # A provider cannot introduce management policy through analysis.
        items.update((item.name, item) for item in self.collection_items(analysis))
        return replace(self, command=command, items=tuple(items.values()))


@dataclass(frozen=True, slots=True)
class Catalog(Record):
    VERSION = 1
    version: int = setting(Number(integer=True), default=VERSION)
    revision: int = setting(Number(minimum=0, integer=True), default=0)
    registrations: tuple[Registration, ...] = setting(Sequence(Nested(Registration)), default=())

    def _check(self):
        if self.version != self.VERSION:
            raise ValueError("Unsupported completion storage version")
        if len({item.prefix for item in self.registrations}) != len(self.registrations):
            raise ValueError("Duplicate completion prefix")

    def get(self, prefix):
        return next((item for item in self.registrations if item.prefix == tuple(prefix)), None)

    def put(self, registration):
        items = [item for item in self.registrations if item.prefix != registration.prefix]
        return replace(self, revision=self.revision + 1, registrations=(*items, registration))

    def edit_registration(self, previous, registration):
        if previous != registration.prefix and self.get(registration.prefix) is not None:
            raise ValueError("This prefix already exists")
        return replace(self, registrations=tuple(item for item in self.registrations
                       if item.prefix != previous)).put(registration)

    def remove_registration(self, prefix):
        return replace(self, revision=self.revision + 1,
                       registrations=tuple(item for item in self.registrations if item.prefix != prefix))

    def replace_contents(self, draft):
        return replace(self, revision=self.revision + 1, registrations=draft.registrations)

    def ordered_registrations(self):
        return tuple(sorted(self.registrations, key=lambda item: tuple(self.name_order(word) for word in item.prefix)))

    @staticmethod
    def name_order(name):
        return name.casefold(), name

    def inherited(self, prefix):
        items = {}
        for size in range(1, len(prefix) + 1):
            registration = self.get(prefix[:size])
            if registration:
                items.update((item.name, item) for item in registration.items)
        return tuple(items.values())

    @classmethod
    def ordered_suggestions(cls, items):
        """Case-insensitive full-name order, including every punctuation character."""
        return tuple(sorted(items, key=lambda item: cls.name_order(item[0])))

    def suggestions(self, words, current):
        """Interpret known options only; unknown options leave native completion alone."""
        if not words:
            return ()
        prefix = [words[0]]
        expecting = None
        for word in words[1:]:
            if expecting is not None:
                expecting = None
                continue
            if word == "--":
                return ()
            if word.startswith("-"):
                name, equal, _ = word.partition("=")
                option = next((item for item in self.inherited(prefix) if item.name == name), None)
                if option is None:
                    return ()
                if not equal and option.argument == Argument.REQUIRED and option.join == Join.SPACE:
                    expecting = option
            else:
                prefix.append(word)
        items = self.inherited(prefix)
        if expecting:
            return self.ordered_suggestions((value, expecting.help, False)
                                            for value in expecting.values if value.startswith(current))
        name, equal, value_prefix = current.partition("=")
        if equal:
            option = next((item for item in items if item.name == name), None)
            return self.ordered_suggestions((name + "=" + value, option.help, False) for value in option.values
                                            if value.startswith(value_prefix)) if option else ()
        # Options from a parent remain valid, but parent subcommands do not leak into children.
        own = self.get(prefix)
        local_commands = {item.name for item in own.items if item.kind == ItemKind.COMMAND} if own else set()
        return self.ordered_suggestions((item.name + ("=" if item.join == Join.EQUAL else ""), item.help,
                                         item.join == Join.EQUAL) for item in items
                                        if item.name.startswith(current)
                                        and (item.kind == ItemKind.OPTION or item.name in local_commands))
