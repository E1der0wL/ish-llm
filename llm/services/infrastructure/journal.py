"""출력 JSONL의 재생성 가능한 희소 위치 인덱스. 원본 저널만 실행 결과의 기준이다."""

from llm.services.infrastructure.storage import open_record, record_stat, record_exists

import hashlib
import json
import os
from typing import Optional

from llm.core.results import EngineDelta, EngineOutput
from .output_records import OutputEventRecord, OutputSource, OutputIndex, OutputIndexEnvelope
from llm.services.infrastructure.storage import (
    atomic_json,
    atomic_bytes,
    read_json,
    append_bytes,
    reject_links,
)


class OutputJournal:
    SCHEMA_VERSION = OutputSource.SCHEMA_VERSION
    VERSION = OutputIndex.VERSION
    DEFAULT_INDEX_STRIDE = 128
    INVALID_STRIDE = "index_stride must be a nonnegative integer (0 disables indexing)"
    BINARY_READ = "rb"
    HASH_NAME = "sha256"
    EMPTY_BYTES = b""
    LINE_END = b"\n"
    TEXT_LINE_END = "\n"
    ENCODING = "utf-8"
    HASH_BLOCK_BYTES = 65536
    INDEX_EXCEEDS_SOURCE = "Output index exceeds its source"
    UNTERMINATED = "Output source has an unterminated record"
    INVALID_SEQUENCE = "Invalid output journal sequence"
    SOURCE_MISMATCH = "Output source sequence or size mismatch"
    ALREADY_EXISTS = "Output storage already exists"
    REVISION_MISMATCH = "Output source bytes or revision mismatch"
    INVALID_EMPTY = "Empty output source must have sequence zero"
    FINAL_MISMATCH = "Output source final sequence mismatch"
    INVALID_APPEND = "Output append requires a current output value"
    INVALID_CURSOR = "Output cursor/limit must be nonnegative integers"
    ABSENT = None
    ZERO = 0
    ONE = 1
    JSON_ASCII = False
    ALLOW_NAN = False

    def __init__(self, *, index_stride: int = DEFAULT_INDEX_STRIDE):
        if type(index_stride) is not int or index_stride < OutputJournal.ZERO:
            raise ValueError(OutputJournal.INVALID_STRIDE)
        self.index_stride = index_stride

    def validate(self, path):
        self._source(path)

    @staticmethod
    def _decode(line):
        return OutputEventRecord.read(json.loads(line))

    @staticmethod
    def _digest(value):
        return OutputIndexEnvelope.digest(value)

    def _index(self, path, source):
        cache = reject_links(path.with_name(OutputIndex.FILE))
        stat = record_stat(path)
        identity = [stat.st_dev, stat.st_ino]
        state = OutputJournal.ABSENT
        try:
            stored = OutputIndexEnvelope(read_json(cache))
            value = OutputIndex.read(stored.index, self.VERSION)
            if value.matches(stored.checksum, self._digest, identity, self.index_stride, stat):
                # A cached prefix is usable only if all of its source bytes
                # still match, not merely the last indexed range.
                with open_record(path) as stream:
                    remaining, prefix = value.size, hashlib.sha256()
                    while remaining:
                        chunk = stream.read(min(remaining, OutputJournal.HASH_BLOCK_BYTES))
                        if not chunk:
                            raise ValueError(OutputJournal.INDEX_EXCEEDS_SOURCE)
                        prefix.update(chunk)
                        remaining -= len(chunk)
                if prefix.hexdigest() == value.source_digest:
                    state = value
        except (OSError, ValueError, KeyError, TypeError):
            pass
        if state is not OutputJournal.ABSENT and state.size == stat.st_size:
            if state.sequence == source.sequence:
                return state
            state = OutputJournal.ABSENT
        if state is OutputJournal.ABSENT:
            state = OutputIndex.start(self.VERSION, identity, self.index_stride)
        with open_record(path) as stream:
            stream.seek(state.size)
            while True:
                offset = stream.tell()
                line = stream.readline()
                if not line:
                    break
                if not line.endswith(OutputJournal.LINE_END):
                    raise ValueError(OutputJournal.UNTERMINATED)
                value = self._decode(line)
                if value.sequence != state.sequence + OutputJournal.ONE:
                    raise ValueError(OutputJournal.INVALID_SEQUENCE)
                if (value.sequence - OutputJournal.ONE) % self.index_stride == OutputJournal.ZERO:
                    state.anchor(value.sequence, offset)
                state.advance(value.sequence, stream.tell())
        if state.size != source.byte_count or state.sequence != source.sequence:
            raise ValueError(OutputJournal.SOURCE_MISMATCH)
        state.seal(source, stat.st_mtime_ns)
        try:
            atomic_json(cache, OutputIndexEnvelope.create(state.record, self._digest(state.record)))
        except OSError:
            # 읽기 전용 백업에서도 원본 조회는 가능해야 한다.
            pass
        return state

    @staticmethod
    def _manifest_path(path):
        return reject_links(path.with_suffix(OutputSource.SUFFIX))

    def initialize(self, path):
        """New output storage only; readers and append never recreate lost state."""
        reject_links(path)
        if any(record_exists(item) for item in (path, self._manifest_path(path), path.with_name(OutputIndex.FILE))):
            raise ValueError(OutputJournal.ALREADY_EXISTS)
        atomic_bytes(path, OutputJournal.EMPTY_BYTES)
        atomic_json(self._manifest_path(path), OutputSource.empty(self.SCHEMA_VERSION).record)

    def _source(self, path):
        """Validate the authoritative source before using or rebuilding its cache."""
        reject_links(path)
        value = OutputSource.read(read_json(self._manifest_path(path)), self.SCHEMA_VERSION)
        with open_record(path) as stream:
            digest = hashlib.file_digest(stream, OutputJournal.HASH_NAME)
            size = stream.seek(OutputJournal.ZERO, os.SEEK_END)
            if size != value.byte_count or digest.hexdigest() != value.digest:
                raise ValueError(OutputJournal.REVISION_MISMATCH)
            if size == OutputJournal.ZERO:
                if value.sequence != OutputJournal.ZERO:
                    raise ValueError(OutputJournal.INVALID_EMPTY)
            else:
                stream.seek(-OutputJournal.ONE, os.SEEK_END)
                if stream.read(OutputJournal.ONE) != OutputJournal.LINE_END:
                    raise ValueError(OutputJournal.UNTERMINATED)
                offset, tail = size - OutputJournal.ONE, OutputJournal.EMPTY_BYTES
                while offset:
                    count = min(offset, OutputJournal.HASH_BLOCK_BYTES)
                    offset -= count
                    stream.seek(offset)
                    chunk = stream.read(count)
                    tail = chunk + tail
                    if OutputJournal.LINE_END in chunk:
                        tail = tail.rsplit(OutputJournal.LINE_END, OutputJournal.ONE)[OutputJournal.ONE]
                        break
                if self._decode(tail).sequence != value.sequence:
                    raise ValueError(OutputJournal.FINAL_MISMATCH)
        return value, digest

    def append(self, path, values):
        source, digest = self._source(path)
        records = []
        sequence = source.sequence
        for value in values:
            if not isinstance(value, (EngineDelta, EngineOutput)):
                raise TypeError(OutputJournal.INVALID_APPEND)
            if value.sequence != sequence + OutputJournal.ONE:
                raise ValueError(OutputJournal.INVALID_SEQUENCE)
            item = OutputEventRecord.create(value)
            self._decode(
                json.dumps(item, ensure_ascii=OutputJournal.JSON_ASCII, allow_nan=OutputJournal.ALLOW_NAN)
            )
            records.append(item)
            sequence = value.sequence
        data = OutputJournal.EMPTY_BYTES.join(
            (
                json.dumps(item, ensure_ascii=OutputJournal.JSON_ASCII, allow_nan=OutputJournal.ALLOW_NAN)
                + OutputJournal.TEXT_LINE_END
            ).encode(OutputJournal.ENCODING)
            for item in records
        )
        if not data:
            return
        append_bytes(path, data)
        digest.update(data)
        atomic_json(
            self._manifest_path(path),
            OutputSource.create(
                self.SCHEMA_VERSION, source.byte_count + len(data), sequence, digest.hexdigest()
            ).record,
        )

    def iter_events(self, path, *, after: int = ZERO, limit: Optional[int] = ABSENT):
        """잠금 범위 안에서 소비할 순차 조회. 파일은 완료/오류/close 시 닫힌다.

        인덱스는 최적화일 뿐이며 삭제/손상/파일 교체 시 원본에서 복구한다.
        """
        if (
            type(after) is not int
            or after < OutputJournal.ZERO
            or (limit is not OutputJournal.ABSENT and (type(limit) is not int or limit < OutputJournal.ZERO))
        ):
            raise ValueError(OutputJournal.INVALID_CURSOR)
        source, _ = self._source(path)
        if limit == OutputJournal.ZERO:
            return
        offset, sequence = OutputJournal.ZERO, OutputJournal.ZERO
        if after and self.index_stride:
            state = self._index(path, source)
            offset, sequence = state.cursor(after)
        count = OutputJournal.ZERO
        with open_record(path) as stream:
            stream.seek(offset)
            for line in stream:
                if not line.endswith(OutputJournal.LINE_END):
                    raise ValueError(OutputJournal.UNTERMINATED)
                value = self._decode(line)
                if value.sequence != sequence + OutputJournal.ONE:
                    raise ValueError(OutputJournal.INVALID_SEQUENCE)
                sequence = value.sequence
                if sequence > after:
                    yield value
                    count += OutputJournal.ONE
                    if limit is not OutputJournal.ABSENT and count >= limit:
                        break

    def read(self, path, *, after: int = ZERO, limit: Optional[int] = ABSENT):
        """공개 목록 계약은 유지하고 내부 복구/투영은 순차 소비할 수 있게 한다."""
        return list(self.iter_events(path, after=after, limit=limit))
