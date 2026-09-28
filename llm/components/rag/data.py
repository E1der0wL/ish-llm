"""UI/Tool에서 사용하는 문서 API. 모델 준비는 잠금 밖, 공개/조회는 잠금 안에서 수행한다."""

from copy import deepcopy
from uuid import uuid4

from llm.components.base import validate_name
from llm.services.lifecycle.components import ComponentData
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.storage import async_method
from llm.providers.observations import observe_component_models


def document_view(document: dict) -> dict:
    """벡터 같은 내부 색인 데이터를 공개 문서 응답에서 제외한다."""
    return deepcopy({key: value for key, value in document.items() if key not in ("vectors", "graph", "profile", "preparation_configuration")})


class RAGData(ComponentData):
    def _current(self):
        project, component = super()._current()
        return project, component.configured(project)

    @workspace_locked
    def _snapshot(self):
        project, component = self._current()
        return component, {**component.snapshot(project), "configuration_version": component._configuration_version,
                           "search_options": deepcopy(component.search_options)}

    @workspace_locked
    def _commit(self, snapshot, document):
        project, component = self._current()
        if snapshot.get("configuration_version") != component._configuration_version:
            from .component import RAGConflictError
            raise RAGConflictError("RAG configuration changed during preparation; retry explicitly")
        documents = dict(snapshot["documents"])
        documents[document["id"]] = document
        component.publish(project, snapshot, documents)
        return document_view(document)

    def _current_snapshot(self, snapshot):
        """저장 트랜잭션 안에서 수명과 세대를 검사하고 이미 읽은 스냅샷을 재사용한다."""
        from .component import RAGConflictError
        project, component = self._current()
        if (snapshot["identity"], snapshot["generation"]) != component.corpus_version(project):
            raise RAGConflictError("Corpus changed during retrieval; retry explicitly")
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
    async def _prepare(self, identifier, title, content, metadata, old=None):
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
            if old.get("expected_revision") is not None and old["expected_revision"] != document["revision"]:
                from .component import RAGConflictError
                raise RAGConflictError("Document revision changed")
            title = document["title"] if title is None else title
            content = document["content"] if content is None else content
            metadata = document["metadata"] if metadata is None else metadata
            revision = document["revision"] + 1
        # 이 await 동안 UI와 기존 검색은 계속 진행한다. 취소 시 공개 단계에 진입하지 않는다.
        prepared = await component.prepare(identifier, title, content,
                                           deepcopy({} if metadata is None else metadata), revision)
        return await self._async_call(self._commit, snapshot, prepared)

    @observe_component_models
    async def _retrieve(self, query: str, *, method=None, expand=None, limit=None, rerank=None):
        """BM25/벡터/하이브리드 검색. 선택 시 RerankModel로 최종 후보를 재정렬한다."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("Query must be nonempty text")
        component, snapshot = await self._async_call(self._snapshot)
        options = {"method": method, "expand": expand, "limit": limit, "rerank": rerank}
        options = {key: component.search_options[key] if value is None else value for key, value in options.items()}
        method, expand, limit, rerank = (options[key] for key in ("method", "expand", "limit", "rerank"))
        if type(rerank) is not bool:
            raise ValueError("rerank must be boolean")
        if method not in ("bm25", "vector", "hybrid") or expand not in ("chunk", "section", "document"):
            raise ValueError("Invalid search method or expansion")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        vector = None
        if snapshot["documents"] and method != "bm25":
            await self._async_call(self.require_model_observation, component.embedding)
            if component._identity() != snapshot["profile"]["model"]:
                raise ValueError("Query embedding model differs from indexed model")
            vector = (await component.embed([query], query=True))[0]
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
    def enqueue_document(self, *, title, content, metadata=None, identifier=None):
        """문서 등록 요청을 영속 큐에 넣는다. 모델 호출은 arun_job에서 명시적으로 시작한다."""
        from .jobs import RAGJobs
        return RAGJobs(self).enqueue(title=title, content=content, metadata=metadata, identifier=identifier)

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

    @observe_component_models
    async def arun_job(self, identifier, *, retry=False):
        from .jobs import RAGJobs
        return await RAGJobs(self).run(identifier, retry=retry)

    async def arun_jobs(self, *, limit=None):
        """현재 영속 대기열을 순차 처리한다. 실패 작업은 남기고 다음 작업으로 진행한다."""
        if limit is not None and (type(limit) is not int or limit < 1):
            raise ValueError("limit must be positive")
        results = []
        for job in await self.ajobs():
            if job["status"] != "queued":
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

    async def aadd_document(self, *, title: str, content: str, metadata=None, identifier=None) -> dict:
        """원문과 색인을 함께 등록한다. 완료된 문서 dict를 반환하며 백그라운드 job은 만들지 않는다."""
        identifier = uuid4().hex if identifier is None else validate_name(identifier)
        return await self._prepare(identifier, title, content, metadata)

    async def aupdate_document(self, identifier: str, *, content=None, title=None,
                               metadata=None, expected_revision=None) -> dict:
        """새 문서/색인을 완성한 뒤 교체한다. 실패하면 기존 버전이 유지된다."""
        return await self._prepare(validate_name(identifier), title, content, metadata,
                                   {"expected_revision": expected_revision})

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
    def delete_document(self, identifier: str, *, expected_revision=None) -> None:
        from .component import RAGConflictError
        project, component = self._current()
        snapshot = component.snapshot(project)
        documents = dict(snapshot["documents"])
        document = documents.pop(validate_name(identifier), None)
        if document is None:
            raise FileNotFoundError(identifier)
        if expected_revision is not None and expected_revision != document["revision"]:
            raise RAGConflictError("Document revision changed")
        component.publish(project, snapshot, documents)

    async def asearch_documents(self, query: str, *, method=None, expand=None, limit=None, rerank=None) -> list:
        """문서 목록만 필요한 코드용 API. 일반 질의에는 asearch를 사용한다."""
        _, hits = await self._retrieve(query, method=method, expand=expand, limit=limit, rerank=rerank)
        return hits

    async def asearch(self, query: str, *, method=None, expand=None, limit=None, rerank=None,
                      max_hops=None, relation_limit=None) -> dict:
        """문서와 해당 문단에 근거한 관계·출처를 한 세대에서 반환한다."""
        for value, maximum in ((max_hops, 5), (relation_limit, 1000)):
            if value is not None and (type(value) is not int or not 1 <= value <= maximum):
                raise ValueError("Invalid graph search limit")
        snapshot, hits = await self._retrieve(query, method=method, expand=expand, limit=limit, rerank=rerank)
        max_hops = snapshot["search_options"]["max_hops"] if max_hops is None else max_hops
        relation_limit = snapshot["search_options"]["relation_limit"] if relation_limit is None else relation_limit
        if type(max_hops) is not int or not 1 <= max_hops <= 5:
            raise ValueError("max_hops must be between 1 and 5")
        if type(relation_limit) is not int or not 1 <= relation_limit <= 1000:
            raise ValueError("relation_limit must be between 1 and 1000")
        return await self._async_call(self._combined, snapshot, query, hits,
                                     {"max_hops": max_hops, "relation_limit": relation_limit})

    @workspace_locked
    def graph_search(self, seed: str, *, max_hops=None, limit=None) -> dict:
        """정확한 엔티티 이름을 아는 호출자를 위한 직접 관계 조회."""
        if not isinstance(seed, str) or not seed.strip():
            raise ValueError("Seed must be a nonempty entity name")
        project, component = self._current()
        max_hops = component.search_options["max_hops"] if max_hops is None else max_hops
        limit = component.search_options["relation_limit"] if limit is None else limit
        if type(max_hops) is not int or not 1 <= max_hops <= 5:
            raise ValueError("max_hops must be between 1 and 5")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        return component.graph_search(project, seed=seed, max_hops=max_hops, limit=limit)

    agraph_search = async_method(graph_search)

    @workspace_locked
    def compact(self) -> None:
        project, component = self._current()
        component.compact(project)

    aget_document = async_method(get_document)
    alist_documents = async_method(list_documents)
    adelete_document = async_method(delete_document)
    acompact = async_method(compact)
