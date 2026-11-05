"""File-backed process output. Capture buffers never retain the complete stream."""

import codecs
import os
import tempfile
import threading

from llm.services.infrastructure.text_stream import read_text_range, COMPLETE, INCOMPLETE, EMPTY_BYTES
from .process_result import ProcessResult


class CapturedText:
    APPEND_FINISHED = 'Cannot append to completed output'

    def __init__(self):
        self.stream = tempfile.TemporaryFile()
        self.decoder = codecs.getincrementaldecoder(ProcessResult.ENCODING)(errors=ProcessResult.ERRORS)
        self.size = self.characters = 0
        self.finished = INCOMPLETE
        self.lock = threading.RLock()

    def __len__(self):
        return self.size

    def extend(self, value):
        with self.lock:
            if self.finished:
                raise ValueError(CapturedText.APPEND_FINISHED)
            self.stream.seek(0, os.SEEK_END)
            self.stream.write(value)
            self.size += len(value)
            self.characters += len(self.decoder.decode(value))

    def finish(self):
        with self.lock:
            if not self.finished:
                self.characters += len(self.decoder.decode(EMPTY_BYTES, final=COMPLETE))
                self.finished = COMPLETE

    def read(self, offset, limit):
        with self.lock:
            self.stream.flush()
            self.stream.seek(0)
            return read_text_range(self.stream, offset=offset, limit=limit,
                                   errors=ProcessResult.ERRORS, final=self.finished)

    def close(self):
        with self.lock:
            self.stream.close()
