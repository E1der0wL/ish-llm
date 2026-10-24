"""Memory public review projections and operational event names."""

from .identity import MemoryIdentity


class MemoryEvents:
    PREFIX = MemoryIdentity.COMPONENT + "."
    EXTRACTED = PREFIX + "extracted"

    @classmethod
    def operation(cls, operation):
        return cls.PREFIX + operation


class MemoryReview:
    DUPLICATES = "duplicates"
    EXPIRED = "expired"
    PROPOSALS = "proposals"
    UNIQUE_COUNT = 1

    @classmethod
    def project(cls, groups, expired, proposals):
        return {cls.DUPLICATES: [ids for ids in groups.values() if len(ids) > cls.UNIQUE_COUNT],
                cls.EXPIRED: expired, cls.PROPOSALS: proposals}
