"""Project 수명 검사와 잠금 아래에서 Memory API와 비동기 API를 제공한다."""

from llm.services.lifecycle.components import ComponentData
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.logging import log_event
from llm.services.infrastructure.storage import async_method
from copy import deepcopy


class MemoryData(ComponentData):
    """기억 CRUD. 변경은 마지막으로 읽은 generation/revision 쌍으로 검증한다."""

    def _write_memory(self, operation, *args, **kwargs):
        project, component = self._current()
        result = getattr(component, operation)(project, *args, **kwargs)
        log_event(project.paths.logs, "memory." + operation, entity_id=project.id)
        return result

    @workspace_locked
    def _processing_snapshot(self, session_id, *, include_records=True):
        project, component = self._current()
        return {"identity": component.identity(project), "configuration": component.get_config(project),
                "has_search": component.search_fn is not None or component._options(project).get("search_strategy") is not None,
                "records": component.list(project, include_deleted=True, session_id=session_id) if include_records else {},
                "summary": component.summary(project, session_id)}

    @workspace_locked
    def _extraction_prompt(self, identifier):
        """한 처리기 실행에서 사용할 명시적 추출 지침과 버전만 읽는다."""
        self._current()
        if identifier is None:
            raise ValueError("Memory extraction requires config.processing.extract_prompt_id")
        snapshot = self.related("prompts").snapshot(identifier)
        return {"text": "\n\n".join(f"{m['role']}: {m['content']}" for m in snapshot["data"]["messages"]),
                "version": snapshot["version"], "source": identifier}

    def _check_snapshot(self, project, component, snapshot):
        if (component.identity(project) != snapshot["identity"] or
                component.get_config(project) != snapshot["configuration"]):
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
                if ((ref["generation"], ref["revision"]) != (records[ref["id"]]["generation"], records[ref["id"]]["revision"]) or
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

    @workspace_locked
    def _compaction_state(self, session_id, scope, *, tool_id=None):
        from .compaction import Compactions
        return Compactions(self, session_id, scope, tool_id=tool_id).recover()

    @workspace_locked
    def _claim_compaction(self, session_id, scope, identifier, run_id, *, tool_id=None):
        from .compaction import Compactions
        storage = Compactions(self, session_id, scope, tool_id=tool_id)
        state = storage.read()
        request = state.get("request", {})
        if request.get("id") != identifier or request.get("status") != Compactions.PENDING:
            return False
        try:
            storage.check_sources(request)
        except ValueError:
            request.update(status=Compactions.STALE, error="Summary source changed")
            storage.write(state)
            return False
        request.update(status=Compactions.RUNNING, run_id=run_id)
        storage.write(state)
        return True

    @workspace_locked
    def _finish_compaction(self, session_id, scope, identifier, status, applied, error, *, run_id=None, tool_id=None):
        from .compaction import Compactions
        storage = Compactions(self, session_id, scope, tool_id=tool_id)
        state = storage.read()
        if state.get("request", {}).get("id") != identifier:
            if applied is not None:
                from .compaction import CompactionStopped
                raise CompactionStopped("Summary request was replaced")
            return
        if state["request"]["status"] == Compactions.CANCELLED:
            if applied is not None:
                from .compaction import CompactionStopped
                raise CompactionStopped("Summary cancelled before publication")
            return
        if run_id is not None:
            storage.require_running(state, identifier, run_id)
        if applied is not None:
            storage.check_sources(state["request"])
            checkpoint = state["request"].get("checkpoint")
            if (checkpoint is None or checkpoint["offset"] != checkpoint["total_chars"]
                    or checkpoint["summary"] != applied["summary"]):
                raise ValueError("Cannot publish an incomplete summary")
        state["request"].update(status=status, error=error)
        if applied is not None:
            state["applied"] = applied
        storage.write(state)

    @workspace_locked
    def _save_compaction(self, session_id, scope, identifier, run_id, fingerprint, total, progress, previous_offset, *, tool_id=None):
        from .compaction import Compactions
        Compactions(self, session_id, scope, tool_id=tool_id).checkpoint(
            identifier, run_id, fingerprint, total, progress, previous_offset)

    @workspace_locked
    def _observe_context(self, session_id, scope, view):
        from .compaction import Compactions
        storage = Compactions(self, session_id, scope)
        state = storage.read()
        state["view"] = view
        storage.write(state)

    def _read_source(self, reference, *, offset, limit):
        """Use the selected file owner's public snapshot/read contract, never its paths."""
        if type(offset) is not int or offset < 0 or limit is not None and (type(limit) is not int or limit < 0):
            raise ValueError("Source ranges must be nonnegative integers")
        from llm.services.infrastructure.storage import revision_token
        files = self.related(reference["component"])
        snapshot = files.snapshot(reference["identifier"])
        info = snapshot["data"].get("files", {}).get(reference["path"])
        if info is None or revision_token(info) != reference["revision"]:
            raise ValueError("Summary source file changed; the saved reference is stale")
        if limit == 0:
            return ""
        text = files.read_file(reference["identifier"], reference["path"], expected_revision=snapshot["version"]).decode("utf-8")
        return text[offset:] if limit is None else text[offset:offset + limit]

    @workspace_locked
    def _prepare_tool_summary(self, session_id, identifier, text, options, destination, mode, call_id):
        from .compaction import Compactions
        from llm.services.infrastructure.storage import revision_token
        storage = Compactions(self, session_id, None, tool_id=identifier)
        state = storage.recover()
        if state:
            storage.check_sources(state["request"])
            return state
        files = self.related(destination["component"])
        snapshot = files.snapshot(destination["identifier"])
        path = f"tool-results/{session_id}/{identifier}.txt"
        if path in snapshot["data"].get("files", {}):
            raise ValueError("Source file already exists without a summary request")
        snapshot = files.write_file(destination["identifier"], path, text, expected_revision=snapshot["version"])
        reference = {**destination, "path": path, "revision": revision_token(snapshot["data"]["files"][path])}
        state = {"request": {"id": identifier, "status": Compactions.APPLIED if mode == Compactions.REFERENCE else Compactions.PENDING,
            "mode": mode, "options": deepcopy(options), "reference": reference, "tool_call_id": call_id}}
        if mode == Compactions.REFERENCE:
            state["applied"] = {}
        storage.write(state)  # Source + reference share the workspace transaction.
        return deepcopy(state)

    # 공개 API. 생성과 조회는 revision이 필요 없고, 모든 변경은 CAS를 거친다.
    @staticmethod
    def describe_compaction():
        """애플리케이션이 선택할 압축 요청의 schema. 설정값을 생성하지 않는다."""
        from .compaction import compaction_schema
        return compaction_schema()

    @workspace_locked
    def get_context(self, session_id, *, scope=None):
        """마지막 모델 입력 경계의 토큰·예산·압축 가능한 턴과 요청 상태를 조회한다."""
        from .compaction import Compactions
        return Compactions(self, session_id, scope).view()

    @workspace_locked
    def request_compaction(self, session_id, options, *, expected_revision, scope=None):
        """명시한 압축을 다음 Loop 입력 경계에 예약한다. 이 API는 모델을 호출하지 않는다."""
        from .compaction import Compactions
        return Compactions(self, session_id, scope).request(options, expected_revision)

    aget_context = async_method(get_context)
    arequest_compaction = async_method(request_compaction)

    @workspace_locked
    def resume_compaction(self, session_id, *, expected_revision, scope=None):
        """Requeue the same immutable request and validated cursor; never resume a Tool effect."""
        from .compaction import Compactions
        return Compactions(self, session_id, scope).change(expected_revision, "resume")

    @workspace_locked
    def cancel_compaction(self, session_id, *, expected_revision, scope=None):
        """Cancel pending work or reject an in-flight result at its next save boundary."""
        from .compaction import Compactions
        return Compactions(self, session_id, scope).change(expected_revision, "cancel")

    aresume_compaction = async_method(resume_compaction)
    acancel_compaction = async_method(cancel_compaction)

    @workspace_locked
    def delete_compaction(self, session_id, *, expected_revision, scope=None):
        """Release a finished/cancelled derived context and its original-message references."""
        from .compaction import Compactions
        Compactions(self, session_id, scope).delete(expected_revision)

    adelete_compaction = async_method(delete_compaction)

    @workspace_locked
    def list_tool_summaries(self, session_id):
        """List saved result transformations, without loading the source text."""
        from .compaction import Compactions
        project, component = self._current()
        from llm.components.base import validate_name
        validate_name(session_id)
        prefix = Compactions.TOOLS + "/" + session_id + "/"
        result = []
        for key in sorted(Compactions.inventory(project, component)[1]):
            if key.startswith(prefix):
                identifier = key.removeprefix(prefix).removesuffix(".json")
                result.append(Compactions(self, session_id, None, tool_id=identifier).view())
        return result

    @workspace_locked
    def read_source(self, session_id, identifier, *, expected_revision, offset, limit):
        """Read a character range of a pinned Tool/MCP source in the owning Session."""
        from .compaction import Compactions
        state = Compactions(self, session_id, None, tool_id=identifier).read()
        reference = state["request"]["reference"]
        if reference["revision"] != expected_revision:
            raise ValueError("Source revision changed")
        return {"text": self._read_source(reference, offset=offset, limit=limit),
                "offset": offset, "reference": deepcopy(reference)}

    @workspace_locked
    def resume_tool_summary(self, session_id, identifier, *, expected_revision):
        from .compaction import Compactions
        return Compactions(self, session_id, None, tool_id=identifier).change(expected_revision, "resume")

    @workspace_locked
    def cancel_tool_summary(self, session_id, identifier, *, expected_revision):
        from .compaction import Compactions
        return Compactions(self, session_id, None, tool_id=identifier).change(expected_revision, "cancel")

    @workspace_locked
    def delete_tool_summary(self, session_id, identifier, *, expected_revision):
        """Release an inactive source reference; Data deletion remains a separate explicit action."""
        from .compaction import Compactions
        Compactions(self, session_id, None, tool_id=identifier).delete(expected_revision)

    alist_tool_summaries = async_method(list_tool_summaries)
    aread_source = async_method(read_source)
    aresume_tool_summary = async_method(resume_tool_summary)
    acancel_tool_summary = async_method(cancel_tool_summary)
    adelete_tool_summary = async_method(delete_tool_summary)

    @workspace_locked
    def consolidate(self, identifier, *, expected_revision, expected_generation, session_id=None):
        return self._write_memory("consolidate", identifier, expected_revision=expected_revision,
                                  expected_generation=expected_generation, session_id=session_id)

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
    def clear_summary(self, session_id: str, *, expected_revision: int, expected_generation: str) -> None:
        """원본은 유지하고 파생 캐시만 지운다. 다음 요청에서 설정에 따라 다시 요약한다."""
        self._write_memory("clear_summary", session_id, expected_revision=expected_revision, expected_generation=expected_generation)

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
    def save(self, identifier: str, data: dict, *, expected_revision: int, expected_generation: str, source=None, session_id=None) -> dict:
        """사용자 필드 전체 교체. load 결과에서 id/revision/출처 등 관리 필드를 제외해 전달한다."""
        return self._write_memory("save", identifier, data, expected_revision=expected_revision, expected_generation=expected_generation, source=source, session_id=session_id)

    @workspace_locked
    def update(self, identifier: str, changes: dict, *, expected_revision: int, expected_generation: str, source=None, session_id=None) -> dict:
        """최상위 사용자 필드를 병합한다. 중첩 사전은 통째로 교체된다."""
        return self._write_memory("update", identifier, changes, expected_revision=expected_revision, expected_generation=expected_generation, source=source, session_id=session_id)

    @workspace_locked
    def delete(self, identifier: str, *, expected_revision: int, expected_generation: str, source=None, session_id=None) -> dict:
        """복구 가능한 삭제. 기본 목록·검색·조회에서 제외한다."""
        return self._write_memory("delete", identifier, expected_revision=expected_revision, expected_generation=expected_generation, source=source, session_id=session_id)

    @workspace_locked
    def restore(self, identifier: str, *, expected_revision: int, expected_generation: str, source=None, session_id=None) -> dict:
        return self._write_memory("restore", identifier, expected_revision=expected_revision, expected_generation=expected_generation, source=source, session_id=session_id)

    @workspace_locked
    def purge(self, identifier: str, *, expected_revision: int, expected_generation: str, session_id=None) -> None:
        """이미 삭제한 기억과 모든 이력을 영구 제거한다. 모델 Tool로는 제공하지 않는다."""
        self._write_memory("purge", identifier, expected_revision=expected_revision, expected_generation=expected_generation, session_id=session_id)

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
