"""공통 목록 조건. 정렬된 원본에서 커서, 상태, 오프셋, 개수 순으로 선택한다."""

from llm.services.infrastructure.storage import record_stat

from typing import Optional
from functools import wraps
from collections import OrderedDict
from heapq import nsmallest, nlargest
from dataclasses import dataclass
from typing import ClassVar

from llm.core.models import Project, Run
from llm.core.results import ExecutionResult


class QueryItem:
    """Common query projection; lifecycle fallback is only a listing label."""

    ACTIVE = "active"
    DELETED = "deleted"
    NOT_DELETED = False
    UNSPECIFIED = None

    @classmethod
    def identifier(cls, item):
        return getattr(
            item, Project.id.__name__, getattr(item, ExecutionResult.run_id.__name__, cls.UNSPECIFIED)
        )

    @classmethod
    def status(cls, item):
        return getattr(
            item,
            Run.status.__name__,
            cls.DELETED if getattr(item, Project.deleted.__name__, cls.NOT_DELETED) else cls.ACTIVE,
        )


@dataclass(frozen=True, slots=True)
class Query:
    """after는 마지막으로 본 ID, offset은 필터를 통과한 항목의 건너뛸 개수다."""

    UNSPECIFIED: ClassVar[None] = None
    MINIMUM_POSITION: ClassVar[int] = 0
    EMPTY_LIMIT: ClassVar[int] = 0
    INVALID_POSITION: ClassVar[str] = "Query offset/limit must be nonnegative integers"
    INVALID_ORDER: ClassVar[str] = "Invalid query offset/descending"
    INVALID_AFTER: ClassVar[str] = "Query after must be a nonempty ID"
    INVALID_STATUS: ClassVar[str] = "Query status must be a nonempty string"
    MISSING_CURSOR: ClassVar[str] = "Query cursor does not exist in this scope"
    INVALID_QUERY: ClassVar[str] = "query must be Query or None"

    after: Optional[str] = UNSPECIFIED
    status: Optional[str] = UNSPECIFIED
    offset: int = MINIMUM_POSITION
    limit: Optional[int] = UNSPECIFIED
    descending: bool = False

    def __post_init__(self):
        for value in (self.offset, self.limit):
            if value is not self.UNSPECIFIED and (type(value) is not int or value < self.MINIMUM_POSITION):
                raise ValueError(self.INVALID_POSITION)
        if self.offset is self.UNSPECIFIED or type(self.descending) is not bool:
            raise ValueError(self.INVALID_ORDER)
        if self.after is not self.UNSPECIFIED and (not isinstance(self.after, str) or not self.after):
            raise ValueError(self.INVALID_AFTER)
        if self.status is not self.UNSPECIFIED and (not isinstance(self.status, str) or not self.status):
            raise ValueError(self.INVALID_STATUS)

    @classmethod
    def validate_optional(cls, query):
        if query is not cls.UNSPECIFIED and not isinstance(query, Query):
            raise TypeError(cls.INVALID_QUERY)

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
        if self.after is not self.UNSPECIFIED:
            for item in rows:
                if QueryItem.identifier(item) == self.after:
                    break
            else:
                raise ValueError(self.MISSING_CURSOR)
        if self.status is not self.UNSPECIFIED:
            rows = (item for item in rows if QueryItem.status(item) == self.status)
        if self.limit == self.EMPTY_LIMIT:
            return []
        selected = []
        # Python 정수 전체 범위를 지원하므로 islice의 sys.maxsize 제한을 피한다.
        for index, item in enumerate(rows):
            if index < self.offset:
                continue
            selected.append(item)
            if self.limit is not self.UNSPECIFIED and len(selected) >= self.limit:
                break
        return selected


def select(items, query: Optional[Query] = Query.UNSPECIFIED):
    """조건 없는 기존 호출의 반환 순서를 그대로 보존한다."""
    Query.validate_optional(query)
    return query.apply(items) if query is not Query.UNSPECIFIED else items


