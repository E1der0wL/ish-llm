"""Local retry countdown for the assistant header; no storage reads."""

from math import ceil
from time import monotonic
from ...model import RetrySummary


def retry_text(retry, t):
    if retry is None:
        return ""
    if retry.kind == RetrySummary.RECOVERY:
        return t("recovery_" + retry.phase, attempt=retry.attempt, maximum=retry.maximum,
                 progress=(" · " + retry.name) if retry.name else "")
    target = t("retry_tool", name=retry.name) if retry.kind == RetrySummary.TOOL else t("retry_provider")
    values = dict(target=target, attempt=retry.attempt, maximum=retry.maximum, next_attempt=retry.attempt + 1)
    if retry.phase == RetrySummary.REPAIRING:
        return t("retry_repairing", **values)
    if retry.retry_at is not None:
        return t("retry_wait", **values, seconds=max(0, ceil(retry.retry_at - monotonic())))
    return t("retry_active", **values)
