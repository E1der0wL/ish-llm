"""List text formatting; selection, storage and row geometry stay with owners."""

from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from .captions import Caption
from .summary import SummaryText, clipped_summary


@dataclass(frozen=True, slots=True)
class ListPresentation:
    title: str
    metadata: tuple[str, ...] = ()
    description: str = ""

    DATE_FORMAT: ClassVar[str] = "%Y-%m-%d %H:%M"
    UNKNOWN: ClassVar[str] = "—"

    @classmethod
    def timestamp(cls, value: str) -> str:
        try:
            return datetime.fromisoformat(value).astimezone().strftime(cls.DATE_FORMAT) if value else cls.UNKNOWN
        except (ValueError, TypeError):
            return cls.UNKNOWN

    @property
    def detail(self) -> str:
        return Caption(details=(*self.metadata, SummaryText.collapsed(self.description))).render()

    def line(self, width: int) -> str:
        return clipped_summary(Caption(self.title, details=(*self.metadata, self.description)).render(), width)

    def sidebar(self, key: str, *, margin_top: int = 0):
        from .sidebar import SidebarItem
        return SidebarItem(key, SummaryText.collapsed(self.title), self.detail, margin_top)