def select_ordered(rows, query, *, key):
    """정렬 전 페이지 후보를 좁힌다. 커서가 있으면 전체 순서에서 먼저 커서를 확인한다."""
    Query.validate_optional(query)
    if (
        type(query) is Query
        and query.after is Query.UNSPECIFIED
        and query.limit is not Query.UNSPECIFIED
        and query.limit > Query.EMPTY_LIMIT
    ):
        if query.status is not Query.UNSPECIFIED:
            rows = (row for row in rows if QueryItem.status(row) == query.status)
        choose = nlargest if query.descending else nsmallest
        rows = choose(query.offset + query.limit, rows, key=key)
    return select(sorted(rows, key=key), query)


def queryable(method):
    """기존 저장소의 list 계약을 유지하면서 선택 조건을 공통 적용한다.

    파일 저장소는 정렬을 위해 메타데이터를 읽는다. 인덱스 저장소는 이 메서드를
    재정의하여 조건을 저장소에서 처리할 수 있다.
    """

    @wraps(method)
    def wrapped(self, *args, query=Query.UNSPECIFIED, **kwargs):
        Query.validate_optional(query)
        return select(method(self, *args, **kwargs), query)

    return wrapped


@dataclass
class CatalogRecord:
    """Listing projection after the storage-version and directory-identity guards.

    A catalog entry cannot stand in for the complete Run/Step Reader at use time.
    Custom projections still receive the original current-format record.
    """

    IDENTITY_MISMATCH: ClassVar[str] = "Catalog identity mismatch"

    id: str
    status: str
    created_at: str

    @classmethod
    def read(cls, data):
        return cls(data[Run.id.__name__], data[Run.status.__name__], data[Run.created_at.__name__])

    @classmethod
    def require_identity(cls, data, identifier):
        if data[Run.id.__name__] != identifier:
            raise ValueError(cls.IDENTITY_MISMATCH)


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    signature: tuple
    projection: object


class FileCatalog:
    """목록용 파생 메타데이터 캐시. 용량 0은 캐시를 끄며 영속 원본을 삭제하지 않는다.

    projection은 None일 때만 기본 투영을 사용한다. 캐시 용량의 기술 초기값은 이 owner가
    선언하며 Run/Step Repository도 같은 선언을 소비한다.
    """

    DEFAULT_MAX_ENTRIES = 4096
    MINIMUM_SIZE = 0
    UNSPECIFIED = None
    EVICT_OLDEST = False
    INVALID_SIZE = "Catalog size must be nonnegative"
    INVALID_PROJECTION = "Catalog projection must be callable or None"
    QUERY_REQUIRED = "query must be Query"

    def __init__(self, max_entries=DEFAULT_MAX_ENTRIES, *, projection=UNSPECIFIED):
        if type(max_entries) is not int or max_entries < self.MINIMUM_SIZE:
            raise ValueError(self.INVALID_SIZE)
        if projection is not self.UNSPECIFIED and not callable(projection):
            raise TypeError(self.INVALID_PROJECTION)
        self.max_entries = max_entries
        self.entries = OrderedDict()
        self.projection = CatalogRecord.read if projection is self.UNSPECIFIED else projection

    def read(self, path):
        """파일 교체를 확인한 뒤 지정된 투영을 반환한다. 호출자는 같은 저장 잠금을 사용한다."""
        from llm.services.infrastructure.storage import read_domain_record

        stat = record_stat(path)
        signature = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        cached = self.entries.pop(path, self.UNSPECIFIED)
        if cached is self.UNSPECIFIED or cached.signature != signature:
            record = read_domain_record(path)
            CatalogRecord.require_identity(record, path.parent.name)
            cached = CatalogEntry(signature, self.projection(record))
        if self.max_entries:
            self.entries[path] = cached
            while len(self.entries) > self.max_entries:
                self.entries.popitem(last=self.EVICT_OLDEST)
        return cached.projection

    def select(self, paths, query):
        if not isinstance(query, Query):
            raise TypeError(self.QUERY_REQUIRED)
        rows = (self.read(path) for path in paths)
        return [row.id for row in select_ordered(rows, query, key=lambda row: (row.created_at, row.id))]
