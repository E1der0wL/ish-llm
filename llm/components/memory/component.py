"""기억의 현재 값·출처·수정 이력을 한 JSON 파일로 원자적으로 저장한다."""

from llm.providers.schema import completion_schema

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
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    STATUSES = (CANDIDATE, CONFIRMED)
    ALL = "all"
    CREATE, SAVE, UPDATE, DELETE, RESTORE = "create", "save", "update", "delete", "restore"
    OPERATIONS = (CREATE, SAVE, UPDATE, DELETE, RESTORE)
    HISTORY_FIELDS = frozenset(("revision", "operation", "at", "source", "data"))
    HISTORY_IDENTITY = ("id", "generation", "created_at", "source", "scope", "session_id")
    capabilities = ("memory", "tools", "completion_processors")
    data_class = MemoryData
    parameter_layout = ParameterLayout(
        config=("cache_records", "search_status", "search_limit", "search_strategy", "min_score"),
        policy=("tool_write_status", "max_search_results"),
        paths={**{"processing." + key: "config.processing." + key for key in
                  ("completion", "priority", "extract_scope", "extract_prompt_id", "summary_chars", "recall_limit", "recall_query_chars", "summary_format", "goal_ids",
                   "tool_result_mode", "tool_data", "tool_summary_prompt")},
               **{"processing." + key: "policy.processing." + key for key in
                  ("recall", "summarize", "extract", "compress_tools", "nested_processing", "compact_active",
                   "keep_turns", "summary_after_chars", "summary_after_tokens", "context_chars", "tool_result_chars", "model_input_chars",
                   "max_candidates", "active_keep_iterations", "max_summary_calls", "max_output_chars",
                   "context_tokens", "recall_every", "failure_mode", "timeout_seconds", "provider")}})
    _managed = frozenset({"id", "generation", "revision", "created_at", "updated_at", "deleted", "source"})
    content_schema = object_schema({"content": field("string", minLength=1), "kind": field("string", minLength=1),
        "scope": field("string", enum=["project", "session"]), "session_id": field(["string", "null"]),
        "status": field("string", enum=list(STATUSES)), "tags": field("array", items=field("string", minLength=1)),
        "expires_at": field(["string", "null"]), "metadata": metadata_schema(),
        "replaces": field("array", items=object_schema({"id": field("string", minLength=1),
            "generation": field("string", pattern=r"^[a-f0-9]{32}$"), "revision": field("integer", minimum=1)},
            required=["id", "generation", "revision"]))})

    def __init__(self, *, completion_fn=None, token_counter=None, search_fn=None):
        """보조 모델과 토큰 계수기는 런타임에 주입한다. 설정/레코드에는 저장하지 않는다."""
        if any(value is not None and not callable(value) for value in (completion_fn, token_counter, search_fn)):
            raise TypeError("Memory model and token counter must be callable")
        self.completion_fn, self.token_counter = completion_fn, token_counter
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
        limit = self._options(project).get("cache_records", 0)
        _, records = self._record_inventory(project)
        for identifier, raw in records.items():
            path = self._record_path(project, identifier)
            stat = path.stat()
            signature = (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
            cached = self._record_cache.pop(str(path), None)
            record = cached[1] if cached and cached[0] == signature else self._decode_record(identifier, raw)["record"]
            if limit:
                self._record_cache[str(path)] = (signature, deepcopy(record))
            while len(self._record_cache) > limit:
                self._record_cache.popitem(last=False)
            yield path.stem, deepcopy(record)

    def _summary_path(self, project, session_id):
        return SummaryStorage(project, self).path(session_id)

    def _validate_summary(self, session_id, value):
        if (not isinstance(value, dict) or not isinstance(value.get("generation"), str)
                or re.fullmatch(r"[a-f0-9]{32}", value["generation"]) is None):
            raise ValueError("Invalid Session summary generation")
        if value.get("session_id") != session_id or type(value.get("revision")) is not int or value["revision"] < 1:
            raise ValueError("Invalid Session summary identity")
        if not isinstance(value.get("content"), str) or not value["content"].strip():
            raise ValueError("Invalid Session summary content")
        metadata = value.get("metadata")
        if not isinstance(metadata, dict):
            raise ValueError("Invalid Session summary metadata")
        if "work_state" in metadata:
            from .work_state import SUMMARY_FORMAT_VERSION, validate_work_state
            if metadata.get("summary_format_version") != SUMMARY_FORMAT_VERSION:
                raise ValueError("Invalid work state format version")
            validate_work_state(metadata["work_state"])
        if type(metadata.get("coverage_count")) is not int or metadata["coverage_count"] < 0:
            raise ValueError("Invalid Session summary coverage")
        partial = metadata.get("partial")
        if metadata["coverage_count"] == 0 and not partial:
            raise ValueError("Empty summary requires a partial cursor")
        if partial is not None and (not isinstance(partial, dict) or type(partial.get("offset")) is not int
                or partial["offset"] <= 0 or not isinstance(partial.get("summary"), str)
                or not isinstance(partial.get("digest"), str)):
            raise ValueError("Invalid partial summary cursor")
        for key in ("coverage_hash", "profile", "through_message_id", "first_message_id"):
            if not isinstance(metadata.get(key), str) or not metadata[key]:
                raise ValueError("Invalid Session summary source")
        if not isinstance(value.get("source"), dict):
            raise ValueError("Invalid Session summary provenance")

    @staticmethod
    def _visible(record, session_id):
        return record["scope"] == "project" or record.get("session_id") == session_id

    @staticmethod
    def _expired(record):
        return bool(record.get("expires_at") and
                    datetime.fromisoformat(record["expires_at"]) <= datetime.now(timezone.utc))

    def _payload(self, data):
        value = self.deserialize(self.serialize(data))
        if self._managed.intersection(value):
            raise ValueError("Memory lifecycle fields cannot be supplied as content")
        error = next(Draft202012Validator(self.content_schema).iter_errors(value), None)
        if error:
            raise ValueError(f"Memory content: {error.message}") from error
        return value

    def _read(self, project, identifier):
        self._assert_ready(project)
        return super().load(project, identifier)

    def _source(self, project, source):
        value = self.deserialize(self.serialize(source or {"kind": "api"}))
        value.setdefault("project_id", project.id)
        return value

    def _commit(self, project, identifier, record, history, operation, source):
        self._assert_ready(project)
        entry = {"revision": record["revision"], "operation": operation,
                 "at": record["updated_at"], "source": self._source(project, source),
                 "data": deepcopy(record)}
        self._write(project, identifier, {"record": record, "history": [*history, entry]})
        return deepcopy(record)

    def _change(self, project, identifier, expected_revision, expected_generation, operation, *, changes=None, source=None, session_id=None):
        envelope = self._read(project, identifier)
        record = envelope["record"]
        if not self._visible(record, session_id):
            raise FileNotFoundError("Memory is outside the requested scope")
        self._check_revision(record, expected_revision, expected_generation)
        if changes is not None and any(key in changes and changes[key] != record.get(key) for key in ("scope", "session_id")):
            raise ValueError("Memory scope cannot be changed")
        if record["deleted"] != (operation == self.RESTORE):
            raise ValueError("Memory is deleted" if record["deleted"] else "Memory is not deleted")
        if operation == self.SAVE:
            record = {**{key: record[key] for key in self._managed}, **self._payload(changes)}
        elif changes is not None:
            record.update(self._payload(changes))
        record["deleted"] = operation == self.DELETE
        record["revision"] += 1
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        return self._commit(project, identifier, record, envelope["history"], operation, source)

    @staticmethod
    def _check_revision(record, expected_revision, expected_generation):
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("expected_revision must be a positive integer")
        if not isinstance(expected_generation, str) or not re.fullmatch(r"[a-f0-9]{32}", expected_generation):
            raise ValueError("expected_generation must be the generation read from the record")
        if (record["generation"], record["revision"]) != (expected_generation, expected_revision):
            raise MemoryConflictError("Memory changed; reload before editing")

    @classmethod
    def _status(cls, status, *, allow_all=False):
        if status not in ((*cls.STATUSES, cls.ALL) if allow_all else cls.STATUSES):
            raise ValueError("Invalid memory status")

    def _validate_memory(self, identifier, record):
        self._payload({key: value for key, value in record.items() if key not in self._managed})
        if not isinstance(record.get("generation"), str) or not re.fullmatch(r"[a-f0-9]{32}", record["generation"]):
            raise ValueError("Memory requires a stored generation; re-register records without one")
        if record.get("id") != identifier or type(record.get("revision")) is not int or record["revision"] < 1:
            raise ValueError("Invalid memory identity or revision")
        for key in ("content", "kind", "created_at", "updated_at"):
            if not isinstance(record.get(key), str) or not record[key].strip():
                raise ValueError(f"Memory {key} must be nonempty text")
        for key in ("created_at", "updated_at"):
            if datetime.fromisoformat(record[key]).utcoffset() is None:
                raise ValueError(f"Memory {key} requires a timezone")
        self._status(record.get("status"))
        if record.get("scope") not in ("project", "session") or type(record.get("deleted")) is not bool:
            raise ValueError("Memory requires project/session scope and a deletion marker")
        if record["scope"] == "session":
            validate_name(record.get("session_id"))
        elif record.get("session_id") is not None:
            raise ValueError("Project memories cannot have a session_id")
        if record.get("expires_at") is not None:
            if not isinstance(record["expires_at"], str):
                raise ValueError("Memory expiry must be ISO datetime text")
            expires = datetime.fromisoformat(record["expires_at"])
            if expires.tzinfo is None:
                raise ValueError("Memory expiry requires a timezone")
        if "metadata" in record and not isinstance(record["metadata"], dict):
            raise ValueError("Memory metadata must be an object")
        if not isinstance(record.get("source"), dict) or not isinstance(record.get("tags"), list) or any(
                not isinstance(tag, str) or not tag.strip() for tag in record["tags"]):
            raise ValueError("Memory requires source object and text tags")

    # 공개 API: ComponentData가 아래 트랜잭션 전체를 workspace 잠금으로 보호한다.
    def summary(self, project, session_id):
        return SummaryStorage(project, self).read(session_id)

    def publish_summary(self, project, session_id, value, *, expected_revision, expected_generation):
        current = self.summary(project, session_id)
        if ((current["revision"] if current else 0) != expected_revision or
                (current["generation"] if current else None) != expected_generation):
            raise MemoryConflictError("Session summary changed during processing")
        result = self.deserialize(self.serialize(value))
        result.update(session_id=session_id, revision=expected_revision + 1,
                      generation=current["generation"] if current else new_id())
        self._validate_summary(session_id, result)
        SummaryStorage(project, self).write(session_id, result)
        return result

    def clear_summary(self, project, session_id, *, expected_revision, expected_generation):
        current = self.summary(project, session_id)
        if current is None:
            raise FileNotFoundError("Session summary does not exist")
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
            return {"run_ids": [], "message_ids": [], "session_ids": []}
        def source(value):
            if isinstance(value, dict):
                if value.get("run_id"):
                    runs.add(value["run_id"])
                messages.update(value.get("message_ids", []))
                if value.get("input_message_id"):
                    messages.add(value["input_message_id"])
        for identifier, _ in self._all_records(project):
            envelope = self._read(project, identifier)
            source(envelope["record"].get("source"))
            for entry in envelope["history"]:
                source(entry.get("source"))
        # 요약의 coverage hash는 전체 원본 접두부를 검증한다. 부분 삭제로 이를 깨지 않는다.
        for value in SummaryStorage(project, self).inventory()[1].values():
            sessions.add(value["session_id"])
            source(value.get("source"))
        from .compaction import Compactions
        for key, state in Compactions.inventory(project, self)[1].items():
            if not key.startswith(Compactions.CONTEXTS + "/"):
                continue
            messages.update(item["id"] for item in state.get("applied", {}).get("sources", []))
            if state.get("request", {}).get("status") in ("pending", "running", "partial", "failed", "interrupted"):
                messages.update(item["id"] for item in state["request"]["sources"])
        return {"run_ids": sorted(runs), "message_ids": sorted(messages), "session_ids": sorted(sessions)}

    def references(self, project):
        refs = []
        if not self._has_storage(project):
            return refs
        from .compaction import Compactions
        for key, state in Compactions.inventory(project, self)[1].items():
            if key.startswith(Compactions.TOOLS + "/"):
                request = state["request"]
                refs.append({**request["reference"], "source": request["id"]})
        return refs

    def validate_config(self, data):
        from jsonschema import Draft202012Validator
        error = next(Draft202012Validator(self.describe_config()).iter_errors(data), None)
        if error is not None:
            raise ValueError("Invalid memory configuration: " + error.message)
        original = data
        data = self.parameter_layout.unpack(data)
        if data.get("search_limit") is not None and data.get("max_search_results") is not None and data["search_limit"] > data["max_search_results"]:
            raise ValueError("Memory search_limit exceeds max_search_results")
        from .processing import resolve_processing_config
        resolve_processing_config(original, token_counter=self.token_counter)

    def describe_config(self):
        from llm.core.schema import object_schema, field
        from llm.providers.requests import provider_schema
        properties = {name: field("boolean") for name in ("recall", "summarize", "extract", "compress_tools", "nested_processing", "compact_active")}
        properties.update({name: field("integer", minimum=1) for name in (
            "keep_turns", "summary_after_chars", "summary_after_tokens", "summary_chars", "context_chars", "recall_limit",
            "tool_result_chars", "model_input_chars", "max_candidates", "active_keep_iterations",
            "max_summary_calls", "recall_query_chars", "max_output_chars")})
        properties.update(priority=field("integer"), recall_every=field("integer", minimum=0),
            tool_result_mode=field("string", enum=["reference", "summary"]),
            tool_data=object_schema({"component": field("string", minLength=1), "identifier": field("string", minLength=1)},
                                    required=["component", "identifier"]),
            tool_summary_prompt=field("string", minLength=1),
            summary_format=field("string", enum=["text", "work_state"]),
            goal_ids={"type": "array", "uniqueItems": True, "items": field("string", pattern=r"^[a-zA-Z0-9_-]{1,64}$")},
            context_tokens=field(["integer", "null"], minimum=1), completion=completion_schema(),
            provider={**provider_schema(), "type": ["object", "null"]},
            extract_scope=field("string", enum=["session", "project"]),
            extract_prompt_id=field("string", pattern=r"^[a-zA-Z0-9_-]{1,64}$"),
            failure_mode=field("string", enum=["raise", "continue"]),
            timeout_seconds=field(["number", "null"], exclusiveMinimum=0))
        return self.parameter_layout.schema(object_schema({
            "cache_records": field("integer", minimum=0),
            "tool_write_status": field("string", enum=list(self.STATUSES)),
            "search_status": field("string", enum=[*self.STATUSES, self.ALL]),
            "search_strategy": field("string", enum=["keyword"]),
            "min_score": field("number"),
            "search_limit": field("integer", minimum=1), "max_search_results": field("integer", minimum=1),
            "processing": object_schema(properties)}))

    def _validate_transition(self, previous, entry):
        record, operation = entry["data"], entry["operation"]
        if previous is None:
            if (operation != self.CREATE or record["deleted"]
                    or record["created_at"] != record["updated_at"] or entry["source"] != record["source"]):
                raise ValueError("Memory history requires its original creation")
            return
        if any(record.get(key) != previous.get(key) for key in self.HISTORY_IDENTITY):
            raise ValueError("Memory history changes immutable identity or scope")
        if (operation == self.CREATE or previous["deleted"] != (operation == self.RESTORE)
                or record["deleted"] != (operation == self.DELETE)):
            raise ValueError("Invalid memory history state transition")
        if operation in (self.DELETE, self.RESTORE):
            expected = {**previous, "revision": record["revision"], "updated_at": record["updated_at"],
                        "deleted": record["deleted"]}
            if operation == self.DELETE and record.get("metadata") != previous.get("metadata"):
                from .consolidation import MemoryConsolidation
                expected["metadata"] = MemoryConsolidation.deletion_metadata(previous, record, entry["source"])
            if record != expected:
                raise ValueError("Memory deletion or restoration changed stored content")

    def validate_record(self, identifier, data):
        if data.keys() - {"record", "history"}:
            raise ValueError("Memory envelope requires only record/history")
        record, history = data.get("record"), data.get("history")
        if not isinstance(record, dict) or not isinstance(history, list) or not history:
            raise ValueError("Memory requires a record and revision history")
        self._validate_memory(identifier, record)
        if len(history) != record["revision"]:
            raise ValueError("Incomplete memory revision history")
        previous = None
        for revision, entry in enumerate(history, 1):
            if not isinstance(entry, dict) or not isinstance(entry.get("data"), dict):
                raise ValueError("Invalid memory history entry")
            if entry.keys() != self.HISTORY_FIELDS:
                raise ValueError("Invalid Memory history fields")
            self._validate_memory(identifier, entry["data"])
            if entry["data"]["generation"] != record["generation"]:
                raise ValueError("Memory history crosses record generations")
            if type(entry["revision"]) is not int or entry["revision"] != revision or entry["data"]["revision"] != revision or (
                    entry["operation"] not in self.OPERATIONS
                    or not isinstance(entry["source"], dict) or entry["at"] != entry["data"]["updated_at"]):
                raise ValueError("Invalid memory revision history")
            self._validate_transition(previous, entry)
            previous = entry["data"]
        if history[-1]["data"] != record:
            raise ValueError("Memory and history disagree")

    def create(self, project, data, *, identifier=None, source=None):
        identifier = new_id() if identifier is None else validate_name(identifier)
        if self._record_path(project, identifier).exists():
            raise FileExistsError("Memory already exists")
        now = datetime.now(timezone.utc).isoformat()
        record = {"kind": "note", "scope": "project", "status": self.CONFIRMED, "tags": [],
                  **self._payload(data), "id": identifier, "generation": new_id(), "revision": 1,
                  "created_at": now, "updated_at": now, "deleted": False,
                  "source": self._source(project, source)}
        self._commit(project, identifier, record, [], self.CREATE, source)
        return identifier

    def load(self, project, identifier, *, include_deleted=False, session_id=None):
        record = self._read(project, identifier)["record"]
        if not self._visible(record, session_id):
            raise FileNotFoundError("Memory is outside the requested scope")
        if record["deleted"] and not include_deleted:
            raise FileNotFoundError("Memory is deleted")
        return record

    def list(self, project, *, include_deleted=False, status=None, session_id=None):
        if status is not None:
            self._status(status, allow_all=True)
        records = {}
        for identifier, record in self._all_records(project):
            if self._visible(record, session_id) and (include_deleted or not record["deleted"]) and (status in (None, self.ALL) or record["status"] == status):
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
        if not record["deleted"]:
            raise ValueError("Soft-delete memory before permanent removal")
        super().delete(project, identifier)

    def history(self, project, identifier, *, session_id=None):
        self.load(project, identifier, include_deleted=True, session_id=session_id)
        return self._read(project, identifier)["history"]

    def score(self, query, record):
        """명시적으로 선택한 keyword 알고리즘: Unicode 단어 부분 일치 비율."""
        terms = set(re.findall(r"\w+", query.casefold()))
        text = " ".join([record["content"], record["kind"], *record["tags"]]).casefold()
        return sum(term in text for term in terms) / len(terms) if terms else 0.0

    def search(self, project, query, *, limit=None, status=None, session_id=None):
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Memory query must be nonempty text")
        config = self._options(project)
        if self.search_fn is None and config.get("search_strategy") != "keyword":
            raise ValueError("Memory search requires config.search_strategy or a host search_fn")
        limit = config.get("search_limit") if limit is None else limit
        if limit is not None and (type(limit) is not int or limit < 1 or config.get("max_search_results") is not None and limit > config["max_search_results"]):
            raise ValueError("Memory search limit is outside configured bounds")
        if limit is None:
            limit = config.get("max_search_results")
        records = self.list(project, status=config.get("search_status") if status is None else status, session_id=session_id)
        records = {key: record for key, record in records.items() if not self._expired(record)}
        scores = (self.search_fn(query, deepcopy(records)) if self.search_fn is not None else
                  {key: self.score(query, record) for key, record in records.items()})
        if not isinstance(scores, dict) or scores.keys() - records.keys() or any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in scores.values()):
            raise ValueError("Memory search adapter must return finite scores for visible memory IDs")
        # keyword의 매칭 조건은 양수 일치율이다. 주입 검색기는 반환한 ID 집합을
        # 직접 소유하므로 0점도 보존한다. 추가 cutoff는 명시된 경우에만 적용한다.
        hits = [{"score": score, "memory": records[key]} for key, score in scores.items()
                if (self.search_fn is not None or score > 0)
                and ("min_score" not in config or score >= config["min_score"])]
        return sorted(hits, key=lambda hit: (-hit["score"], hit["memory"]["id"]))[:limit]

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
        if capability == "completion_processors":
            from .processing import MemoryProcessor, resolve_processing_config
            data = data_factory(self.name)
            config = resolve_processing_config(data.get_config(), token_counter=self.token_counter)
            if "tool_result_mode" in config:
                selection = config["tool_data"]
                files = data.related(selection["component"])
                if not all(callable(getattr(files, name, None)) for name in ("read_file", "write_file", "snapshot")):
                    raise ValueError("tool_data requires a file Component public interface")
                files.snapshot(selection["identifier"])
            return MemoryProcessor(data, completion_fn=self.completion_fn,
                                   token_counter=self.token_counter,
                                   priority=config.get("priority", MemoryProcessor.UNCONFIGURED_PRIORITY))
        if capability == "memory":
            return data_factory(self.name)
        if capability == "tools":
            from .tools import memory_tools
            return memory_tools(data_factory(self.name))
        return self.resolve(project, capability)
