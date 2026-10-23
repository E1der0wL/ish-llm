"""Definition capability envelope; each Component still owns its record meaning."""

from dataclasses import dataclass, fields


@dataclass(frozen=True, slots=True)
class DefinitionSnapshot:
    configuration: dict
    records: dict

    def to_dict(self):
        return {item.name: getattr(self, item.name) for item in fields(self)}

    @classmethod
    def records_from(cls, value):
        """Read only the requested projection; preserve runtime capability extensions."""
        return value[cls.records.__name__]
