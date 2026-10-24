"""Hub presentation notification categories, independent of diagnostic severity."""

from enum import StrEnum


class NotificationKind(StrEnum):
    ERROR = "error"
    INFO = "info"
    WARNING = "warning"
    SUCCESS = "success"

    @classmethod
    def values(cls):
        return [kind.value for kind in cls]
