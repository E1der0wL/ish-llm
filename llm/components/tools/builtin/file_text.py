"""Disk-backed line positions for bounded file inspection; no user limits."""

import hashlib
import re
import struct
import tempfile
from dataclasses import dataclass

from llm.services.infrastructure.text_stream import text_blocks, read_text_range, BUFFER_BYTES, ENCODING
from .file_execution import FileReader, check_cancelled


class FileDigest:
    EXCEEDED = "File exceeds Tool byte limit"

    def __init__(self, stream, maximum):
        self.stream, self.maximum = stream, maximum
        self.digest, self.size = hashlib.sha256(), 0

    def read(self, size):
        block = self.stream.read(size)
        self.digest.update(block)
        self.size += len(block)
        if self.maximum is not None and self.size > self.maximum:
            raise ValueError(self.EXCEEDED)
        return block


@dataclass(frozen=True, slots=True)
class TextRange:
    text: str
    sha256: str
    has_more: bool

    @classmethod
    def read(cls, stream, *, offset, limit, max_bytes):
        reader = FileDigest(FileReader(stream), max_bytes)
        position, remaining, parts = 0, limit + 1, []
        # Validate/hash the whole source, retaining only the requested characters
        # and one lookahead. No line index or persistent validation cache exists.
        for block in text_blocks(reader):
            if remaining and position + len(block) > offset:
                page = block[max(0, offset - position):][:remaining]
                parts.append(page)
                remaining -= len(page)
            position += len(block)
        text = ''.join(parts)
        return cls(text[:limit], reader.digest.hexdigest(), len(text) > limit)


class TextIndex:
    BINARY_FILE = 'Binary file'
    ALLOW_NUL = False
    NO_CHARACTER_LIMIT = None
    KEEP_ENDINGS = True

    ROW = struct.Struct("QQ")
    NUL = "\x00"
    CR = "\r"
    LF = "\n"
    LINE_ENDINGS = LF + CR + "\v\f\x1c\x1d\x1e\x85\u2028\u2029"
    BREAK = re.compile(re.escape(CR + LF) + "|[" + re.escape(LINE_ENDINGS) + "]")

    def __init__(self, stream, *, max_bytes, reject_nul=ALLOW_NUL):
        stream = FileReader(stream)
        self.stream, self.rows = stream, tempfile.TemporaryFile()
        self.count = 0
        try:
            reader = FileDigest(stream, max_bytes)
            while reader.read(BUFFER_BYTES):
                pass
            self.sha256 = reader.digest.hexdigest()
            stream.seek(0)
            start, position, pending = 0, 0, ""
            for block in text_blocks(stream):
                if reject_nul and self.NUL in block:
                    raise ValueError(TextIndex.BINARY_FILE)
                block = pending + block
                pending = self.CR if block.endswith(self.CR) else ""
                if pending:
                    block = block[:-1]
                consumed = 0
                for match in self.BREAK.finditer(block):
                    position += len(block[consumed:match.end()].encode(ENCODING))
                    self.rows.write(self.ROW.pack(start, position - start))
                    self.count += 1
                    start, consumed = position, match.end()
                position += len(block[consumed:].encode(ENCODING))
            if pending:
                position += 1
            if position > start:
                self.rows.write(self.ROW.pack(start, position - start))
                self.count += 1
        except BaseException:
            self.rows.close()
            raise

    def close(self):
        self.rows.close()

    def bounds(self, line):
        check_cancelled()
        self.rows.seek((line - 1) * self.ROW.size)
        return self.ROW.unpack(self.rows.read(self.ROW.size))

    def read(self, line, *, max_chars=NO_CHARACTER_LIMIT, keepends=KEEP_ENDINGS):
        offset, size = self.bounds(line)
        self.stream.seek(offset)
        bounded = FileSlice(self.stream, size)
        text = read_text_range(bounded, offset=0, limit=max_chars)
        return text if keepends else text.rstrip(self.LINE_ENDINGS)

    def contains(self, line, needle, *, case_sensitive):
        offset, size = self.bounds(line)
        self.stream.seek(offset)
        tail = ""
        for text in text_blocks(FileSlice(self.stream, size)):
            if self.NUL in text:
                raise ValueError(TextIndex.BINARY_FILE)
            text = tail + (text if case_sensitive else text.casefold())
            if needle in text:
                return True
            tail = text[-max(0, len(needle) - 1):] if len(needle) > 1 else ""
        return False


class FileSlice:
    def __init__(self, stream, remaining):
        self.stream, self.remaining = stream, remaining

    def read(self, size):
        data = self.stream.read(min(size, self.remaining))
        self.remaining -= len(data)
        return data
