"""dok execution settings, independent of a calendar provider's event record."""
from dataclasses import dataclass, fields

from ...core.records import Nested, Number, Record, Sequence, Text, setting
from .reminders import Reminder


@dataclass(frozen=True, slots=True, kw_only=True)
class ActionSettings(Record):
    alarms: tuple[Reminder, ...] = setting(Sequence(Nested(Reminder)), default=(),
        description="dok reminder offsets from occurrence start. Equivalent offsets are delivered once.")
    command: str = setting(Text(single_line=True), default="",
        description="Command submitted only through an idle, empty ish prompt. Busy occurrences are skipped.")

    def _check(self):
        object.__setattr__(self, "alarms", Reminder.ordered(self.alarms))

    @property
    def active(self):
        return bool(self.alarms or self.command.strip())

    @classmethod
    def from_event(cls, event):
        return cls(**{field.name: getattr(event, field.name) for field in fields(cls)})

    def apply(self, event):
        from dataclasses import replace
        return replace(event, **{field.name: getattr(self, field.name) for field in fields(ActionSettings)})

    @classmethod
    def split(cls, record):
        """The declaration owner separates execution fields from a public record."""
        values = dict(record)
        actions = cls.from_dict({field.name: values.pop(field.name) for field in fields(cls) if field.name in values})
        return values, actions


@dataclass(frozen=True, slots=True)
class ActionLink(Record):
    connection: str = setting(Text(nonblank=True, single_line=True))
    calendar: str = setting(Text(nonblank=True, single_line=True))
    event: str = setting(Text(nonblank=True, single_line=True))
    series: str = setting(Text(single_line=True), default="",
        description="Parent ID for an explicit occurrence override; empty for an event or series binding.")

    @property
    def identity(self):
        return self.connection, self.calendar, self.event

    @property
    def source(self):
        return self.connection, self.calendar

    @classmethod
    def for_event(cls, source, event):
        return cls(*source.identity, event.id, event.series_id)


@dataclass(frozen=True, slots=True)
class ActionBinding(Record):
    link: ActionLink = setting(Nested(ActionLink))
    settings: ActionSettings = setting(Nested(ActionSettings), factory=ActionSettings)
    revision: int = setting(Number(minimum=0, integer=True), default=0)


@dataclass(frozen=True, slots=True)
class ActionCatalog:
    bindings: tuple[ActionBinding, ...] = ()

    def __post_init__(self):
        if any(not isinstance(item, ActionBinding) for item in self.bindings):
            raise ValueError("Expected calendar action bindings")
        if len({item.link.identity for item in self.bindings}) != len(self.bindings):
            raise ValueError("Duplicate calendar action bindings")

    def get(self, link):
        return next((item for item in self.bindings if item.link.identity == link.identity), None)

    def effective(self, link):
        exact = self.get(link)
        if exact is not None:
            return exact.settings
        parent = self.get(ActionLink(*link.source, link.series)) if link.series else None
        return parent.settings if parent else ActionSettings()

    def targets(self, source):
        return tuple(item for item in self.bindings if item.link.source == source.identity)
