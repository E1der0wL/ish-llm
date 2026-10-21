"""UI/Tool에서 사용하는 문서 API. 모델 준비는 잠금 밖, 공개/조회는 잠금 안에서 수행한다."""

from copy import deepcopy
from llm.core.configuration import require_config
from uuid import uuid4

from llm.components.base import validate_name
from llm.services.lifecycle.components import ComponentData
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.storage import async_method
from llm.providers.observations import observe_component_models


def document_view(document: dict) -> dict:
    """벡터 같은 내부 색인 데이터를 공개 문서 응답에서 제외한다."""
    return deepcopy({key: value for key, value in document.items() if key not in (
        "vectors", "graph", "profile", "preparation_configuration", "preparation_fingerprint", "extraction_manifest")})


def validate_documents(documents):
    """Validate the entire explicit create batch before model calls or queue admission."""
    from .component import RAGComponent
    if not isinstance(documents, list) or not documents:
        raise ValueError("documents must be a nonempty list")
    result, identifiers = [], set()
    for position, source in enumerate(documents):
        if not isinstance(source, dict):
            raise ValueError(f"documents[{position}] has unsupported fields")
        item = deepcopy(source)
        identifier = uuid4().hex if item.get("identifier") is None else validate_name(item["identifier"])
        if identifier in identifiers:
            raise ValueError(f"Duplicate document identifier: {identifier}")
        identifiers.add(identifier)
        item.update(identifier=identifier, metadata={} if item.get("metadata") is None else item["metadata"])
        RAGComponent.validate_input(item)
        result.append(item)
    return result


