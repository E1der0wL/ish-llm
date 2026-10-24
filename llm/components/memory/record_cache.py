"""An optional Memory decode cache, used only after byte inventory validation."""

from collections import OrderedDict
from copy import deepcopy
from typing import NamedTuple


class MemoryCacheEntry(NamedTuple):
    signature: tuple
    record: dict


class MemoryRecordCache(OrderedDict):
    ABSENT = None
    OLDEST = False

    def read(self, path, *, limit, decode):
        stat = path.stat()
        signature = (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
        cached = self.pop(str(path), self.ABSENT)
        record = cached.record if cached and cached.signature == signature else decode()
        if limit:
            self[str(path)] = MemoryCacheEntry(signature, deepcopy(record))
        while len(self) > limit:
            self.popitem(last=self.OLDEST)
        return deepcopy(record)
