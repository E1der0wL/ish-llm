"""Disposable corpus projections. Generation files remain the source of truth."""

from collections import OrderedDict
from copy import deepcopy
from threading import RLock
import sys


class SnapshotCache:
    """Bound retained Python payload bytes, with independent copies for every reader."""

    def __init__(self):
        self.entries, self.bytes = OrderedDict(), 0
        self.lock = RLock()

    @staticmethod
    def _size(value):
        seen, pending, size = set(), [value], 0
        while pending:
            item = pending.pop()
            if id(item) in seen:
                continue
            seen.add(id(item))
            size += sys.getsizeof(item)
            if isinstance(item, dict):
                pending.extend(item.keys())
                pending.extend(item.values())
            elif isinstance(item, (list, tuple)):
                pending.extend(item)
        return size

    def _trim(self, maximum):
        while self.entries and self.bytes > maximum:
            _, (_, size) = self.entries.popitem(last=False)
            self.bytes -= size

    def get(self, key, maximum):
        with self.lock:
            self._trim(maximum)
            entry = self.entries.pop(key, None)
            if entry is None:
                return None
            self.entries[key] = entry
            return deepcopy(entry[0])

    def put(self, key, value, maximum):
        if not maximum:
            return
        size = self._size((key, value))
        with self.lock:
            previous = self.entries.pop(key, None)
            if previous is not None:
                self.bytes -= previous[1]
            self._trim(maximum)
            if size > maximum:
                return
            self._trim(maximum - size)
            self.entries[key] = (deepcopy(value), size)
            self.bytes += size
