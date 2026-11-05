"""Explicitly bounded reuse of verified UTF-8 bytes on a local filesystem."""

from collections import OrderedDict
from io import BytesIO
import os

from llm.services.infrastructure.text_stream import read_text_range
from .file_execution import FileReader, check_cancelled
from .file_text import TextRange, FileDigest


class VerifiedTextCache:
    """Toolkit-owned, lock-protected optimization; never a permission cache.

    Identity includes inode, device, mode, size and nanosecond mtime/ctime.
    The caller still securely opens the file and checks grants each time. Only
    local filesystems with coherent stat/change metadata may use this opt-in.
    max_bytes bounds retained source bytes (empty entries consume one unit);
    Python bookkeeping and transient decoding are separate implementation memory.
    """

    MIN_BYTES = 1  # Positive capacity and one bookkeeping unit for an empty entry.

    def __init__(self, max_bytes):
        if type(max_bytes) is not int or max_bytes < self.MIN_BYTES:
            raise ValueError('read_cache_bytes must be an explicit positive integer')
        self.max_bytes, self.size = max_bytes, 0
        self.entries = OrderedDict()

    @staticmethod
    def identity(observation):
        return (observation.st_dev, observation.st_ino, observation.st_mode,
                observation.st_size, observation.st_mtime_ns, observation.st_ctime_ns)

    def clear(self):
        self.entries.clear()
        self.size = 0

    def read(self, stream, before, *, offset, limit, max_bytes):
        check_cancelled()
        key = self.identity(before)
        if max_bytes is not None and before.st_size > max_bytes:
            raise ValueError(FileDigest.EXCEEDED)
        entry = self.entries.get(key)
        if entry is not None:
            data, digest = entry
            self.entries.move_to_end(key)
            text = read_text_range(FileReader(BytesIO(data)), offset=offset, limit=limit + 1)
            page = TextRange(text[:limit], digest, len(text) > limit)
        else:
            capture = _Capture(stream, self.max_bytes) if before.st_size <= self.max_bytes else None
            page = TextRange.read(capture if capture is not None else stream,
                                  offset=offset, limit=limit, max_bytes=max_bytes)
        check_cancelled()
        if self.identity(os.fstat(stream.fileno())) != key:
            raise ValueError('File changed during verified range read')
        if entry is None and capture is not None and capture.data is not None:
            data = bytes(capture.data)
            weight = max(self.MIN_BYTES, len(data))
            while self.entries and self.size + weight > self.max_bytes:
                _, (old, _) = self.entries.popitem(last=False)
                self.size -= max(self.MIN_BYTES, len(old))
            self.entries[key] = data, page.sha256
            self.size += weight
        return page


class _Capture:
    def __init__(self, stream, maximum):
        self.stream, self.maximum, self.data = stream, maximum, bytearray()

    def read(self, size):
        block = self.stream.read(size)
        if self.data is not None:
            if len(self.data) + len(block) <= self.maximum:
                self.data.extend(block)
            else:
                self.data = None  # A growing/oversized source is not retained.
        return block
