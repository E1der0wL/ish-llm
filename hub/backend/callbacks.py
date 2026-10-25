"""Optional worker callback arguments; no callback or operation is invoked here."""

from dataclasses import dataclass, fields


@dataclass(frozen=True, slots=True)
class CommandCallbacks:
    ABSENT = None
    acknowledged: object = ABSENT
    progress: object = ABSENT

    def to_kwargs(self):
        return {
            field.name: getattr(self, field.name)
            for field in fields(self)
            if getattr(self, field.name) is not CommandCallbacks.ABSENT
        }
