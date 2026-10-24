"""기억의 현재 값·출처·수정 이력을 한 JSON 파일로 원자적으로 저장한다."""

from llm.core.history_references import HistoryReferenceRecord

from llm.components.processing import COMPLETION_PROCESSORS
from llm.components.tools.registry import ToolRegistry
from copy import deepcopy
from datetime import datetime, timezone

from llm.components.base import Component, validate_name
from llm.core.models import new_id
from llm.errors import CodedError
from .data import MemoryData
from .identity import MemoryIdentity
from .search import MemorySearch
from .record_cache import MemoryRecordCache
from .dependencies import MemoryDependencies
from llm.components.files import FileData
from .summary_storage import SummaryStorage
from .summary_records import SummaryRecord
from .records import MemoryRecord
from .history import MemoryHistory, MemoryEnvelope
from .sources import MemorySource
from .processing import MemoryProcessor
from .configuration import MemoryConfiguration, MemoryProcessingConfig
from llm.services.infrastructure.storage import atomic_json, read_json


class MemoryConflictError(CodedError, RuntimeError):
    """읽은 뒤 기억이 변경되었다. 최신 revision을 확인하고 명시적으로 다시 수정한다."""

    code = "memory_conflict"


class MemoryComponent(Component):
    """프로젝트 단위 장기 기억. 모델이나 실행 엔진 없이도 CRUD를 사용할 수 있다.

    직접 호출에는 workspace 소유권이 필요하다. 일반 사용자는 잠금·수명 검사가 있는
    project.components.memory를 사용한다. 검색을 바꾸려면 search/score를 재정의한다.
    """

    name = MemoryIdentity.COMPONENT
    directory = name
    CANDIDATE = MemoryRecord.CANDIDATE
    CONFIRMED = MemoryRecord.CONFIRMED
    STATUSES = MemoryRecord.STATUSES
    ALL = MemoryRecord.ALL
    PROJECT_SCOPE, SESSION_SCOPE = MemoryRecord.PROJECT_SCOPE, MemoryRecord.SESSION_SCOPE
    SCOPES = MemoryRecord.SCOPES
    KEYWORD = MemoryConfiguration.KEYWORD
    CREATE, SAVE, UPDATE, DELETE, RESTORE = (MemoryHistory.CREATE, MemoryHistory.SAVE,
                                           MemoryHistory.UPDATE, MemoryHistory.DELETE, MemoryHistory.RESTORE)
    OPERATIONS = MemoryHistory.OPERATIONS
    HISTORY_FIELDS = MemoryHistory.FIELDS
    HISTORY_IDENTITY = MemoryRecord.IMMUTABLE
    capabilities = (name, ToolRegistry.CAPABILITY, COMPLETION_PROCESSORS)
    ABSENT = None
    LIVE_ONLY = False
    INCLUDE_DELETED = True
    SINGLE_STATUS = False
    ALL_STATUSES = True
    PENDING_CONSOLIDATION = "Memory consolidation is pending; recover before reading or editing"
    OUTSIDE_SCOPE = "Memory is outside the requested scope"
    ALREADY_EXISTS = "Memory already exists"
    REQUIRE_SOFT_DELETE = "Soft-delete memory before permanent removal"
    DESTINATION_EXISTS = "Destination memory already exists"
    REQUIRE_DATA_FACTORY = "Memory capabilities require a lifecycle-bound component data factory"
    REQUIRE_FILE_INTERFACE = "tool_data requires a file Component public interface"
    data_class = MemoryData
    parameter_layout = MemoryConfiguration.layout()
    _managed = MemoryRecord.MANAGED
    content_schema = MemoryRecord.content_schema(scopes=SCOPES, statuses=STATUSES)

    def __init__(self, *, completion_adapter=ABSENT, token_counter=ABSENT, search_fn=ABSENT):
        """보조 모델과 토큰 계수기는 런타임에 주입한다. 설정/레코드에는 저장하지 않는다."""
        MemoryDependencies.validate_callables(token_counter, search_fn)
        self.completion_adapter = MemoryDependencies.completion(completion_adapter)
        self.token_counter = token_counter
        self.search_fn = search_fn
        self._record_cache = MemoryRecordCache()

    def _assert_ready(self, project):
        from .consolidation import MemoryConsolidation
        if MemoryConsolidation(self, project).pending():
            raise MemoryConflictError(self.PENDING_CONSOLIDATION)

    def _all_records(self, project):
        self._assert_ready(project)
        if not self._has_storage(project):
            return
        limit = MemoryConfiguration(self._options(project)).cache_records
        _, records = self._record_inventory(project)
        for identifier, raw in records.items():
            path = self._record_path(project, identifier)
            yield path.stem, self._record_cache.read(path, limit=limit,
                decode=lambda: MemoryEnvelope(self._decode_record(identifier, raw)).record)

    def _summary_path(self, project, session_id):
        return SummaryStorage(project, self).path(session_id)

    def _validate_summary(self, session_id, value):
        return SummaryRecord.validate(session_id, value)

    @classmethod
    def _visible(cls, record, session_id):
        return MemoryRecord(record).visible(session_id, project_scope=cls.PROJECT_SCOPE)

    @staticmethod
    def _expired(record):
        return MemoryRecord(record).expired()

    def _payload(self, data):
        value = self.deserialize(self.serialize(data))
        MemoryRecord.validate_content(value, managed=self._managed, schema=self.content_schema)
        return value

    def _read(self, project, identifier):
        self._assert_ready(project)
        return super().load(project, identifier)

    def _source(self, project, source):
        value = self.deserialize(self.serialize(MemorySource.api_input(source)))
        return MemorySource.ensure_project(value, project.id)

    def _commit(self, project, identifier, record, history, operation, source):
        self._assert_ready(project)
        selected = MemoryRecord(record)
        entry = MemoryHistory.entry(revision=selected.revision, operation=operation,
            at=selected.updated_at, source=self._source(project, source), data=deepcopy(record))
        self._write(project, identifier, MemoryEnvelope.append(record, history, entry))
        return deepcopy(record)

    def _change(self, project, identifier, expected_revision, expected_generation, operation, *, changes=ABSENT, source=ABSENT, session_id=ABSENT):
        envelope = self._read(project, identifier)
        record = MemoryEnvelope(envelope).record
        if not self._visible(record, session_id):
            raise FileNotFoundError(self.OUTSIDE_SCOPE)
        self._check_revision(record, expected_revision, expected_generation)
        selected = MemoryRecord(record)
        if changes is not self.ABSENT:
            selected.require_unchanged_scope(changes)
        selected.require_edit_state(operation == self.RESTORE)
        if operation == self.SAVE:
            record = MemoryRecord.replace(selected.managed_fields(self._managed), self._payload(changes))
            selected = MemoryRecord(record)
        elif changes is not self.ABSENT:
            record.update(self._payload(changes))
        selected.deleted = operation == self.DELETE
        selected.advance_revision()
        selected.updated_at = datetime.now(timezone.utc).isoformat()
        return self._commit(project, identifier, record, MemoryEnvelope(envelope).history, operation, source)

    @staticmethod
    def _check_revision(record, expected_revision, expected_generation):
        return MemoryRecord.check_revision(record, expected_revision, expected_generation, conflict=MemoryConflictError)

    @classmethod
    def _status(cls, status, *, allow_all=SINGLE_STATUS):
        return MemoryRecord.validate_status(status, allow_all=allow_all, statuses=cls.STATUSES, all_status=cls.ALL)

    def _validate_memory(self, identifier, record):
        self._payload(MemoryRecord(record).content_fields(self._managed))
        MemoryRecord.validate_fields(identifier, record, validate_status=self._status,
                                     scopes=self.SCOPES, session_scope=self.SESSION_SCOPE)

    # 공개 API: ComponentData가 아래 트랜잭션 전체를 workspace 잠금으로 보호한다.
    def summary(self, project, session_id):
        return SummaryStorage(project, self).read(session_id)

    def publish_summary(self, project, session_id, value, *, expected_revision, expected_generation):
        current = self.summary(project, session_id)
        observed = SummaryRecord(current)
        if observed.observed_revision != expected_revision or observed.observed_generation != expected_generation:
            raise MemoryConflictError(SummaryRecord.CHANGED)
        result = self.deserialize(self.serialize(value))
        SummaryRecord.stamp(result, session_id=session_id, revision=SummaryRecord.next_revision(expected_revision),
                            generation=observed.generation if current else new_id())
        self._validate_summary(session_id, result)
        SummaryStorage(project, self).write(session_id, result)
        return result

    def clear_summary(self, project, session_id, *, expected_revision, expected_generation):
        current = self.summary(project, session_id)
        if current is self.ABSENT:
            raise FileNotFoundError(SummaryStorage.MISSING_SUMMARY)
        self._check_revision(current, expected_revision, expected_generation)
        SummaryStorage(project, self).clear(session_id)

    def initialize(self, project):
        from .compaction import Compactions
        from .consolidation import MemoryConsolidation
        root = self._checked(self.root(project))
        if self.name in project.initialized_components:
            self.identity(project)
            super().initialize(project)
            SummaryStorage(project, self).inventory()
            Compactions.inventory(project, self)
            MemoryConsolidation(self, project).inventory()
            return
        super().initialize(project)
        atomic_json(root / MemoryIdentity.FILENAME, MemoryIdentity.create(new_id()))
        SummaryStorage(project, self).initialize()
        Compactions.initialize(project, self)
        MemoryConsolidation(self, project).initialize()

    def identity(self, project):
        path = self._checked(self.root(project) / MemoryIdentity.FILENAME)
        return MemoryIdentity.read(read_json(path))

    def history_references(self, project):
        runs, messages, sessions = set(), set(), set()
        if not self._has_storage(project):
            return HistoryReferenceRecord.project(run_ids=[], message_ids=[], session_ids=[])
        def source(value):
            MemorySource.collect_history_references(value, runs, messages)
        for identifier, _ in self._all_records(project):
            envelope = self._read(project, identifier)
            source(MemoryRecord(MemoryEnvelope(envelope).record).optional_source)
            for entry in MemoryEnvelope(envelope).history:
                source(MemoryHistory.optional_source(entry))
        # 요약의 coverage hash는 전체 원본 접두부를 검증한다. 부분 삭제로 이를 깨지 않는다.
        for value in SummaryStorage(project, self).inventory().records.values():
            summary = SummaryRecord(value)
            sessions.add(summary.session_id)
            source(summary.optional_source)
        from .compaction import Compactions
        Compactions.collect_history_references(project, self, messages)
        return HistoryReferenceRecord.project(run_ids=sorted(runs), message_ids=sorted(messages), session_ids=sorted(sessions))

    def references(self, project):
        refs = []
        if not self._has_storage(project):
            return refs
        from .compaction import Compactions
        return Compactions.resource_references(project, self)

    def validate_config(self, data):
        from jsonschema import Draft202012Validator
        error = next(Draft202012Validator(self.describe_config()).iter_errors(data), self.ABSENT)
        if error is not self.ABSENT:
            raise ValueError(MemoryConfiguration.INVALID_CONFIGURATION + error.message)
        original = data
        data = self.parameter_layout.unpack(data)
        MemoryConfiguration(data).validate_search_limits()
        from .processing import resolve_processing_config
        resolve_processing_config(original, token_counter=self.token_counter, completion_adapter=self.completion_adapter)

    def describe_config(self):
        return self.parameter_layout.schema(MemoryConfiguration.schema(
            statuses=self.STATUSES, all_status=self.ALL, keyword=self.KEYWORD,
            processing_schema=lambda: MemoryProcessor.describe_config(completion_adapter=self.completion_adapter)))

    def _validate_transition(self, previous, entry):
        return MemoryHistory.validate_transition(previous, entry, create=self.CREATE,
            delete=self.DELETE, restore=self.RESTORE, identity_fields=self.HISTORY_IDENTITY)

    def validate_record(self, identifier, data):
        return MemoryHistory.validate_envelope(identifier, data, validate_memory=self._validate_memory,
            validate_transition=self._validate_transition, operations=self.OPERATIONS, fields=self.HISTORY_FIELDS)

    def create(self, project, data, *, identifier=ABSENT, source=ABSENT):
        identifier = new_id() if identifier is self.ABSENT else validate_name(identifier)
        if self._record_path(project, identifier).exists():
            raise FileExistsError(self.ALREADY_EXISTS)
        now = datetime.now(timezone.utc).isoformat()
        record = MemoryRecord.create(project_scope=self.PROJECT_SCOPE, confirmed=self.CONFIRMED,
            payload=self._payload(data), identifier=identifier, generation=new_id(),
            at=now, source=self._source(project, source))
        self._commit(project, identifier, record, [], self.CREATE, source)
        return identifier

    def load(self, project, identifier, *, include_deleted=LIVE_ONLY, session_id=ABSENT):
        record = MemoryEnvelope(self._read(project, identifier)).record
        if not self._visible(record, session_id):
            raise FileNotFoundError(self.OUTSIDE_SCOPE)
        if MemoryRecord(record).deleted and not include_deleted:
            raise FileNotFoundError(MemoryRecord.DELETED_RECORD)
        return record

    def list(self, project, *, include_deleted=LIVE_ONLY, status=ABSENT, session_id=ABSENT):
        if status is not self.ABSENT:
            self._status(status, allow_all=self.ALL_STATUSES)
        records = {}
        for identifier, record in self._all_records(project):
            if self._visible(record, session_id) and (include_deleted or not MemoryRecord(record).deleted) and (status in (self.ABSENT, self.ALL) or MemoryRecord(record).status == status):
                records[identifier] = record
        return records

    def save(self, project, identifier, data, *, expected_revision, expected_generation, source=ABSENT, session_id=ABSENT):
        return self._change(project, identifier, expected_revision, expected_generation, self.SAVE, changes=data, source=source, session_id=session_id)

    def update(self, project, identifier, changes, *, expected_revision, expected_generation, source=ABSENT, session_id=ABSENT):
        return self._change(project, identifier, expected_revision, expected_generation, self.UPDATE, changes=changes, source=source, session_id=session_id)

    def delete(self, project, identifier, *, expected_revision, expected_generation, source=ABSENT, session_id=ABSENT):
        return self._change(project, identifier, expected_revision, expected_generation, self.DELETE, source=source, session_id=session_id)

    def restore(self, project, identifier, *, expected_revision, expected_generation, source=ABSENT, session_id=ABSENT):
        return self._change(project, identifier, expected_revision, expected_generation, self.RESTORE, source=source, session_id=session_id)

    def purge(self, project, identifier, *, expected_revision, expected_generation, session_id=ABSENT):
        record = self.load(project, identifier, include_deleted=self.INCLUDE_DELETED, session_id=session_id)
        self._check_revision(record, expected_revision, expected_generation)
        if not MemoryRecord(record).deleted:
            raise ValueError(self.REQUIRE_SOFT_DELETE)
        super().delete(project, identifier)

    def history(self, project, identifier, *, session_id=ABSENT):
        self.load(project, identifier, include_deleted=self.INCLUDE_DELETED, session_id=session_id)
        return MemoryEnvelope(self._read(project, identifier)).history

    def score(self, query, record):
        """명시적으로 선택한 keyword 알고리즘: Unicode 단어 부분 일치 비율."""
        return MemorySearch.score(query, record)

    def search(self, project, query, *, limit=ABSENT, status=ABSENT, session_id=ABSENT):
        MemorySearch.require_query(query)
        config = MemoryConfiguration(self._options(project))
        MemorySearch.require_strategy(config, self.search_fn, keyword=self.KEYWORD)
        limit = MemorySearch.limit(config, limit)
        records = self.list(project, status=config.search_status if status is self.ABSENT else status, session_id=session_id)
        records = {key: record for key, record in records.items() if not self._expired(record)}
        scores = (self.search_fn(query, deepcopy(records)) if self.search_fn is not self.ABSENT else
                  {key: self.score(query, record) for key, record in records.items()})
        return MemorySearch.select(scores, records, config, search_fn=self.search_fn, limit=limit)

    def consolidate(self, project, identifier, *, expected_revision, expected_generation, session_id=ABSENT, source=ABSENT):
        from .consolidation import MemoryConsolidation
        return MemoryConsolidation(self, project).apply(identifier, expected_revision=expected_revision,
            expected_generation=expected_generation, session_id=session_id, source=source)

    def recover_consolidations(self, project):
        from .consolidation import MemoryConsolidation
        return MemoryConsolidation(self, project).recover()

    def pending_consolidations(self, project):
        from .consolidation import MemoryConsolidation
        return [path.stem for path in MemoryConsolidation(self, project).pending()]

    def validate_backup(self, project):
        self.model_usage(project)
        self.identity(project)
        self.get_config(project)
        list(self._all_records(project))
        SummaryStorage(project, self).inventory()
        from .compaction import Compactions
        Compactions.inventory(project, self)

    def clone(self, source, destination):
        SummaryStorage(source, self).inventory()
        from .compaction import Compactions
        Compactions.inventory(source, self)
        for identifier, _ in self._all_records(source):
            # 출처는 원래 Project/Run을 유지하며 복제 과정에서 새 기억으로 다시 작성하지 않는다.
            if self._record_path(destination, identifier).exists():
                raise FileExistsError(self.DESTINATION_EXISTS)
            self._write(destination, identifier, self._read(source, identifier))
        from .consolidation import MemoryConsolidation
        MemoryConsolidation(self, source).clone_receipts(destination)

    def resolve(self, project, capability):
        raise ValueError(self.REQUIRE_DATA_FACTORY)

    def resolve_runtime(self, project, capability, *, data_factory):
        if capability == COMPLETION_PROCESSORS:
            from .processing import resolve_processing_config
            data = data_factory(self.name)
            config = resolve_processing_config(data.get_config(), token_counter=self.token_counter, completion_adapter=self.completion_adapter)
            selected = MemoryProcessingConfig(config)
            if selected.has_tool_result_mode:
                selection = selected.tool_data
                files = data.related(selection.component)
                if not FileData.supports_file_operations(files):
                    raise ValueError(self.REQUIRE_FILE_INTERFACE)
                files.snapshot(selection.identifier)
            return MemoryProcessor(data, completion_adapter=self.completion_adapter,
                                   token_counter=self.token_counter,
                                   priority=selected.priority_or(MemoryProcessor.UNCONFIGURED_PRIORITY))
        if capability == MemoryComponent.name:
            return data_factory(self.name)
        if capability == ToolRegistry.CAPABILITY:
            from .tools import memory_tools
            return memory_tools(data_factory(self.name))
        return self.resolve(project, capability)
