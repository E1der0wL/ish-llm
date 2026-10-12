"""Local retry countdown for the assistant header; no storage reads."""

from math import ceil
from time import monotonic


def retry_text(retry, t):
    if retry is None:
        return ""
    if retry.retry_at is not None:
        return t("retry_wait", attempt=retry.attempt, maximum=retry.maximum,
            next_attempt=retry.attempt + 1, seconds=max(0, ceil(retry.retry_at - monotonic())))
    return t("retry_active", attempt=retry.attempt, maximum=retry.maximum)
