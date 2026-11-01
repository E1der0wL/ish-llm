"""Cooperative CPU preparation for strict, replaceable render surfaces."""

from time import perf_counter


class RenderWork:
    SLICE_SECONDS = .008
    ROW_BATCH = 128

    def __init__(self, iterator):
        self.iterator = iter(iterator)
        self.result = None

    def advance(self):
        deadline = perf_counter() + self.SLICE_SECONDS
        while True:
            try:
                next(self.iterator)
            except StopIteration as completed:
                self.result = completed.value
                return True
            if perf_counter() >= deadline:
                return False

    def finish(self):
        while not self.advance():
            pass
        return self.result

    def close(self):
        self.iterator.close()