class RAGData(ComponentData):
    def _current(self):
        project, component = super()._current()
        worker = component.with_config(project)
        prompt_id = worker.extraction_options.get("prompt_id")
        if prompt_id is not None:
            try:
                if "prompts" not in project.components:
                    raise ValueError("RAG extraction.prompt_id requires selecting the prompts component")
                prompts = self.registry.get("prompts")
                if "prompts" not in prompts.capabilities:
                    raise ValueError("Selected prompts component must provide prompts capability")
                worker = component.with_config(project, prompt=prompts.load(project, prompt_id))
            except (FileNotFoundError, ValueError) as error:
                # 깨진 참조가 있어도 설정 수정·기존 자료 조회·실패 작업 정리는 가능해야 한다.
                # 새 문서 준비는 모델 호출 전에 실패한다.
                worker._extraction_error = error
        return project, worker

    @workspace_locked
    def _snapshot(self):
        project, component = self._current()
        snapshot = component.snapshot(project)
        component._embedding_dimensions = (snapshot.get("profile") or {}).get("dimensions")
        return component, {**snapshot, "configuration_version": component._configuration_version,
                           "source_version": component.source_version(project),
                           "search_options": deepcopy(component.search_options)}

    @workspace_locked
    def _commit(self, snapshot, document):
        return self._commit_documents(snapshot, [document])[0]

    @workspace_locked
    def _commit_documents(self, snapshot, prepared):
        project, component = self._current()
        if snapshot.get("configuration_version") != component._configuration_version:
            from .component import RAGConflictError
            raise RAGConflictError("RAG configuration changed during preparation; retry explicitly")
        documents = dict(snapshot["documents"])
        documents.update({document["id"]: document for document in prepared})
        component.publish(project, snapshot, documents)
        return [document_view(document) for document in prepared]

    def _current_snapshot(self, snapshot):
        """저장 트랜잭션 안에서 수명과 세대를 검사하고 이미 읽은 스냅샷을 재사용한다."""
        from .component import RAGConflictError
        project, component = self._current()
        current = component.source_version(project)
        if (snapshot["identity"], snapshot["generation"]) != current[:2]:
            raise RAGConflictError("Corpus changed during retrieval; retry explicitly")
        if snapshot["source_version"] != current:
            raise RAGConflictError("RAG source evidence changed during retrieval; retry explicitly")
        if snapshot.get("configuration_version") != component._configuration_version:
            raise RAGConflictError("RAG configuration changed during retrieval; retry explicitly")
        return project, component

    @workspace_locked
    def _search(self, snapshot, query, vector, options):
        project, component = self._current_snapshot(snapshot)
        return component.search(project, snapshot, query, vector, **options)

    @workspace_locked
    def _combined(self, snapshot, query, hits, options):
        project, component = self._current_snapshot(snapshot)
        return component.combined_result(project, snapshot, query, hits, **options)

    @observe_component_models
    async def _prepare(self, identifier, title, content, metadata, old=None, description=None):
        if description is not None and not isinstance(description, str):
            raise ValueError("Document description must be text")
        component, snapshot = await self._async_call(self._snapshot)
        await self._async_call(self.require_model_observation, component.embedding, component.extractor)
        if old is None:
            if identifier in snapshot["documents"]:
                raise FileExistsError(identifier)
            revision = 1
        else:
            document = snapshot["documents"].get(identifier)
            if document is None:
                raise FileNotFoundError(identifier)
            component._check_revision(document, old["expected_revision"], old["expected_generation"])
            title = document["title"] if title is None else title
            content = document["content"] if content is None else content
            metadata = document["metadata"] if metadata is None else metadata
            description = document.get("description") if description is None else description
            revision = document["revision"] + 1
        # 이 await 동안 UI와 기존 검색은 계속 진행한다. 취소 시 공개 단계에 진입하지 않는다.
        from .component import RAGComponent
        options = {"previous": snapshot["documents"].get(identifier)} if type(component).prepare is RAGComponent.prepare else {}
        prepared = await component.prepare(identifier, title, content,
                                           deepcopy({} if metadata is None else metadata), revision,
                                           **options)
        if description is not None:
            prepared["description"] = description
        if old is not None:
            # prepare를 교체한 구현에도 문서 update의 생성 세대 보존 계약은 동일하다.
            prepared["generation"] = snapshot["documents"][identifier]["generation"]
        return await self._async_call(self._commit, snapshot, prepared)

    @observe_component_models
    async def _retrieve(self, query: str, *, method=None, expand=None, limit=None, rerank=None):
        """BM25/벡터/하이브리드 검색. 선택 시 RerankModel로 최종 후보를 재정렬한다."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Query must be nonempty text")
        component, snapshot = await self._async_call(self._snapshot)
        options = {"method": method, "expand": expand, "limit": limit, "rerank": rerank}
        options = {key: (component.search_options.get(key, False) if key == "rerank" else require_config(component.search_options, key, scope="rag.search")) if value is None else value for key, value in options.items()}
        method, expand, limit, rerank = (options[key] for key in ("method", "expand", "limit", "rerank"))
        if type(rerank) is not bool:
            raise ValueError("rerank must be boolean")
        if method not in ("bm25", "vector", "hybrid") or expand not in ("chunk", "section", "document"):
            raise ValueError("Invalid search method or expansion")
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        vector = None
        if snapshot["documents"] and method != "bm25":
            await self._async_call(self.require_model_observation, component.embedding)
            if component._identity() != snapshot["profile"]["model"]:
                raise ValueError("Query embedding model differs from indexed model")
            vector = await component.embed_query(query)
            if len(vector) != snapshot["profile"]["dimensions"]:
                raise ValueError("Query embedding dimensions differ from index")
        if rerank and component.reranker is None:
            raise ValueError("Configure a reranker first")
        hits = await self._async_call(self._search, snapshot, query, vector,
                                     {"method": method, "expand": expand, "limit": limit})
        if rerank and hits:
            await self._async_call(self.require_model_observation, component.reranker)
            response = await component.reranker.rerank(query, [hit["text"] for hit in hits])
            rows = response["results"] if isinstance(response, dict) else response.results
            order = [row["index"] for row in rows]
            if len(set(order)) != len(order) or any(type(i) is not int or not 0 <= i < len(hits) for i in order):
                raise ValueError("Invalid rerank indexes")
            hits = [{**hits[row["index"]], "rerank_score": row["relevance_score"]} for row in rows]
            # 모델 호출 중 프로젝트가 삭제/해제되었는지도 다시 확인한다.
            await self._async_call(self._current_snapshot, snapshot)
        return snapshot, hits

    # 공개 API
    @workspace_locked
    def has_reranker(self):
        """현재 Project의 실행 가능한 reranker 유무. Tool 스키마와 동일한 바인딩을 사용한다."""
        return self._current()[1].reranker is not None

    def enqueue_document(self, *, title, content, metadata=None, identifier=None, description=None):
        """문서 등록 요청을 영속 큐에 넣는다. 모델 호출은 arun_job에서 명시적으로 시작한다."""
        from .jobs import RAGJobs
        return RAGJobs(self).enqueue(title=title, content=content, metadata=metadata, identifier=identifier, description=description)

    def enqueue_documents(self, documents):
        """Queue an explicit document batch; its one Job publishes all documents together."""
        from .jobs import RAGJobs
        return RAGJobs(self).enqueue_documents(documents)

    aenqueue_documents = async_method(enqueue_documents)

    def jobs(self):
        from .jobs import RAGJobs
        return RAGJobs(self).list()

    def job(self, identifier):
        from .jobs import RAGJobs
        return RAGJobs(self).get(identifier)

    def job_progress(self, identifier):
        """작업 상태 전이와 별개인 공통 UI 진행 조회. 전체량을 추정하지 않는다."""
        from .jobs import RAGJobs
        return RAGJobs(self).progress(identifier)

    ajob_progress = async_method(job_progress)

    def cancel_job(self, identifier):
        from .jobs import RAGJobs
        return RAGJobs(self).cancel(identifier)

    def checkpoint_plan(self, identifier):
        """Job의 재개 포기/전체 삭제 계획. RunHandle과 같은 CheckpointPlan을 반환한다."""
        from .jobs import RAGJobs
        return RAGJobs(self).checkpoint_plan(identifier)

    def resume_plan(self, identifier):
        """Read Job-owned eligibility and revision without invoking any model."""
        from .jobs import RAGJobs
        return RAGJobs(self).resume_plan(identifier)

    aresume_plan = async_method(resume_plan)

    def job_diagnostic(self, identifier):
        from llm.core.contracts import Diagnostic
        value = self.job(identifier).get("diagnostic")
        return Diagnostic.from_dict(value) if value is not None else None

    ajob_diagnostic = async_method(job_diagnostic)

    def abandon_job(self, identifier, *, expected_revision):
        from .jobs import RAGJobs
        return RAGJobs(self).abandon(identifier, expected_revision=expected_revision)

    def delete_job(self, identifier, *, expected_revision):
        from .jobs import RAGJobs
        return RAGJobs(self).delete(identifier, expected_revision=expected_revision)

    def job_cleanup_plan(self):
        from .jobs import RAGJobs
        return RAGJobs(self).cleanup_plan()

    def prune_jobs(self, identifiers, *, expected_version):
        from .jobs import RAGJobs
        return RAGJobs(self).prune(identifiers, expected_version=expected_version)

    acheckpoint_plan = async_method(checkpoint_plan)
    aabandon_job = async_method(abandon_job)
    adelete_job = async_method(delete_job)
    ajob_cleanup_plan = async_method(job_cleanup_plan)
    aprune_jobs = async_method(prune_jobs)

    @observe_component_models
    async def arun_job(self, identifier, *, retry=False, expected_revision=None):
        from .jobs import RAGJobs
        return await RAGJobs(self).run(identifier, retry=retry, expected_revision=expected_revision)

    async def arun_jobs(self, *, limit=None):
        """현재 영속 대기열을 순차 처리한다. 실패 작업은 남기고 다음 작업으로 진행한다."""
        if limit is not None and (type(limit) is not int or limit < 1):
            raise ValueError("limit must be positive")
        results = []
        for job in await self.ajobs():
            if job["status"] != "queued" or job["resume_abandonment"] is not None:
                continue
            try:
                results.append(await self.arun_job(job["id"]))
            except Exception:
                results.append(await self.ajob(job["id"]))
            if limit is not None and len(results) >= limit:
                break
        return results

    aenqueue_document = async_method(enqueue_document)
    ajobs = async_method(jobs)
    ajob = async_method(job)
    acancel_job = async_method(cancel_job)

    async def aadd_document(self, *, title: str, content: str, metadata=None, identifier=None, description=None) -> dict:
        """원문과 색인을 함께 등록한다. 완료된 문서 dict를 반환하며 백그라운드 job은 만들지 않는다."""
        identifier = uuid4().hex if identifier is None else validate_name(identifier)
        return await self._prepare(identifier, title, content, metadata, description=description)

    @observe_component_models
    async def aadd_documents(self, documents) -> list:
        """Prepare the caller's batch, then publish one generation. Use a Job for durable retry."""
        inputs = validate_documents(documents)
        component, snapshot = await self._async_call(self._snapshot)
        for item in inputs:
            if item["identifier"] in snapshot["documents"]:
                raise FileExistsError(item["identifier"])
        prepared = []
        for item in inputs:
            await self._async_call(self.require_model_observation, component.embedding, component.extractor)
            document = await component.prepare(item["identifier"], item["title"], item["content"], item["metadata"], 1)
            if item.get("description") is not None:
                document["description"] = item["description"]
            component._embedding_dimensions = document["profile"]["dimensions"]
            prepared.append(document)
        return await self._async_call(self._commit_documents, snapshot, prepared)

    async def aupdate_document(self, identifier: str, *, content=None, title=None,
                               metadata=None, expected_revision=None, expected_generation=None, description=None) -> dict:
        """문서/색인을 교체한다. CAS 사용 시 읽은 generation과 revision을 함께 전달한다."""
        return await self._prepare(validate_name(identifier), title, content, metadata,
                                   {"expected_revision": expected_revision, "expected_generation": expected_generation}, description=description)

    @workspace_locked
    def list_catalog(self, *, limit: int, cursor=None) -> dict:
        """본문·벡터를 읽지 않는 세대 고정 목록. 페이지 크기는 호출자가 명시한다."""
        project, component = self._current()
        return component.list_catalog(project, limit=limit, cursor=cursor)

    alist_catalog = async_method(list_catalog)

    @workspace_locked
    def get_document(self, identifier: str) -> dict:
        _, snapshot = self._snapshot()
        try:
            return document_view(snapshot["documents"][validate_name(identifier)])
        except KeyError:
            raise FileNotFoundError(identifier) from None

    @workspace_locked
    def list_documents(self) -> list:
        _, snapshot = self._snapshot()
        return [document_view(document) for document in snapshot["documents"].values()]

    @workspace_locked
    def delete_document(self, identifier: str, *, expected_revision=None, expected_generation=None) -> None:
        project, component = self._current()
        snapshot = component.snapshot(project)
        documents = dict(snapshot["documents"])
        document = documents.pop(validate_name(identifier), None)
        if document is None:
            raise FileNotFoundError(identifier)
        component._check_revision(document, expected_revision, expected_generation)
        component.publish(project, snapshot, documents)

    async def asearch_documents(self, query: str, *, method=None, expand=None, limit=None, rerank=None) -> list:
        """문서 목록만 필요한 코드용 API. 일반 질의에는 asearch를 사용한다."""
        _, hits = await self._retrieve(query, method=method, expand=expand, limit=limit, rerank=rerank)
        return hits

    async def asearch(self, query: str, *, method=None, expand=None, limit=None, rerank=None,
                      max_hops=None, relation_limit=None) -> dict:
        """문서와 해당 문단에 근거한 관계·출처를 한 세대에서 반환한다."""
        for value in (max_hops, relation_limit):
            if value is not None and (type(value) is not int or value < 1):
                raise ValueError("Invalid graph search limit")
        snapshot, hits = await self._retrieve(query, method=method, expand=expand, limit=limit, rerank=rerank)
        max_hops = require_config(snapshot["search_options"], "max_hops", scope="rag.search") if max_hops is None else max_hops
        relation_limit = require_config(snapshot["search_options"], "relation_limit", scope="rag.search") if relation_limit is None else relation_limit
        if type(max_hops) is not int or max_hops < 1:
            raise ValueError("max_hops must be a positive integer")
        if type(relation_limit) is not int or relation_limit < 1:
            raise ValueError("relation_limit must be a positive integer")
        return await self._async_call(self._combined, snapshot, query, hits,
                                     {"max_hops": max_hops, "relation_limit": relation_limit})

    @workspace_locked
    def graph_search(self, seed: str, *, max_hops=None, limit=None) -> dict:
        """정확한 엔티티 이름을 아는 호출자를 위한 직접 관계 조회."""
        if not isinstance(seed, str) or not seed.strip():
            raise ValueError("Seed must be a nonempty entity name")
        project, component = self._current()
        max_hops = require_config(component.search_options, "max_hops", scope="rag.search") if max_hops is None else max_hops
        limit = require_config(component.search_options, "relation_limit", scope="rag.search") if limit is None else limit
        if type(max_hops) is not int or max_hops < 1:
            raise ValueError("max_hops must be a positive integer")
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be a positive integer")
        return component.graph_search(project, seed=seed, max_hops=max_hops, limit=limit)

    agraph_search = async_method(graph_search)

    @workspace_locked
    def compaction_plan(self):
        project, component = self._current()
        return component.compaction_plan(project)

    @workspace_locked
    def recover_compaction(self):
        project, component = self._current()
        return component.recover_compaction(project)

    @workspace_locked
    def compact(self, generations, *, expected_version) -> None:
        project, component = self._current()
        component.compact(project, generations, expected_version=expected_version)

    aget_document = async_method(get_document)
    alist_documents = async_method(list_documents)
    adelete_document = async_method(delete_document)
    acompact = async_method(compact)
    acompaction_plan = async_method(compaction_plan)
    arecover_compaction = async_method(recover_compaction)
