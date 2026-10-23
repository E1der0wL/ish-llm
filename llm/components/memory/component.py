"""기억의 현재 값·출처·수정 이력을 한 JSON 파일로 원자적으로 저장한다."""

from llm.core.history_references import HistoryReferenceRecord

from llm.components.processing import COMPLETION_PROCESSORS
from llm.components.tools.registry import ToolRegistry

from llm.core.resource_references import StoredResourceReference

from copy import deepcopy
from datetime import datetime, timezone
import re
import math
from collections import OrderedDict

from llm.components.base import Component, validate_name
from llm.core.models import new_id
from llm.core.parameters import ParameterLayout
from llm.core.schema import object_schema, metadata_schema, field
from jsonschema import Draft202012Validator
from llm.errors import CodedError
from .data import MemoryData
from .summary_storage import SummaryStorage
from .summary_records import SummaryRecord
from .records import MemoryRecord, MemorySearchHit
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

    name = "memory"
    directory = "memory"
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
    capabilities = ("memory", ToolRegistry.CAPABILITY, COMPLETION_PROCESSORS)
    data_class = MemoryData
    parameter_layout = MemoryConfiguration.layout()
    _managed = MemoryRecord.MANAGED
    content_schema = MemoryRecord.content_schema(scopes=SCOPES, statuses=STATUSES)

    def __init__(self, *, completion_adapter=None, token_counter=None, search_fn=None):
        """보조 모델과 토큰 계수기는 런타임에 주입한다. 설정/레코드에는 저장하지 않는다."""
        if any(value is not None and not callable(value) for value in (token_counter, search_fn)):
            raise TypeError("Memory model and token counter must be callable")
        from llm.providers.litellm import LiteLLMCompletionAdapter
        self.completion_adapter = LiteLLMCompletionAdapter() if completion_adapter is None else completion_adapter
        if not callable(getattr(self.completion_adapter, "stream", None)):
            raise TypeError("Memory completion_adapter requires stream")
        self.token_counter = token_counter
        self.search_fn = search_fn
        self._record_cache = OrderedDict()

    def _assert_ready(self, project):
        from .consolidation import MemoryConsolidation
        if MemoryConsolidation(self, project).pending():
            raise MemoryConflictError("Memory consolidation is pending; recover before reading or editing")

    def _all_records(self, project):
        self._assert_ready(project)
        if not self._has_storage(project):
            return
        limit = MemoryConfiguration(self._options(project)).cache_records
        _, records = self._record_inventory(project)
        for identifier, raw in records.items():
            path = self._record_path(project, identifier)
            stat = path.stat()
            signature = (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
            cached = self._record_cache.pop(str(path), None)
            record = cached[1] if cached and cached[0] == signature else MemoryEnvelope(self._decode_record(identifier, raw)).record
            if limit:
                self._record_cache[str(path)] = (signature, deepcopy(record))
            while len(self._record_cache) > limit:
                self._record_cache.popitem(last=False)
            yield path.stem, deepcopy(record)

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

    def _change(self, project, identifier, expected_revision, expected_generation, operation, *, changes=None, source=None, session_id=None):
        envelope = self._read(project, identifier)
        record = MemoryEnvelope(envelope).record
        if not self._visible(record, session_id):
            raise FileNotFoundError("Memory is outside the requested scope")
        self._check_revision(record, expected_revision, expected_generation)
        selected = MemoryRecord(record)
        if changes is not None:
            selected.require_unchanged_scope(changes)
        selected.require_edit_state(operation == self.RESTORE)
        if operation == self.SAVE:
            record = MemoryRecord.replace(selected.managed_fields(self._managed), self._payload(changes))
            selected = MemoryRecord(record)
        elif changes is not None:
            record.update(self._payload(changes))
        selected.deleted = operation == self.DELETE
        selected.advance_revision()
        selected.updated_at = datetime.now(timezone.utc).isoformat()
        return self._commit(project, identifier, record, MemoryEnvelope(envelope).history, operation, source)

    @staticmethod
    def _check_revision(record, expected_revision, expected_generation):
        return MemoryRecord.check_revision(record, expected_revision, expected_generation, conflict=MemoryConflictError)

    @classmethod
    def _status(cls, status, *, allow_all=False):
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
        if current is None:
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
        atomic_json(root / "identity.json", {"id": new_id()})
        SummaryStorage(project, self).initialize()
        Compactions.initialize(project, self)
        MemoryConsolidation(self, project).initialize()

    def identity(self, project):
        path = self._checked(self.root(project) / "identity.json")
        value = read_json(path)
        if (not isinstance(value, dict) or set(value) != {"id"}
                or not isinstance(value["id"], str) or not re.fullmatch(r"[a-f0-9]{32}", value["id"])):
            raise ValueError("Invalid Memory identity")
        return value["id"]

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
        for key, state in Compactions.inventory(project, self)[1].items():
            if not key.startswith(Compactions.CONTEXTS + "/"):
                continue
            messages.update(item["id"] for item in state.get("applied", {}).get("sources", []))
            if state.get("request", {}).get("status") in Compactions.UNFINISHED_STATES:
                messages.update(item["id"] for item in state["request"]["sources"])
        return HistoryReferenceRecord.project(run_ids=sorted(runs), message_ids=sorted(messages), session_ids=sorted(sessions))

    def references(self, project):
        refs = []
        if not self._has_storage(project):
            return refs
        from .compaction import Compactions
        for key, state in Compactions.inventory(project, self)[1].items():
            if key.startswith(Compactions.TOOLS + "/"):
                request = state["request"]
                refs.append(StoredResourceReference.from_value(request["reference"], request["id"]).to_dict())
        return refs

    def validate_config(self, data):
        from jsonschema import Draft202012Validator
        error = next(Draft202012Validator(self.describe_config()).iter_errors(data), None)
        if error is not None:
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

    def create(self, project, data, *, identifier=None, source=None):
        identifier = new_id() if identifier is None else validate_name(identifier)
        if self._record_path(project, identifier).exists():
            raise FileExistsError("Memory already exists")
        now = datetime.now(timezone.utc).isoformat()
        record = MemoryRecord.create(project_scope=self.PROJECT_SCOPE, confirmed=self.CONFIRMED,
            payload=self._payload(data), identifier=identifier, generation=new_id(),
            at=now, source=self._source(project, source))
        self._commit(project, identifier, record, [], self.CREATE, source)
        return identifier

    def load(self, project, identifier, *, include_deleted=False, session_id=None):
        record = MemoryEnvelope(self._read(project, identifier)).record
        if not self._visible(record, session_id):
            raise FileNotFoundError("Memory is outside the requested scope")
        if MemoryRecord(record).deleted and not include_deleted:
            raise FileNotFoundError("Memory is deleted")
        return record

    def list(self, project, *, include_deleted=False, status=None, session_id=None):
        if status is not None:
            self._status(status, allow_all=True)
        records = {}
        for identifier, record in self._all_records(project):
            if self._visible(record, session_id) and (include_deleted or not MemoryRecord(record).deleted) and (status in (None, self.ALL) or MemoryRecord(record).status == status):
                records[identifier] = record
        return records

    def save(self, project, identifier, data, *, expected_revision, expected_generation, source=None, session_id=None):
        return self._change(project, identifier, expected_revision, expected_generation, self.SAVE, changes=data, source=source, session_id=session_id)

    def update(self, project, identifier, changes, *, expected_revision, expected_generation, source=None, session_id=None):
        return self._change(project, identifier, expected_revision, expected_generation, self.UPDATE, changes=changes, source=source, session_id=session_id)

    def delete(self, project, identifier, *, expected_revision, expected_generation, source=None, session_id=None):
        return self._change(project, identifier, expected_revision, expected_generation, self.DELETE, source=source, session_id=session_id)

    def restore(self, project, identifier, *, expected_revision, expected_generation, source=None, session_id=None):
        return self._change(project, identifier, expected_revision, expected_generation, self.RESTORE, source=source, session_id=session_id)

    def purge(self, project, identifier, *, expected_revision, expected_generation, session_id=None):
        record = self.load(project, identifier, include_deleted=True, session_id=session_id)
        self._check_revision(record, expected_revision, expected_generation)
        if not MemoryRecord(record).deleted:
            raise ValueError("Soft-delete memory before permanent removal")
        super().delete(project, identifier)

    def history(self, project, identifier, *, session_id=None):
        self.load(project, identifier, include_deleted=True, session_id=session_id)
        return MemoryEnvelope(self._read(project, identifier)).history

    def score(self, query, record):
        """명시적으로 선택한 keyword 알고리즘: Unicode 단어 부분 일치 비율."""
        terms = set(re.findall(r"\w+", query.casefold()))
        text = " ".join([MemoryRecord(record).content, MemoryRecord(record).kind, *MemoryRecord(record).tags]).casefold()
        return sum(term in text for term in terms) / len(terms) if terms else 0.0

    def search(self, project, query, *, limit=None, status=None, session_id=None):
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Memory query must be nonempty text")
        config = MemoryConfiguration(self._options(project))
        if self.search_fn is None and config.search_strategy != self.KEYWORD:
            raise ValueError("Memory search requires config.search_strategy or a host search_fn")
        limit = config.search_limit if limit is None else limit
        if limit is not None and (type(limit) is not int or limit < 1 or config.maximum_results is not None and limit > config.required_maximum_results):
            raise ValueError("Memory search limit is outside configured bounds")
        if limit is None:
            limit = config.maximum_results
        records = self.list(project, status=config.search_status if status is None else status, session_id=session_id)
        records = {key: record for key, record in records.items() if not self._expired(record)}
        scores = (self.search_fn(query, deepcopy(records)) if self.search_fn is not None else
                  {key: self.score(query, record) for key, record in records.items()})
        if not isinstance(scores, dict) or scores.keys() - records.keys() or any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in scores.values()):
            raise ValueError("Memory search adapter must return finite scores for visible memory IDs")
        # keyword의 매칭 조건은 양수 일치율이다. 주입 검색기는 반환한 ID 집합을
        # 직접 소유하므로 0점도 보존한다. 추가 cutoff는 명시된 경우에만 적용한다.
        hits = [MemorySearchHit.create(score, records[key]) for key, score in scores.items()
                if (self.search_fn is not None or score > 0)
                and (not config.has_min_score or score >= config.min_score)]
        return sorted(hits, key=lambda hit: MemorySearchHit(hit).rank)[:limit]

    def consolidate(self, project, identifier, *, expected_revision, expected_generation, session_id=None, source=None):
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
                raise FileExistsError("Destination memory already exists")
            self._write(destination, identifier, self._read(source, identifier))
        from .consolidation import MemoryConsolidation
        MemoryConsolidation(self, source).clone_receipts(destination)

    def resolve(self, project, capability):
        raise ValueError("Memory capabilities require a lifecycle-bound component data factory")

    def resolve_runtime(self, project, capability, *, data_factory):
        if capability == COMPLETION_PROCESSORS:
            from .processing import resolve_processing_config
            data = data_factory(self.name)
            config = resolve_processing_config(data.get_config(), token_counter=self.token_counter, completion_adapter=self.completion_adapter)
            selected = MemoryProcessingConfig(config)
            if selected.has_tool_result_mode:
                selection = selected.tool_data
                files = data.related(selection.component)
                if not all(callable(getattr(files, name, None)) for name in ("read_file", "write_file", "snapshot")):
                    raise ValueError("tool_data requires a file Component public interface")
                files.snapshot(selection.identifier)
            return MemoryProcessor(data, completion_adapter=self.completion_adapter,
                                   token_counter=self.token_counter,
                                   priority=selected.priority_or(MemoryProcessor.UNCONFIGURED_PRIORITY))
        if capability == "memory":
            return data_factory(self.name)
        if capability == ToolRegistry.CAPABILITY:
            from .tools import memory_tools
            return memory_tools(data_factory(self.name))
        return self.resolve(project, capability)
