"""Disposable corpus projections. Generation files remain the source of truth."""

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from threading import RLock
import sys


@dataclass(frozen=True, slots=True)
class CacheEntry:
    value: object
    size: int


class SnapshotCache:
    """Bound retained Python payload bytes, with independent copies for every reader."""

    EMPTY_BYTES = 0
    EVICT_NEWEST = False

    def __init__(self):
        self.entries, self.bytes = OrderedDict(), self.EMPTY_BYTES
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
            _, entry = self.entries.popitem(last=self.EVICT_NEWEST)
            self.bytes -= entry.size

    def get(self, key, maximum):
        with self.lock:
            self._trim(maximum)
            entry = self.entries.pop(key, None)
            if entry is None:
                return None
            self.entries[key] = entry
            return deepcopy(entry.value)

    def put(self, key, value, maximum):
        if not maximum:
            return
        size = self._size((key, value))
        with self.lock:
            previous = self.entries.pop(key, None)
            if previous is not None:
                self.bytes -= previous.size
            self._trim(maximum)
            if size > maximum:
                return
            self._trim(maximum - size)
            self.entries[key] = CacheEntry(deepcopy(value), size)
            self.bytes += size
