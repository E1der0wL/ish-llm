"""Hub Session creation modes; selection never grants Backend execution rights."""

from enum import StrEnum


class SessionMode(StrEnum):
    NEW = "new"
    CLONE = "clone"
