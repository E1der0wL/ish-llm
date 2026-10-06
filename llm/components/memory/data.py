"""Project 수명 검사와 잠금 아래에서 Memory API와 비동기 API를 제공한다."""

from llm.services.lifecycle.components import ComponentData
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.logging import log_event
from llm.services.infrastructure.storage import async_method
from copy import deepcopy


class MemoryData(ComponentData):
    """열린 기억 레코드 CRUD. 변경 시 마지막으로 읽은 expected_revision이 필수다."""

    def _write_memory(self, operation, *args, **kwargs):
        project, component = self._current()
        result = getattr(component, operation)(project, *args, **kwargs)
        log_event(project.paths.logs, "memory." + operation, entity_id=project.id)
        return result

    @workspace_locked
    def _processing_snapshot(self, session_id, *, include_records=True):
        project, component = self._current()
        return {"identity": component.identity(project), "configuration": component.configuration(project),
                "has_search": component.search_fn is not None or component._options(project).get("search_strategy") is not None,
                "records": component.list(project, include_deleted=True, session_id=session_id) if include_records else {},
                "summary": component.summary(project, session_id)}

    @workspace_locked
    def _extraction_prompt(self, identifier):
        """한 처리기 실행에서 사용할 명시적 추출 지침과 버전만 읽는다."""
        _, component = self._current()
        if component.extract_prompt is not None:
            from llm.services.infrastructure.storage import revision_token
            return {"text": component.extract_prompt, "version": revision_token(component.extract_prompt), "source": "host"}
        if identifier is None:
            raise ValueError("Memory extraction requires processing.extract_prompt_id or host extract_prompt")
        snapshot = self.related("prompts").snapshot(identifier)
        return {"text": "\n\n".join(f"{m['role']}: {m['content']}" for m in snapshot["data"]["messages"]),
                "version": snapshot["version"], "source": identifier}

    def _check_snapshot(self, project, component, snapshot):
        if (component.identity(project) != snapshot["identity"] or
                component.configuration(project) != snapshot["configuration"]):
            from .component import MemoryConflictError
            raise MemoryConflictError("Memory configuration or storage changed during processing")

    @workspace_locked
    def _goal_references(self, session_id, identifiers):
        """명시된 Goal만 참조한다. Goal의 소유권과 버전 검사는 GoalData에 남긴다."""
        if not identifiers:
            return []
        refs = self.related("goals").context_references(session_id, identifiers=identifiers)
        return [{key: deepcopy(ref[key]) for key in ("goal_id", "version", "objective", "success_criteria") if key in ref}
                for ref in refs]

    @workspace_locked
    def _publish_summary(self, snapshot, record, *, session_id, source):
        project, component = self._current()
        self._check_snapshot(project, component, snapshot)
        if "goal_references" in snapshot and self._goal_references(session_id, snapshot["goal_ids"]) != snapshot["goal_references"]:
            from .component import MemoryConflictError
            raise MemoryConflictError("Goal changed during summary processing")
        prior = snapshot["summary"]
        record = {**deepcopy(record), "source": deepcopy(source)}
        return component.publish_summary(project, session_id, record, expected_revision=prior["revision"] if prior else 0,
                                         expected_generation=prior["generation"] if prior else None)

    @workspace_locked
    def _publish_candidates(self, snapshot, candidates, *, session_id, source):
        from .processing import content_key
        from .component import MemoryConflictError
        project, component = self._current()
        self._check_snapshot(project, component, snapshot)
        records = component.list(project, include_deleted=True, session_id=session_id)
        # 모든 참조를 쓰기 전에 검사한다. 모델에게 다른 Session의 ID/버전을 선택하게 하지 않는다.
        for candidate in candidates:
            for ref in candidate.get("replaces", []):
                if ref["id"] not in snapshot["records"] or ref["id"] not in records:
                    raise MemoryConflictError("Consolidation reference is unavailable")
                if (ref["revision"] != records[ref["id"]]["revision"] or
                        records[ref["id"]] != snapshot["records"][ref["id"]]):
                    raise MemoryConflictError("Consolidation reference changed")
        keys = {(r["scope"], r.get("session_id"), content_key(r["content"])) for r in records.values()}
        created = []
        for candidate in candidates:
            key = (candidate["scope"], candidate.get("session_id"), content_key(candidate["content"]))
            if key in keys:
                continue  # 삭제된 기억도 자동으로 되살리지 않는다.
            created.append(component.create(project, candidate, source=source))
            keys.add(key)
        log_event(project.paths.logs, "memory.extracted", entity_id=project.id, count=len(created))
        return created

    @workspace_locked
    def _tool_write(self, operation, arguments, source):
        # 설정 검사와 쓰기를 하나의 잠금 범위에서 수행한다. Tool 인자로 상태를 승격할 수 없다.
        project, component = self._current()
        config = component._options(project)
        if operation != "delete" and "tool_write_status" not in config:
            raise ValueError("Missing required setting: memory.tool_write_status")
        arguments = dict(arguments)
        if operation == "create":
            if arguments.get("scope") == "session":
                arguments["session_id"] = source["session_id"]
            arguments = {"data": {**arguments, "status": config["tool_write_status"]}}
        elif operation == "update":
            arguments["changes"] = {**arguments["changes"], "status": config["tool_write_status"]}
        elif operation != "delete":
            raise ValueError("Unsupported memory Tool mutation")
        if operation != "create":
            arguments["session_id"] = source["session_id"]
        result = self._write_memory(operation, **arguments, source=source)
        return component.load(project, result, session_id=source["session_id"]) if operation == "create" else result

    # 공개 API. 생성과 조회는 revision이 필요 없고, 모든 변경은 CAS를 거친다.
    @workspace_locked
    def consolidate(self, identifier, *, expected_revision, session_id=None):
        return self._write_memory("consolidate", identifier, expected_revision=expected_revision, session_id=session_id)

    @workspace_locked
    def recover_consolidations(self):
        return self._write_memory("recover_consolidations")

    @workspace_locked
    def pending_consolidations(self):
        project, component = self._current()
        return component.pending_consolidations(project)

    aconsolidate = async_method(consolidate)
    arecover_consolidations = async_method(recover_consolidations)
    apending_consolidations = async_method(pending_consolidations)

    @workspace_locked
    def summary(self, session_id: str):
        """Session의 파생 요약과 원본 범위 해시를 조회한다. 없으면 None이다."""
        project, component = self._current()
        return component.summary(project, session_id)

    @workspace_locked
    def clear_summary(self, session_id: str, *, expected_revision: int) -> None:
        """원본은 유지하고 파생 캐시만 지운다. 다음 요청에서 설정에 따라 다시 요약한다."""
        self._write_memory("clear_summary", session_id, expected_revision=expected_revision)

    @workspace_locked
    def review(self, *, session_id=None) -> dict:
        """정확히 중복된 기억, 만료된 기억, 모델이 제안한 대체 관계를 UI에 제공한다."""
        from .processing import content_key
        project, component = self._current()
        groups, expired, proposals = {}, [], []
        for identifier, record in component.list(project, session_id=session_id).items():
            groups.setdefault((record["scope"], record.get("session_id"), content_key(record["content"])), []).append(identifier)
            if component._expired(record):
                expired.append(identifier)
            if record.get("replaces"):
                proposals.append(deepcopy(record))
        return {"duplicates": [ids for ids in groups.values() if len(ids) > 1],
                "expired": expired, "proposals": proposals}

    @workspace_locked
    def create(self, data: dict, *, identifier=None, source=None) -> str:
        """API 작성은 confirmed가 기본이다. 사용자 검토 대기는 status='candidate'로 저장한다."""
        return self._write_memory("create", data, identifier=identifier, source=source)

    @workspace_locked
    def load(self, identifier: str, *, include_deleted=False, session_id=None) -> dict:
        project, component = self._current()
        return component.load(project, identifier, include_deleted=include_deleted, session_id=session_id)

    @workspace_locked
    def list(self, *, include_deleted=False, status=None, session_id=None) -> dict:
        project, component = self._current()
        return component.list(project, include_deleted=include_deleted, status=status, session_id=session_id)

    @workspace_locked
    def save(self, identifier: str, data: dict, *, expected_revision: int, source=None, session_id=None) -> dict:
        """사용자 필드 전체 교체. load 결과에서 id/revision/출처 등 관리 필드를 제외해 전달한다."""
        return self._write_memory("save", identifier, data, expected_revision=expected_revision, source=source, session_id=session_id)

    @workspace_locked
    def update(self, identifier: str, changes: dict, *, expected_revision: int, source=None, session_id=None) -> dict:
        """최상위 사용자 필드를 병합한다. 중첩 사전은 통째로 교체된다."""
        return self._write_memory("update", identifier, changes, expected_revision=expected_revision, source=source, session_id=session_id)

    @workspace_locked
    def delete(self, identifier: str, *, expected_revision: int, source=None, session_id=None) -> dict:
        """복구 가능한 삭제. 기본 목록·검색·조회에서 제외한다."""
        return self._write_memory("delete", identifier, expected_revision=expected_revision, source=source, session_id=session_id)

    @workspace_locked
    def restore(self, identifier: str, *, expected_revision: int, source=None, session_id=None) -> dict:
        return self._write_memory("restore", identifier, expected_revision=expected_revision, source=source, session_id=session_id)

    @workspace_locked
    def purge(self, identifier: str, *, expected_revision: int, session_id=None) -> None:
        """이미 삭제한 기억과 모든 이력을 영구 제거한다. 모델 Tool로는 제공하지 않는다."""
        self._write_memory("purge", identifier, expected_revision=expected_revision, session_id=session_id)

    @workspace_locked
    def history(self, identifier: str, *, session_id=None) -> list:
        project, component = self._current()
        return component.history(project, identifier, session_id=session_id)

    @workspace_locked
    def search(self, query: str, *, limit=None, status=None, session_id=None) -> list:
        """[{score, memory}] 반환. status='all'이면 candidate도 포함한다. 삭제한 기억은 제외한다."""
        project, component = self._current()
        return component.search(project, query, limit=limit, status=status, session_id=session_id)

    acreate = async_method(create)
    asummary = async_method(summary)
    aclear_summary = async_method(clear_summary)
    areview = async_method(review)
    aload = async_method(load)
    alist = async_method(list)
    asave = async_method(save)
    aupdate = async_method(update)
    adelete = async_method(delete)
    arestore = async_method(restore)
    apurge = async_method(purge)
    ahistory = async_method(history)
    asearch = async_method(search)
