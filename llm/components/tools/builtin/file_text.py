"""Disk-backed line positions for bounded file inspection; no user limits."""

import hashlib
import re
import struct
import tempfile

from llm.services.infrastructure.text_stream import text_blocks, read_text_range, BUFFER_BYTES


class TextIndex:
    ROW = struct.Struct("QQ")
    BREAK = re.compile(r"\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]")

    def __init__(self, stream, *, max_bytes, reject_nul=False):
        self.stream, self.rows = stream, tempfile.TemporaryFile()
        self.count = 0
        try:
            digest, size = hashlib.sha256(), 0
            while block := stream.read(BUFFER_BYTES):
                digest.update(block)
                size += len(block)
                if max_bytes is not None and size > max_bytes:
                    raise ValueError("File exceeds Tool byte limit")
            self.sha256 = digest.hexdigest()
            stream.seek(0)
            start, position, pending = 0, 0, ""
            for block in text_blocks(stream):
                if reject_nul and "\x00" in block:
                    raise ValueError("Binary file")
                block = pending + block
                pending = "\r" if block.endswith("\r") else ""
                if pending:
                    block = block[:-1]
                consumed = 0
                for match in self.BREAK.finditer(block):
                    position += len(block[consumed:match.end()].encode("utf-8"))
                    self.rows.write(self.ROW.pack(start, position - start))
                    self.count += 1
                    start, consumed = position, match.end()
                position += len(block[consumed:].encode("utf-8"))
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
        self.rows.seek((line - 1) * self.ROW.size)
        return self.ROW.unpack(self.rows.read(self.ROW.size))

    def read(self, line, *, max_chars=None, keepends=True):
        offset, size = self.bounds(line)
        self.stream.seek(offset)
        bounded = FileSlice(self.stream, size)
        text = read_text_range(bounded, offset=0, limit=max_chars)
        return text if keepends else text.rstrip("\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029")

    def contains(self, line, needle, *, case_sensitive):
        offset, size = self.bounds(line)
        self.stream.seek(offset)
        tail = ""
        for text in text_blocks(FileSlice(self.stream, size)):
            if "\x00" in text:
                raise ValueError("Binary file")
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
