"""Declarative Hub toolboxes, intersected with the Agent's allowed Tools."""

from dataclasses import dataclass
from types import MappingProxyType

from .request_profile import RequestMode
from .tool_catalog import ToolCatalog, ToolTag


@dataclass(frozen=True, slots=True)
class RequestToolSet:
    any_tags: frozenset[ToolTag] = frozenset()
    all_tags: frozenset[ToolTag] = frozenset()
    excluded_tags: frozenset[ToolTag] = frozenset()
    preserve_selection: bool = False

    def names(self, catalog: ToolCatalog) -> tuple[str, ...]:
        if self.preserve_selection:
            return tuple(entry.name for entry in catalog.entries)
        return tuple(entry.name for entry in catalog.entries
            if (self.any_tags or self.all_tags)
            and (not self.any_tags or not self.any_tags.isdisjoint(entry.tags))
            and self.all_tags <= entry.tags
            and self.excluded_tags.isdisjoint(entry.tags))


class RequestTools:
    QUESTIONS = frozenset((ToolTag.QUESTION,))
    BASIC = QUESTIONS | {ToolTag.SHELL}
    PROFILES = MappingProxyType({
        RequestMode.CONVERSATION: RequestToolSet(any_tags=QUESTIONS),
        RequestMode.LOOKUP: RequestToolSet(any_tags=BASIC | {ToolTag.READ},
                                         excluded_tags=frozenset((ToolTag.REPORT,))),
        RequestMode.WORK: RequestToolSet(preserve_selection=True),
    })

    @classmethod
    def names(cls, catalog: ToolCatalog, mode: RequestMode) -> tuple[str, ...]:
        return cls.PROFILES[mode].names(catalog)
