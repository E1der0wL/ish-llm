"""Application selection within the backend's editable reference bodies."""

from dataclasses import dataclass
import hashlib
import json


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


@dataclass(frozen=True, slots=True)
class Reduction:
    index: int
    source: str
    key: str


def reductions(failure, minimum):
    result = []
    for index in failure.editable:
        content = failure.request["messages"][index].get("content")
        if isinstance(content, str) and len(content) > minimum:
            result.append(Reduction(index, content, hashlib.sha256(content.encode()).hexdigest()))
    return result
