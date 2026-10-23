"""The mutable ToolExecutor result channel shared with trusted event handlers."""

from copy import deepcopy


class ToolEventResult:
    VALUE = "value"
    CONTENT = "content"
    PAUSED = "paused"
    RETRY = "retry"
    IS_PAUSED = True

    def __init__(self, target):
        # This is a live output sink. A copied dict would hide a Host handler's
        # writes from its caller and could incorrectly report missing effects.
        self._target = target

    @property
    def value(self):
        return self._target[self.VALUE]

    @property
    def content(self):
        return self._target[self.CONTENT]

    @property
    def paused(self):
        return self._target.get(self.PAUSED)

    def pause(self):
        self._target[self.PAUSED] = self.IS_PAUSED

    def yielded(self, value):
        self._target[self.VALUE] = value

    def complete(self, value, content):
        self._target.update({self.VALUE: deepcopy(value), self.CONTENT: content})

    def record_retry(self, retry):
        self._target[self.RETRY] = deepcopy(retry)
