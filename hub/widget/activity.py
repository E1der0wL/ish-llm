"""One composer status row with local, storage-independent countdowns."""

from math import ceil
from time import monotonic


def activity_text(view):
    if view.question.item and view.question.busy:
        return view.t("question_sending", title=view.question.item["request"]["title"])
    if view.preparing.visible():
        return view.preparing.text()
    snapshot = getattr(view, "_last_snapshot", None)
    run = snapshot.run if snapshot else None
    retry = run.retry if run and run.active else None
    if retry:
        if retry.retry_at is not None:
            return view.t("retry_wait", attempt=retry.attempt, maximum=retry.maximum,
                next_attempt=retry.attempt + 1, seconds=max(0, ceil(retry.retry_at - monotonic())))
        return view.t("retry_active", attempt=retry.attempt, maximum=retry.maximum)
    return view.activity
