"""공통 목록 조건. 정렬된 원본에서 커서, 상태, 오프셋, 개수 순으로 선택한다."""

from typing import Optional
from functools import wraps
from collections import OrderedDict
from types import SimpleNamespace
from heapq import nsmallest, nlargest
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Query:
    """after는 마지막으로 본 ID, offset은 필터를 통과한 항목의 건너뛸 개수다."""

    after: Optional[str] = None
    status: Optional[str] = None
    offset: int = 0
    limit: Optional[int] = None
    descending: bool = False

    def __post_init__(self):
        for value in (self.offset, self.limit):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("Query offset/limit must be nonnegative integers")
        if self.offset is None or type(self.descending) is not bool:
            raise ValueError("Invalid query offset/descending")
        if self.after is not None and (not isinstance(self.after, str) or not self.after):
            raise ValueError("Query after must be a nonempty ID")
        if self.status is not None and (not isinstance(self.status, str) or not self.status):
            raise ValueError("Query status must be a nonempty string")

    def apply(self, items):
        """커서가 사라졌으면 오류를 내어 첫 페이지의 중복 반환을 방지한다."""
        # list/dict view는 역방향도 필요한 페이지까지만 소비한다.
        if self.descending:
            try:
                rows = reversed(items)
            except TypeError:
                rows = reversed(list(items))
        else:
            rows = iter(items)
        if self.after is not None:
            for item in rows:
                if getattr(item, "id", getattr(item, "run_id", None)) == self.after:
                    break
            else:
                raise ValueError("Query cursor does not exist in this scope")
        if self.status is not None:
            rows = (item for item in rows if getattr(item, "status",
                    "deleted" if getattr(item, "deleted", False) else "active") == self.status)
        if self.limit == 0:
            return []
        selected = []
        # Python 정수 전체 범위를 지원하므로 islice의 sys.maxsize 제한을 피한다.
        for index, item in enumerate(rows):
            if index < self.offset:
                continue
            selected.append(item)
            if self.limit is not None and len(selected) >= self.limit:
                break
        return selected


def select(items, query: Optional[Query] = None):
    """조건 없는 기존 호출의 반환 순서를 그대로 보존한다."""
    if query is not None and not isinstance(query, Query):
        raise TypeError("query must be Query or None")
    return query.apply(items) if query is not None else items


def queryable(method):
    """기존 저장소의 list 계약을 유지하면서 선택 조건을 공통 적용한다.

    파일 저장소는 정렬을 위해 메타데이터를 읽는다. 인덱스 저장소는 이 메서드를
    재정의하여 조건을 저장소에서 처리할 수 있다.
    """
    @wraps(method)
    def wrapped(self, *args, query=None, **kwargs):
        if query is not None and not isinstance(query, Query):
            raise TypeError("query must be Query or None")
        return select(method(self, *args, **kwargs), query)
    return wrapped


class FileCatalog:
    """목록용 작은 메타데이터만 캐시한다. 파일 변경을 stat으로 확인하고 선택한 항목만 완전히 로드한다."""

    def __init__(self, max_entries=4096, *, projection=None):
        if type(max_entries) is not int or max_entries < 0:
            raise ValueError("Catalog size must be nonnegative")
        self.max_entries = max_entries
        self.entries = OrderedDict()
        self.projection = projection or (lambda r: SimpleNamespace(
            id=r["id"], status=r["status"], created_at=r["created_at"]))

    def read(self, path):
        """파일 교체를 확인한 뒤 지정된 투영을 반환한다. 호출자는 같은 저장 잠금을 사용한다."""
        from llm.services.infrastructure.storage import read_domain_record
        stat = path.stat()
        signature = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        cached = self.entries.pop(path, None)
        if cached is None or cached[0] != signature:
            record = read_domain_record(path)
            if record["id"] != path.parent.name:
                raise ValueError("Catalog identity mismatch")
            cached = (signature, self.projection(record))
        if self.max_entries:
            self.entries[path] = cached
            while len(self.entries) > self.max_entries:
                self.entries.popitem(last=False)
        return cached[1]

    def select(self, paths, query):
        if not isinstance(query, Query):
            raise TypeError("query must be Query")
        rows = (self.read(path) for path in paths)
        key = lambda row: (row.created_at, row.id)
        if query.after is None and query.limit is not None and query.limit > 0:
            # 전체 파일의 변경/무결성은 확인하되 페이지에 필요한 메타데이터만 정렬한다.
            if query.status is not None:
                rows = (row for row in rows if getattr(row, "status",
                        "deleted" if getattr(row, "deleted", False) else "active") == query.status)
            choose = nlargest if query.descending else nsmallest
            rows = choose(query.offset + query.limit, rows, key=key)
        return [row.id for row in query.apply(sorted(rows, key=key))]
