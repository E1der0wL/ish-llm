"""출력 JSONL의 재생성 가능한 희소 위치 인덱스. 원본 저널만 실행 결과의 기준이다."""

import hashlib
import json
import os
import re
from bisect import bisect_right
from typing import Optional

from llm.core.results import EngineDelta, EngineOutput
from llm.services.infrastructure.storage import atomic_json, atomic_bytes, read_json, append_bytes, reject_links


class OutputJournal:
    SCHEMA_VERSION = 1
    VERSION = 1
    DEFAULT_INDEX_STRIDE = 128

    def __init__(self, *, index_stride: int = DEFAULT_INDEX_STRIDE):
        if type(index_stride) is not int or index_stride < 0:
            raise ValueError("index_stride must be a nonnegative integer (0 disables indexing)")
        self.index_stride = index_stride

    def validate(self, path):
        self._source(path)

    @staticmethod
    def _decode(line):
        item = json.loads(line)
        if not isinstance(item, dict) or item.keys() != {"type", "value"} or item["type"] not in ("delta", "output"):
            raise ValueError("Invalid output journal record")
        return (EngineDelta if item["type"] == "delta" else EngineOutput).from_dict(item["value"])

    @staticmethod
    def _digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def _index(self, path, source):
        cache = reject_links(path.with_name("outputs.index.json"))
        stat = path.stat()
        identity = [stat.st_dev, stat.st_ino]
        state = None
        try:
            stored = read_json(cache)
            value = stored["index"]
            if not isinstance(value, dict) or value.keys() != {
                    "version", "identity", "stride", "size", "sequence", "anchors", "source_digest", "mtime"}:
                raise ValueError("Invalid output index fields")
            if type(value["version"]) is not int or value["version"] != self.VERSION:
                raise ValueError("Invalid output index version")
            if any(type(value[key]) is not int or value[key] < 0 for key in ("stride", "size", "sequence", "mtime")):
                raise ValueError("Invalid output index counters")
            if not isinstance(value["anchors"], list):
                raise ValueError("Invalid output index anchors")
            previous = [0, -1]
            for anchor in value["anchors"]:
                if (not isinstance(anchor, list) or len(anchor) != 2 or any(type(v) is not int for v in anchor)
                        or not previous[0] < anchor[0] <= value["sequence"]
                        or not previous[1] < anchor[1] < value["size"]):
                    raise ValueError("Invalid output index anchor")
                previous = anchor
            if (stored["checksum"] == self._digest(value)
                    and value["identity"] == identity and value["stride"] == self.index_stride
                    and 0 <= value["size"] <= stat.st_size
                    and (value["size"] < stat.st_size or value["mtime"] == stat.st_mtime_ns)):
                # A cached prefix is usable only if all of its source bytes
                # still match, not merely the last indexed range.
                with path.open("rb") as stream:
                    remaining, prefix = value["size"], hashlib.sha256()
                    while remaining:
                        chunk = stream.read(min(remaining, 65536))
                        if not chunk:
                            raise ValueError("Output index exceeds its source")
                        prefix.update(chunk)
                        remaining -= len(chunk)
                if prefix.hexdigest() == value["source_digest"]:
                    state = value
        except (OSError, ValueError, KeyError, TypeError):
            pass
        if state is not None and state["size"] == stat.st_size:
            if state["sequence"] == source["sequence"]:
                return state
            state = None
        if state is None:
            state = {"version": self.VERSION, "identity": identity, "stride": self.index_stride,
                     "size": 0, "sequence": 0, "anchors": []}
        with path.open("rb") as stream:
            stream.seek(state["size"])
            while True:
                offset = stream.tell()
                line = stream.readline()
                if not line:
                    break
                if not line.endswith(b"\n"):
                    raise ValueError("Output source has an unterminated record")
                value = self._decode(line)
                if value.sequence != state["sequence"] + 1:
                    raise ValueError("Invalid output journal sequence")
                if (value.sequence - 1) % self.index_stride == 0:
                    state["anchors"].append([value.sequence, offset])
                state["sequence"], state["size"] = value.sequence, stream.tell()
        if state["size"] != source["bytes"] or state["sequence"] != source["sequence"]:
            raise ValueError("Output source sequence or size mismatch")
        state["source_digest"] = source["sha256"]
        state["mtime"] = stat.st_mtime_ns
        try:
            atomic_json(cache, {"index": state, "checksum": self._digest(state)})
        except OSError:
            # 읽기 전용 백업에서도 원본 조회는 가능해야 한다.
            pass
        return state

    @staticmethod
    def _manifest_path(path):
        return reject_links(path.with_suffix(".manifest.json"))

    def initialize(self, path):
        """New output storage only; readers and append never recreate lost state."""
        reject_links(path)
        if any(item.exists() for item in (path, self._manifest_path(path), path.with_name("outputs.index.json"))):
            raise ValueError("Output storage already exists")
        atomic_bytes(path, b"")
        atomic_json(self._manifest_path(path), {"schema_version": self.SCHEMA_VERSION,
            "bytes": 0, "sequence": 0, "sha256": hashlib.sha256(b"").hexdigest()})

    def _source(self, path):
        """Validate the authoritative source before using or rebuilding its cache."""
        reject_links(path)
        value = read_json(self._manifest_path(path))
        if not isinstance(value, dict) or value.keys() != {"schema_version", "bytes", "sequence", "sha256"}:
            raise ValueError("Output manifest requires all current fields")
        if type(value["schema_version"]) is not int or value["schema_version"] != self.SCHEMA_VERSION:
            raise ValueError("Invalid output manifest version")
        if (type(value["bytes"]) is not int or value["bytes"] < 0
                or type(value["sequence"]) is not int or value["sequence"] < 0
                or not isinstance(value["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", value["sha256"])):
            raise ValueError("Invalid output manifest source evidence")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256")
            size = stream.seek(0, os.SEEK_END)
            if size != value["bytes"] or digest.hexdigest() != value["sha256"]:
                raise ValueError("Output source bytes or revision mismatch")
            if size == 0:
                if value["sequence"] != 0:
                    raise ValueError("Empty output source must have sequence zero")
            else:
                stream.seek(-1, os.SEEK_END)
                if stream.read(1) != b"\n":
                    raise ValueError("Output source has an unterminated record")
                offset, tail = size - 1, b""
                while offset:
                    count = min(offset, 65536)
                    offset -= count
                    stream.seek(offset)
                    chunk = stream.read(count)
                    tail = chunk + tail
                    if b"\n" in chunk:
                        tail = tail.rsplit(b"\n", 1)[1]
                        break
                if self._decode(tail).sequence != value["sequence"]:
                    raise ValueError("Output source final sequence mismatch")
        return value, digest

    def append(self, path, values):
        source, digest = self._source(path)
        records = []
        sequence = source["sequence"]
        for value in values:
            if not isinstance(value, (EngineDelta, EngineOutput)):
                raise TypeError("Output append requires a current output value")
            if value.sequence != sequence + 1:
                raise ValueError("Invalid output journal sequence")
            item = {"type": "delta" if isinstance(value, EngineDelta) else "output", "value": value.to_dict()}
            self._decode(json.dumps(item, ensure_ascii=False, allow_nan=False))
            records.append(item)
            sequence = value.sequence
        data = b"".join((json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
                        for item in records)
        if not data:
            return
        append_bytes(path, data)
        digest.update(data)
        atomic_json(self._manifest_path(path), {"schema_version": self.SCHEMA_VERSION,
            "bytes": source["bytes"] + len(data), "sequence": sequence, "sha256": digest.hexdigest()})

    def iter_events(self, path, *, after: int = 0, limit: Optional[int] = None):
        """잠금 범위 안에서 소비할 순차 조회. 파일은 완료/오류/close 시 닫힌다.

        인덱스는 최적화일 뿐이며 삭제/손상/파일 교체 시 원본에서 복구한다.
        """
        if type(after) is not int or after < 0 or (limit is not None and (type(limit) is not int or limit < 0)):
            raise ValueError("Output cursor/limit must be nonnegative integers")
        source, _ = self._source(path)
        if limit == 0:
            return
        offset, sequence = 0, 0
        if after and self.index_stride:
            state = self._index(path, source)
            # 두 번째 정렬 키는 실제 파일 크기보다 크게 하여 같은 sequence도 포함한다.
            index = bisect_right(state["anchors"], [after + 1, state["size"]]) - 1
            if index >= 0:
                number, offset = state["anchors"][index]
                sequence = number - 1
        count = 0
        with path.open("rb") as stream:
            stream.seek(offset)
            for line in stream:
                if not line.endswith(b"\n"):
                    raise ValueError("Output source has an unterminated record")
                value = self._decode(line)
                if value.sequence != sequence + 1:
                    raise ValueError("Invalid output journal sequence")
                sequence = value.sequence
                if sequence > after:
                    yield value
                    count += 1
                    if limit is not None and count >= limit:
                        break

    def read(self, path, *, after: int = 0, limit: Optional[int] = None):
        """공개 목록 계약은 유지하고 내부 복구/투영은 순차 소비할 수 있게 한다."""
        return list(self.iter_events(path, after=after, limit=limit))
