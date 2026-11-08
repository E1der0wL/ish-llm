"""Hub-owned category proportions, separate from editable preferences."""

from dataclasses import asdict, dataclass, fields, replace
from typing import ClassVar, Self
import math


@dataclass(frozen=True, slots=True)
class SidebarHeights:
    FILE: ClassVar[str] = "sidebar-heights.json"
    AUTOMATIC: ClassVar[None] = None
    MIN_RATIO: ClassVar[float] = 0.0
    MAX_RATIO: ClassVar[float] = 1.0
    INVALID: ClassVar[str] = "Sidebar heights require home and settings ratios between 0 and 1"

    home: float = 0.27
    settings: float | None = AUTOMATIC

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if item.name == type(self).settings.__name__ and value is self.AUTOMATIC:
                continue
            if (type(value) not in (int, float) or not math.isfinite(value)
                    or not self.MIN_RATIO < value < self.MAX_RATIO):
                raise ValueError(self.INVALID)

    def ratio(self, page: str) -> float | None:
        if page not in {item.name for item in fields(self)}:
            raise ValueError(self.INVALID)
        return getattr(self, page)

    def resized(self, page: str, ratio: float | None) -> Self:
        self.ratio(page)
        return replace(self, **{page: ratio})

    def to_dict(self) -> dict[str, float | None]:
        return asdict(self)

    @classmethod
    def read(cls, value: object) -> Self:
        if not isinstance(value, dict) or value.keys() != {item.name for item in fields(cls)}:
            raise ValueError(cls.INVALID)
        return cls(**value)
