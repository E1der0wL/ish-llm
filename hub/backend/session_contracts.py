"""Hub Session creation modes; selection never grants Backend execution rights."""

from enum import StrEnum


class SessionMode(StrEnum):
    NEW = "new"
    CLONE = "clone"


class InternalSession:
    """Shared UI visibility marker; not a backend access-control boundary."""

    KEY = "hub_internal"

    @classmethod
    def is_internal(cls, metadata):
        return bool(metadata.get(cls.KEY))
