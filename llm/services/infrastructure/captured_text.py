"""File-backed process output. Capture buffers never retain the complete stream."""

import codecs
import tempfile
import threading

from llm.services.infrastructure.text_stream import read_text_range


class CapturedText:
    def __init__(self):
        self.stream = tempfile.TemporaryFile()
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.size = self.characters = 0
        self.finished = False
        self.lock = threading.RLock()

    def __len__(self):
        return self.size

    def extend(self, value):
        with self.lock:
            if self.finished:
                raise ValueError("Cannot append to completed output")
            self.stream.seek(0, 2)
            self.stream.write(value)
            self.size += len(value)
            self.characters += len(self.decoder.decode(value))

    def finish(self):
        with self.lock:
            if not self.finished:
                self.characters += len(self.decoder.decode(b"", final=True))
                self.finished = True

    def read(self, offset, limit):
        with self.lock:
            self.stream.flush()
            self.stream.seek(0)
            return read_text_range(self.stream, offset=offset, limit=limit,
                                   errors="replace", final=self.finished)

    def close(self):
        with self.lock:
            self.stream.close()
