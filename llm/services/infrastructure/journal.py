"""출력 JSONL의 재생성 가능한 희소 위치 인덱스. 원본 저널만 실행 결과의 기준이다."""

import hashlib
import json
import os
from typing import Optional

from llm.core.results import EngineDelta, EngineOutput
from llm.services.infrastructure.storage import atomic_json, read_json, sync_directory


class OutputJournal:
    def __init__(self, *, index_stride: int = 128):
        if type(index_stride) is not int or index_stride < 0:
            raise ValueError("index_stride must be a nonnegative integer (0 disables indexing)")
        self.index_stride = index_stride

    @staticmethod
    def _decode(line):
        item = json.loads(line)
        if item["type"] not in ("delta", "output"):
            raise ValueError("Invalid output journal record")
        return (EngineDelta if item["type"] == "delta" else EngineOutput).from_dict(item["value"])

    @staticmethod
    def _digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def _index(self, path):
        cache = path.with_name("outputs.index.json")
        stat = path.stat()
        identity = [stat.st_dev, stat.st_ino]
        state = None
        try:
            stored = read_json(cache)
            value = stored["index"]
            if (stored["checksum"] == self._digest(value) and value["version"] == 1
                    and value["identity"] == identity and value["stride"] == self.index_stride
                    and 0 <= value["size"] <= stat.st_size
                    and (value["size"] < stat.st_size or value["mtime"] == stat.st_mtime_ns)):
                # 마지막 인덱스 구간도 원본에 실제로 존재하는지 확인한다.
                with path.open("rb") as stream:
                    stream.seek(value["tail_start"])
                    tail = stream.read(value["size"] - value["tail_start"])
                if hashlib.sha256(tail).hexdigest() == value["tail_hash"]:
                    state = value
        except (OSError, ValueError, KeyError, TypeError):
            pass
        if state is not None and state["size"] == stat.st_size:
            return state
        if state is None:
            state = {"version": 1, "identity": identity, "stride": self.index_stride,
                     "size": 0, "sequence": 0, "anchors": [], "tail_start": 0}
        with path.open("rb") as stream:
            stream.seek(state["size"])
            while True:
                offset = stream.tell()
                line = stream.readline()
                if not line or not line.endswith(b"\n"):
                    break
                value = self._decode(line)
                if value.sequence != state["sequence"] + 1:
                    raise ValueError("Invalid output journal sequence")
                if (value.sequence - 1) % self.index_stride == 0:
                    state["anchors"].append([value.sequence, offset])
                    state["tail_start"] = offset
                state["sequence"], state["size"] = value.sequence, stream.tell()
            stream.seek(state["tail_start"])
            state["tail_hash"] = hashlib.sha256(stream.read(state["size"] - state["tail_start"])).hexdigest()
        state["mtime"] = stat.st_mtime_ns
        try:
            atomic_json(cache, {"index": state, "checksum": self._digest(state)})
        except OSError:
            # 읽기 전용 백업에서도 원본 조회는 가능해야 한다.
            pass
        return state

    def append(self, path, values):
        records = [{"type": "delta" if isinstance(value, EngineDelta) else "output",
                    "value": value.to_dict()} for value in values]
        data = b"".join((json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
                        for item in records)
        if not data:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        first = not path.exists()
        with path.open("ab") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if first:
            sync_directory(path.parent)

    def read(self, path, *, after: int = 0, limit: Optional[int] = None):
        """인덱스는 최적화일 뿐이며 삭제/손상/파일 교체 시 원본에서 복구한다."""
        if type(after) is not int or after < 0 or (limit is not None and (type(limit) is not int or limit < 0)):
            raise ValueError("Output cursor/limit must be nonnegative integers")
        if not path.exists() or limit == 0:
            return []
        offset, sequence = 0, 0
        if after and self.index_stride:
            state = self._index(path)
            for number, position in reversed(state["anchors"]):
                if number <= after + 1:
                    offset, sequence = position, number - 1
                    break
        result = []
        with path.open("rb") as stream:
            stream.seek(offset)
            for line in stream:
                if not line.endswith(b"\n"):
                    break
                value = self._decode(line)
                if value.sequence != sequence + 1:
                    raise ValueError("Invalid output journal sequence")
                sequence = value.sequence
                if sequence > after:
                    result.append(value)
                    if limit is not None and len(result) >= limit:
                        break
        return result
