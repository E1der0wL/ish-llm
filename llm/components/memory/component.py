"""기억의 현재 값·출처·수정 이력을 한 JSON 파일로 원자적으로 저장한다."""

from copy import deepcopy
from datetime import datetime, timezone
import re
import math
from collections import OrderedDict

from llm.components.base import Component, validate_name
from llm.core.models import new_id
from .data import MemoryData
from llm.services.infrastructure.storage import atomic_json, read_json


class MemoryConflictError(RuntimeError):
    """읽은 뒤 기억이 변경되었다. 최신 revision을 확인하고 명시적으로 다시 수정한다."""

    code = "memory_conflict"


class MemoryComponent(Component):
    """프로젝트 단위 장기 기억. 모델이나 실행 엔진 없이도 CRUD를 사용할 수 있다.

    직접 호출에는 workspace 소유권이 필요하다. 일반 사용자는 잠금·수명 검사가 있는
    project.components.memory를 사용한다. 검색을 바꾸려면 search/score를 재정의한다.
    """

    name = "memory"
    directory = "memory"
    capabilities = ("memory", "tools", "completion_processors")
    data_class = MemoryData
    _managed = frozenset({"id", "revision", "created_at", "updated_at", "deleted", "source"})

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
        limit = self.configuration(project).get("cache_records", 256)
        for path in sorted(self._checked(self.root(project) / "records").glob("*.json")):
            path = self._checked(path)
            stat = path.stat()
            signature = (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
            cached = self._record_cache.pop(str(path), None)
            record = cached[1] if cached and cached[0] == signature else self._read(project, path.stem)["record"]
            if limit:
                self._record_cache[str(path)] = (signature, deepcopy(record))
            while len(self._record_cache) > limit:
                self._record_cache.popitem(last=False)
            yield path.stem, deepcopy(record)

    def _summary_path(self, project, task_id):
        return self._checked(self.root(project) / "contexts" / (validate_name(task_id) + ".json"))

    def _validate_summary(self, task_id, value):
        validate_name(value.get("generation"))
        if value.get("task_id") != task_id or type(value.get("revision")) is not int or value["revision"] < 1:
            raise ValueError("Invalid Task summary identity")
        if not isinstance(value.get("content"), str) or not value["content"].strip():
            raise ValueError("Invalid Task summary content")
        metadata = value.get("metadata", {})
        if not isinstance(metadata, dict) or type(metadata.get("coverage_count")) is not int or metadata["coverage_count"] < 0:
            raise ValueError("Invalid Task summary coverage")
        partial = metadata.get("partial")
        if metadata["coverage_count"] == 0 and not partial:
            raise ValueError("Empty summary requires a partial cursor")
        if partial is not None and (not isinstance(partial, dict) or type(partial.get("offset")) is not int
                or partial["offset"] <= 0 or not isinstance(partial.get("summary"), str)
                or not isinstance(partial.get("digest"), str)):
            raise ValueError("Invalid partial summary cursor")
        for key in ("coverage_hash", "profile", "through_message_id", "first_message_id"):
            if not isinstance(metadata.get(key), str) or not metadata[key]:
                raise ValueError("Invalid Task summary source")
        if not isinstance(value.get("source"), dict):
            raise ValueError("Invalid Task summary provenance")

    @staticmethod
    def _visible(record, task_id):
        return record["scope"] == "project" or record.get("task_id") == task_id

    @staticmethod
    def _expired(record):
        return bool(record.get("expires_at") and
                    datetime.fromisoformat(record["expires_at"]) <= datetime.now(timezone.utc))

    def _payload(self, data):
        value = self.deserialize(self.serialize(data))
        if self._managed.intersection(value):
            raise ValueError("Memory lifecycle fields cannot be supplied as content")
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

    def _change(self, project, identifier, expected_revision, operation, *, changes=None, source=None, task_id=None):
        envelope = self._read(project, identifier)
        record = envelope["record"]
        if not self._visible(record, task_id):
            raise FileNotFoundError("Memory is outside the requested scope")
        self._check_revision(record, expected_revision)
        if changes is not None and any(key in changes and changes[key] != record.get(key) for key in ("scope", "task_id")):
            raise ValueError("Memory scope cannot be changed")
        if record["deleted"] != (operation == "restore"):
            raise ValueError("Memory is deleted" if record["deleted"] else "Memory is not deleted")
        if operation == "save":
            record = {**{key: record[key] for key in self._managed}, **self._payload(changes)}
        elif changes is not None:
            record.update(self._payload(changes))
        record["deleted"] = operation == "delete"
        record["revision"] += 1
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        return self._commit(project, identifier, record, envelope["history"], operation, source)

    @staticmethod
    def _check_revision(record, expected_revision):
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("expected_revision must be a positive integer")
        if record["revision"] != expected_revision:
            raise MemoryConflictError("Memory changed; reload before editing")

    @staticmethod
    def _status(status, *, allow_all=False):
        if status not in (("candidate", "confirmed", "all") if allow_all else ("candidate", "confirmed")):
            raise ValueError("Invalid memory status")

    def _validate_memory(self, identifier, record):
        if record.get("id") != identifier or type(record.get("revision")) is not int or record["revision"] < 1:
            raise ValueError("Invalid memory identity or revision")
        for key in ("content", "kind", "created_at", "updated_at"):
            if not isinstance(record.get(key), str) or not record[key].strip():
                raise ValueError(f"Memory {key} must be nonempty text")
        self._status(record.get("status"))
        if record.get("scope") not in ("project", "task") or type(record.get("deleted")) is not bool:
            raise ValueError("Memory requires project/task scope and a deletion marker")
        if record["scope"] == "task":
            validate_name(record.get("task_id"))
        elif record.get("task_id") is not None:
            raise ValueError("Project memories cannot have a task_id")
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
    def summary(self, project, task_id):
        path = self._summary_path(project, task_id)
        if not path.exists():
            return None
        value = self.deserialize(path.read_text(encoding="utf-8"))
        self._validate_summary(task_id, value)
        return value

    def publish_summary(self, project, task_id, value, *, expected_revision, expected_generation):
        current = self.summary(project, task_id)
        if ((current["revision"] if current else 0) != expected_revision or
                (current["generation"] if current else None) != expected_generation):
            raise MemoryConflictError("Task summary changed during processing")
        result = self.deserialize(self.serialize(value))
        result.update(task_id=task_id, revision=expected_revision + 1,
                      generation=current["generation"] if current else new_id())
        self._validate_summary(task_id, result)
        atomic_json(self._summary_path(project, task_id), result)
        return result

    def clear_summary(self, project, task_id, *, expected_revision):
        current = self.summary(project, task_id)
        if current is None:
            raise FileNotFoundError("Task summary does not exist")
        self._check_revision(current, expected_revision)
        from llm.services.infrastructure.storage import unlink_file
        path = self._summary_path(project, task_id)
        unlink_file(path)

    def initialize(self, project):
        super().initialize(project)
        path = self._checked(self.root(project) / "identity.json")
        if not path.exists():
            atomic_json(path, {"id": new_id()})

    def identity(self, project):
        path = self._checked(self.root(project) / "identity.json")
        # 처리기 도입 전의 기존 기억 데이터도 그대로 둔 채, 처리기 세대 표지만 초기화한다.
        if not path.exists():
            atomic_json(path, {"id": new_id()})
        return read_json(path)["id"]

    def history_references(self, project):
        runs, messages, tasks = set(), set(), set()
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
        for path in self._checked(self.root(project) / "contexts").glob("*.json"):
            value = read_json(self._checked(path))
            self._validate_summary(path.stem, value)
            tasks.add(value["task_id"])
            source(value.get("source"))
        return {"run_ids": sorted(runs), "message_ids": sorted(messages), "task_ids": sorted(tasks)}

    def default_configuration(self):
        return {"tool_write_status": "candidate", "search_status": "confirmed",
                "search_limit": 10, "max_search_results": 100, "cache_records": 256}

    def validate_configuration(self, data):
        config = {**self.default_configuration(), **data}
        if type(config["cache_records"]) is not int or config["cache_records"] < 0:
            raise ValueError("cache_records must be nonnegative")
        self._status(config["tool_write_status"])
        self._status(config["search_status"], allow_all=True)
        if any(type(config[key]) is not int or config[key] < 1
               for key in ("search_limit", "max_search_results")) or config["search_limit"] > config["max_search_results"]:
            raise ValueError("Memory search limits must be positive and default <= maximum")
        from .processing import processing_settings
        processing_settings(data, token_counter=self.token_counter)

    def configuration_schema(self):
        from llm.core.schema import object_schema, completion_schema, field
        from .processing import processing_settings
        defaults = processing_settings({})
        properties = {}
        for key, value in defaults.items():
            kind = "boolean" if type(value) is bool else "integer" if type(value) is int else "number" if type(value) is float else "string"
            properties[key] = field(kind, value)
            if kind in ("integer", "number") and key != "priority":
                properties[key]["exclusiveMinimum"] = 0
        properties["recall_every"].pop("exclusiveMinimum", None)
        properties["recall_every"]["minimum"] = 0
        properties["context_tokens"] = field(["integer", "null"], None, minimum=1,
            **{"x-available": self.token_counter is not None})
        properties["completion"] = completion_schema()
        properties["extract_scope"]["enum"] = ["task", "project"]
        properties["failure_mode"]["enum"] = ["raise", "continue"]
        return object_schema({
            "cache_records": field("integer", 256, minimum=0),
            "tool_write_status": field("string", "candidate", enum=["candidate", "confirmed"]),
            "search_status": field("string", "confirmed", enum=["candidate", "confirmed", "all"]),
            "search_limit": field("integer", 10, minimum=1), "max_search_results": field("integer", 100, minimum=1),
            "processing": object_schema(properties, default=defaults)}, default=self.default_configuration())

    def effective_configuration(self, project):
        from llm.core.configuration import resolve_configuration
        from .processing import processing_settings
        return resolve_configuration({**self.default_configuration(), "processing": processing_settings({})},
                                     self.configuration_layers(project))

    def validate_record(self, identifier, data):
        record, history = data.get("record"), data.get("history")
        if not isinstance(record, dict) or not isinstance(history, list) or not history:
            raise ValueError("Memory requires a record and revision history")
        self._validate_memory(identifier, record)
        if len(history) != record["revision"]:
            raise ValueError("Incomplete memory revision history")
        for revision, entry in enumerate(history, 1):
            if not isinstance(entry, dict) or not isinstance(entry.get("data"), dict):
                raise ValueError("Invalid memory history entry")
            self._validate_memory(identifier, entry["data"])
            if entry.get("revision") != revision or entry["data"]["revision"] != revision or (
                    entry.get("operation") not in {"create", "save", "update", "delete", "restore"}
                    or not isinstance(entry.get("source"), dict) or entry.get("at") != entry["data"]["updated_at"]):
                raise ValueError("Invalid memory revision history")
        if history[-1]["data"] != record:
            raise ValueError("Memory and history disagree")

    def create(self, project, data, *, identifier=None, source=None):
        identifier = new_id() if identifier is None else validate_name(identifier)
        if self._record_path(project, identifier).exists():
            raise FileExistsError("Memory already exists")
        now = datetime.now(timezone.utc).isoformat()
        record = {"kind": "note", "scope": "project", "status": "confirmed", "tags": [],
                  **self._payload(data), "id": identifier, "revision": 1,
                  "created_at": now, "updated_at": now, "deleted": False,
                  "source": self._source(project, source)}
        self._commit(project, identifier, record, [], "create", source)
        return identifier

    def load(self, project, identifier, *, include_deleted=False, task_id=None):
        record = self._read(project, identifier)["record"]
        if not self._visible(record, task_id):
            raise FileNotFoundError("Memory is outside the requested scope")
        if record["deleted"] and not include_deleted:
            raise FileNotFoundError("Memory is deleted")
        return record

    def list(self, project, *, include_deleted=False, status=None, task_id=None):
        if status is not None:
            self._status(status, allow_all=True)
        records = {}
        for identifier, record in self._all_records(project):
            if self._visible(record, task_id) and (include_deleted or not record["deleted"]) and (status in (None, "all") or record["status"] == status):
                records[identifier] = record
        return records

    def save(self, project, identifier, data, *, expected_revision, source=None, task_id=None):
        return self._change(project, identifier, expected_revision, "save", changes=data, source=source, task_id=task_id)

    def update(self, project, identifier, changes, *, expected_revision, source=None, task_id=None):
        return self._change(project, identifier, expected_revision, "update", changes=changes, source=source, task_id=task_id)

    def delete(self, project, identifier, *, expected_revision, source=None, task_id=None):
        return self._change(project, identifier, expected_revision, "delete", source=source, task_id=task_id)

    def restore(self, project, identifier, *, expected_revision, source=None, task_id=None):
        return self._change(project, identifier, expected_revision, "restore", source=source, task_id=task_id)

    def purge(self, project, identifier, *, expected_revision, task_id=None):
        record = self.load(project, identifier, include_deleted=True, task_id=task_id)
        self._check_revision(record, expected_revision)
        if not record["deleted"]:
            raise ValueError("Soft-delete memory before permanent removal")
        super().delete(project, identifier)

    def history(self, project, identifier, *, task_id=None):
        self.load(project, identifier, include_deleted=True, task_id=task_id)
        return self._read(project, identifier)["history"]

    def score(self, query, record):
        """기본 검색은 Unicode 단어의 부분 일치 비율이다. 의미 검색이 필요하면 재정의한다."""
        terms = set(re.findall(r"\w+", query.casefold()))
        text = " ".join([record["content"], record["kind"], *record["tags"]]).casefold()
        return sum(term in text for term in terms) / len(terms) if terms else 0.0

    def search(self, project, query, *, limit=None, status=None, task_id=None):
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Memory query must be nonempty text")
        config = {**self.default_configuration(), **self.configuration(project)}
        limit = config["search_limit"] if limit is None else limit
        if type(limit) is not int or not 1 <= limit <= config["max_search_results"]:
            raise ValueError("Memory search limit is outside configured bounds")
        records = self.list(project, status=config["search_status"] if status is None else status, task_id=task_id)
        records = {key: record for key, record in records.items() if not self._expired(record)}
        scores = (self.search_fn(query, deepcopy(records)) if self.search_fn is not None else
                  {key: self.score(query, record) for key, record in records.items()})
        if not isinstance(scores, dict) or scores.keys() - records.keys() or any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in scores.values()):
            raise ValueError("Memory search adapter must return finite scores for visible memory IDs")
        hits = [{"score": score, "memory": records[key]} for key, score in scores.items() if score > 0]
        return sorted(hits, key=lambda hit: (-hit["score"], hit["memory"]["id"]))[:limit]

    def consolidate(self, project, identifier, *, expected_revision, task_id=None, source=None):
        from .consolidation import MemoryConsolidation
        return MemoryConsolidation(self, project).apply(identifier, expected_revision=expected_revision, task_id=task_id, source=source)

    def recover_consolidations(self, project):
        from .consolidation import MemoryConsolidation
        return MemoryConsolidation(self, project).recover()

    def pending_consolidations(self, project):
        from .consolidation import MemoryConsolidation
        return [path.stem for path in MemoryConsolidation(self, project).pending()]

    def validate_backup(self, project):
        self.configuration(project)
        list(self._all_records(project))
        for path in self._checked(self.root(project) / "contexts").glob("*.json"):
            self.summary(project, path.stem)

    def clone(self, source, destination):
        for identifier, _ in self._all_records(source):
            # 출처는 원래 Project/Run을 유지하며 복제 과정에서 새 기억으로 다시 작성하지 않는다.
            if self._record_path(destination, identifier).exists():
                raise FileExistsError("Destination memory already exists")
            self._write(destination, identifier, self._read(source, identifier))

    def resolve(self, project, capability):
        raise ValueError("Memory capabilities require a lifecycle-bound component data factory")

    def resolve_runtime(self, project, capability, *, data_factory):
        if capability == "completion_processors":
            from .processing import MemoryProcessor, processing_settings
            data = data_factory(self.name)
            settings = processing_settings(data.configuration(), token_counter=self.token_counter)
            return MemoryProcessor(data, completion_fn=self.completion_fn,
                                   token_counter=self.token_counter, priority=settings["priority"])
        if capability == "memory":
            return data_factory(self.name)
        if capability == "tools":
            from .tools import memory_tools
            return memory_tools(data_factory(self.name))
        return self.resolve(project, capability)
