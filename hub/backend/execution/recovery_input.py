"""Application selection within the backend's editable reference bodies."""

from dataclasses import dataclass
import hashlib
import json


class RecoveryEncoding:
    ENSURE_ASCII = False
    SORT_KEYS = True
    ALLOW_NAN = False


def encoded(value):
    return json.dumps(
        value,
        ensure_ascii=RecoveryEncoding.ENSURE_ASCII,
        sort_keys=RecoveryEncoding.SORT_KEYS,
        allow_nan=RecoveryEncoding.ALLOW_NAN,
    )


@dataclass(frozen=True, slots=True)
class Reduction:
    index: int
    source: str
    key: str


def reductions(failure, minimum):
    result = []
    for index in failure.editable:
        content = failure.request.messages[index].text
        if isinstance(content, str) and len(content) > minimum:
            result.append(
                Reduction(index, content, hashlib.sha256(content.encode()).hexdigest())
            )
    return result
