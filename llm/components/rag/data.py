"""UI/Tool에서 사용하는 문서 API. 모델 준비는 잠금 밖, 공개/조회는 잠금 안에서 수행한다."""

from .graph_records import GraphRecord
from .ingestion_records import EmbeddingEvidence

from .status import RAGJobStatus
from copy import deepcopy
from .search import SearchMethod, validate_search_options, SearchOptions
from .retrieval_records import RetrievalHit
from .chunk_records import RAGChunk, SplitDocument
from .document_records import RAGDocument, RAGDocumentInput
from .corpus_records import RAGCorpus
from .document_views import DocumentView, DocumentChange, RAGDataSnapshot
from .job_records import JobRecord
from .prompts import ExtractionOptions
from llm.components.prompts.records import PromptDefinition
from .ingestion_records import PreparationCallbacks
from llm.core.model_protocol import RankedDocument
from llm.core.configuration import require_config
from uuid import uuid4

from llm.components.base import validate_name
from llm.services.lifecycle.components import ComponentData
from llm.services.infrastructure.locking import workspace_locked
from llm.services.infrastructure.storage import async_method
from llm.providers.observations import observe_component_models




class RAGData(ComponentData):
    MESSAGE_RAG_CONFIGURATION_CHANGED_DURING_PREPARATION__RETRY_EXPLICITLY = 'RAG configuration changed during preparation; retry explicitly'
    MESSAGE_CORPUS_CHANGED_DURING_RETRIEVAL__RETRY_EXPLICITLY = 'Corpus changed during retrieval; retry explicitly'
    MESSAGE_RAG_SOURCE_EVIDENCE_CHANGED_DURING_RETRIEVAL__RETRY_EXPLICITLY = 'RAG source evidence changed during retrieval; retry explicitly'
    MESSAGE_RAG_CONFIGURATION_CHANGED_DURING_RETRIEVAL__RETRY_EXPLICITLY = 'RAG configuration changed during retrieval; retry explicitly'
    MESSAGE_DOCUMENT_DESCRIPTION_MUST_BE_TEXT = 'Document description must be text'
    MESSAGE_QUERY_MUST_BE_NONEMPTY_TEXT = 'Query must be nonempty text'
    MESSAGE_CONFIGURE_A_RERANKER_FIRST = 'Configure a reranker first'
    MESSAGE_LIMIT_MUST_BE_POSITIVE = 'limit must be positive'
    MESSAGE_SEED_MUST_BE_A_NONEMPTY_ENTITY_NAME = 'Seed must be a nonempty entity name'
    MESSAGE_QUERY_EMBEDDING_MODEL_DIFFERS_FROM_INDEXED_MODEL = 'Query embedding model differs from indexed model'
    MESSAGE_QUERY_EMBEDDING_DIMENSIONS_DIFFER_FROM_INDEX = 'Query embedding dimensions differ from index'
    MESSAGE_RERANKER_MUST_RETURN_RERANKRESULT = 'Reranker must return RerankResult'
    MESSAGE_INVALID_RERANK_INDEXES = 'Invalid rerank indexes'
    MESSAGE_RAG_EXTRACTION_PROMPT_ID_REQUIRES_SELECTING_THE_PROMPTS_COMPONENT = 'RAG extraction.prompt_id requires selecting the prompts component'
    MESSAGE_SELECTED_PROMPTS_COMPONENT_MUST_PROVIDE_PROMPTS_CAPABILITY = 'Selected prompts component must provide prompts capability'

    ABSENT = None
    NO_RETRY = False
    FIRST_DOCUMENT = 0
    COMPONENT_INDEX = 1
    MINIMUM_JOB_LIMIT = 1
    def _current(self):
        project, component = super()._current()
        worker = component.with_config(project)
        prompt_id = worker.extraction_options.get(ExtractionOptions.Field.PROMPT_ID)
        if prompt_id is not RAGData.ABSENT:
            try:
                if PromptDefinition.CAPABILITY not in project.components:
                    raise ValueError(RAGData.MESSAGE_RAG_EXTRACTION_PROMPT_ID_REQUIRES_SELECTING_THE_PROMPTS_COMPONENT)
                prompts = self.registry.get(PromptDefinition.CAPABILITY)
                if PromptDefinition.CAPABILITY not in prompts.capabilities:
                    raise ValueError(RAGData.MESSAGE_SELECTED_PROMPTS_COMPONENT_MUST_PROVIDE_PROMPTS_CAPABILITY)
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
        component._embedding_dimensions = EmbeddingEvidence(snapshot).optional_dimensions
        return component, RAGDataSnapshot.create(snapshot, component._configuration_version,
                                                component.source_version(project), component.search_options)

    @workspace_locked
    def _commit(self, snapshot, document):
        return self._commit_documents(snapshot, [document])[self.FIRST_DOCUMENT]

    @workspace_locked
    def _commit_documents(self, snapshot, prepared):
        project, component = self._current()
        if snapshot.get(RAGDataSnapshot.CONFIGURATION_VERSION) != component._configuration_version:
            from .component import RAGConflictError
            raise RAGConflictError(RAGData.MESSAGE_RAG_CONFIGURATION_CHANGED_DURING_PREPARATION__RETRY_EXPLICITLY)
        documents = dict(snapshot[RAGCorpus.Field.DOCUMENTS])
        documents.update({document[RAGDocument.Field.ID]: document for document in prepared})
        component.publish(project, snapshot, documents)
        return [DocumentView.project(document) for document in prepared]

    def _current_snapshot(self, snapshot):
        """저장 트랜잭션 안에서 수명과 세대를 검사하고 이미 읽은 스냅샷을 재사용한다."""
        from .component import RAGConflictError
        project, component = self._current()
        current = component.source_version(project)
        if RAGCorpus(snapshot).binding != current[:RAGCorpus.IDENTITY_WIDTH]:
            raise RAGConflictError(RAGData.MESSAGE_CORPUS_CHANGED_DURING_RETRIEVAL__RETRY_EXPLICITLY)
        if snapshot[RAGDataSnapshot.SOURCE_VERSION] != current:
            raise RAGConflictError(RAGData.MESSAGE_RAG_SOURCE_EVIDENCE_CHANGED_DURING_RETRIEVAL__RETRY_EXPLICITLY)
        if snapshot.get(RAGDataSnapshot.CONFIGURATION_VERSION) != component._configuration_version:
            raise RAGConflictError(RAGData.MESSAGE_RAG_CONFIGURATION_CHANGED_DURING_RETRIEVAL__RETRY_EXPLICITLY)
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
    async def _prepare(self, identifier, title, content, metadata, old=ABSENT, description=ABSENT):
        if description is not RAGData.ABSENT and not isinstance(description, str):
            raise ValueError(RAGData.MESSAGE_DOCUMENT_DESCRIPTION_MUST_BE_TEXT)
        component, snapshot = await self._async_call(self._snapshot)
        await self._async_call(self.require_model_observation, component.embedding, component.extractor)
        if old is RAGData.ABSENT:
            if identifier in snapshot[RAGCorpus.Field.DOCUMENTS]:
                raise FileExistsError(identifier)
            revision = RAGDocument.INITIAL_REVISION
        else:
            document = snapshot[RAGCorpus.Field.DOCUMENTS].get(identifier)
            if document is RAGData.ABSENT:
                raise FileNotFoundError(identifier)
            component._check_revision(document, old[DocumentChange.EXPECTED_REVISION], old[DocumentChange.EXPECTED_GENERATION])
            title = document[RAGDocument.Field.TITLE] if title is RAGData.ABSENT else title
            content = document[SplitDocument.Field.CONTENT] if content is RAGData.ABSENT else content
            metadata = document[RAGDocument.Field.METADATA] if metadata is RAGData.ABSENT else metadata
            description = document.get(RAGDocument.Field.DESCRIPTION) if description is RAGData.ABSENT else description
            revision = document[RAGDocument.Field.REVISION] + RAGDocument.REVISION_INCREMENT
        # 이 await 동안 UI와 기존 검색은 계속 진행한다. 취소 시 공개 단계에 진입하지 않는다.
        from .component import RAGComponent
        options = PreparationCallbacks.update(snapshot[RAGCorpus.Field.DOCUMENTS].get(identifier)) if type(component).prepare is RAGComponent.prepare else {}
        prepared = await component.prepare(identifier, title, content,
                                           deepcopy({} if metadata is RAGData.ABSENT else metadata), revision,
                                           **options)
        if description is not RAGData.ABSENT:
            prepared[RAGDocument.Field.DESCRIPTION] = description
        if old is not RAGData.ABSENT:
            # prepare를 교체한 구현에도 문서 update의 생성 세대 보존 계약은 동일하다.
            prepared[RAGDocument.Field.GENERATION] = snapshot[RAGCorpus.Field.DOCUMENTS][identifier][RAGDocument.Field.GENERATION]
        return await self._async_call(self._commit, snapshot, prepared)

    @observe_component_models
    async def _retrieve(self, query: str, *, method=ABSENT, expand=ABSENT, limit=ABSENT, rerank=ABSENT):
        """BM25/벡터/하이브리드 검색. 선택 시 RerankModel로 최종 후보를 재정렬한다."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError(RAGData.MESSAGE_QUERY_MUST_BE_NONEMPTY_TEXT)
        component, snapshot = await self._async_call(self._snapshot)
        options = {SearchOptions.Field.METHOD: method, SearchOptions.Field.EXPAND: expand, SearchOptions.Field.LIMIT: limit, SearchOptions.Field.RERANK: rerank}
        options = {key: (component.search_options.get(key, SearchOptions.RERANK_DISABLED) if key == SearchOptions.Field.RERANK else require_config(component.search_options, key, scope=SearchOptions.SCOPE)) if value is RAGData.ABSENT else value for key, value in options.items()}
        method, expand, limit, rerank = (options[key] for key in (SearchOptions.Field.METHOD, SearchOptions.Field.EXPAND, SearchOptions.Field.LIMIT, SearchOptions.Field.RERANK))
        validate_search_options(options)
        vector = RAGData.ABSENT
        if snapshot[RAGCorpus.Field.DOCUMENTS] and method != SearchMethod.BM25:
            await self._async_call(self.require_model_observation, component.embedding)
            if component._identity() != EmbeddingEvidence(snapshot).profile.model:
                raise ValueError(RAGData.MESSAGE_QUERY_EMBEDDING_MODEL_DIFFERS_FROM_INDEXED_MODEL)
            vector = await component.embed_query(query)
            if len(vector) != EmbeddingEvidence(snapshot).profile.dimensions:
                raise ValueError(RAGData.MESSAGE_QUERY_EMBEDDING_DIMENSIONS_DIFFER_FROM_INDEX)
        if rerank and component.reranker is RAGData.ABSENT:
            raise ValueError(RAGData.MESSAGE_CONFIGURE_A_RERANKER_FIRST)
        hits = await self._async_call(self._search, snapshot, query, vector,
                                     {SearchOptions.Field.METHOD: method, SearchOptions.Field.EXPAND: expand, SearchOptions.Field.LIMIT: limit})
        if rerank and hits:
            await self._async_call(self.require_model_observation, component.reranker)
            response = await component.reranker.rerank(query, [hit[RAGChunk.Field.TEXT] for hit in hits])
            from llm.core.model_protocol import RerankResult
            if not isinstance(response, RerankResult):
                raise TypeError(RAGData.MESSAGE_RERANKER_MUST_RETURN_RERANKRESULT)
            rows = response.rows
            order = [row.index for row in rows]
            if len(set(order)) != len(order) or any(type(i) is not int or not RankedDocument.MINIMUM_INDEX <= i < len(hits) for i in order):
                raise ValueError(RAGData.MESSAGE_INVALID_RERANK_INDEXES)
            hits = [RetrievalHit.reranked(hits[row.index], row.score) for row in rows]
            # 모델 호출 중 프로젝트가 삭제/해제되었는지도 다시 확인한다.
            await self._async_call(self._current_snapshot, snapshot)
        return snapshot, hits

    # 공개 API
    @workspace_locked
    def has_reranker(self):
        """현재 Project의 실행 가능한 reranker 유무. Tool 스키마와 동일한 바인딩을 사용한다."""
        return self._current()[self.COMPONENT_INDEX].reranker is not RAGData.ABSENT

    def enqueue_document(self, *, title, content, metadata=ABSENT, identifier=ABSENT, description=ABSENT):
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
        value = self.job(identifier).get(JobRecord.Field.DIAGNOSTIC)
        return Diagnostic.from_dict(value) if value is not RAGData.ABSENT else RAGData.ABSENT

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
    async def arun_job(self, identifier, *, retry: bool = NO_RETRY, expected_revision=ABSENT):
        from .jobs import RAGJobs
        return await RAGJobs(self).run(identifier, retry=retry, expected_revision=expected_revision)

    async def arun_jobs(self, *, limit=ABSENT):
        """현재 영속 대기열을 순차 처리한다. 실패 작업은 남기고 다음 작업으로 진행한다."""
        if limit is not RAGData.ABSENT and (type(limit) is not int or limit < self.MINIMUM_JOB_LIMIT):
            raise ValueError(RAGData.MESSAGE_LIMIT_MUST_BE_POSITIVE)
        results = []
        for job in await self.ajobs():
            if job[JobRecord.Field.STATUS] != RAGJobStatus.QUEUED.value or job[JobRecord.Field.RESUME_ABANDONMENT] is not RAGData.ABSENT:
                continue
            try:
                results.append(await self.arun_job(job[JobRecord.Field.ID]))
            except Exception:
                results.append(await self.ajob(job[JobRecord.Field.ID]))
            if limit is not RAGData.ABSENT and len(results) >= limit:
                break
        return results

    aenqueue_document = async_method(enqueue_document)
    ajobs = async_method(jobs)
    ajob = async_method(job)
    acancel_job = async_method(cancel_job)

    async def aadd_document(self, *, title: str, content: str, metadata=ABSENT, identifier=ABSENT, description=ABSENT) -> dict:
        """원문과 색인을 함께 등록한다. 완료된 문서 dict를 반환하며 백그라운드 job은 만들지 않는다."""
        identifier = uuid4().hex if identifier is RAGData.ABSENT else validate_name(identifier)
        return await self._prepare(identifier, title, content, metadata, description=description)

    @observe_component_models
    async def aadd_documents(self, documents) -> list:
        """Prepare the caller's batch, then publish one generation. Use a Job for durable retry."""
        inputs = RAGDocumentInput.prepare_batch(documents)
        component, snapshot = await self._async_call(self._snapshot)
        for item in inputs:
            if item[RAGDocumentInput.Field.IDENTIFIER] in snapshot[RAGCorpus.Field.DOCUMENTS]:
                raise FileExistsError(item[RAGDocumentInput.Field.IDENTIFIER])
        prepared = []
        for item in inputs:
            await self._async_call(self.require_model_observation, component.embedding, component.extractor)
            document = await component.prepare(item[RAGDocumentInput.Field.IDENTIFIER], item[RAGDocument.Field.TITLE], item[SplitDocument.Field.CONTENT], item[RAGDocument.Field.METADATA], RAGDocument.INITIAL_REVISION)
            if item.get(RAGDocument.Field.DESCRIPTION) is not RAGData.ABSENT:
                document[RAGDocument.Field.DESCRIPTION] = item[RAGDocument.Field.DESCRIPTION]
            component._embedding_dimensions = EmbeddingEvidence(document).profile.dimensions
            prepared.append(document)
        return await self._async_call(self._commit_documents, snapshot, prepared)

    async def aupdate_document(self, identifier: str, *, content=ABSENT, title=ABSENT,
                               metadata=ABSENT, expected_revision=ABSENT, expected_generation=ABSENT, description=ABSENT) -> dict:
        """문서/색인을 교체한다. CAS 사용 시 읽은 generation과 revision을 함께 전달한다."""
        return await self._prepare(validate_name(identifier), title, content, metadata,
                                   DocumentChange.create(expected_revision, expected_generation), description=description)

    @workspace_locked
    def list_catalog(self, *, limit: int, cursor=ABSENT) -> dict:
        """본문·벡터를 읽지 않는 세대 고정 목록. 페이지 크기는 호출자가 명시한다."""
        project, component = self._current()
        return component.list_catalog(project, limit=limit, cursor=cursor)

    alist_catalog = async_method(list_catalog)

    @workspace_locked
    def get_document(self, identifier: str) -> dict:
        _, snapshot = self._snapshot()
        try:
            return DocumentView.project(snapshot[RAGCorpus.Field.DOCUMENTS][validate_name(identifier)])
        except KeyError:
            raise FileNotFoundError(identifier) from RAGData.ABSENT

    @workspace_locked
    def list_documents(self) -> list:
        _, snapshot = self._snapshot()
        return [DocumentView.project(document) for document in snapshot[RAGCorpus.Field.DOCUMENTS].values()]

    @workspace_locked
    def delete_document(self, identifier: str, *, expected_revision=ABSENT, expected_generation=ABSENT) -> None:
        project, component = self._current()
        snapshot = component.snapshot(project)
        documents = dict(snapshot[RAGCorpus.Field.DOCUMENTS])
        document = documents.pop(validate_name(identifier), RAGData.ABSENT)
        if document is RAGData.ABSENT:
            raise FileNotFoundError(identifier)
        component._check_revision(document, expected_revision, expected_generation)
        component.publish(project, snapshot, documents)

    async def asearch_documents(self, query: str, *, method=ABSENT, expand=ABSENT, limit=ABSENT, rerank=ABSENT) -> list:
        """문서 목록만 필요한 코드용 API. 일반 질의에는 asearch를 사용한다."""
        _, hits = await self._retrieve(query, method=method, expand=expand, limit=limit, rerank=rerank)
        return hits

    async def asearch(self, query: str, *, method=ABSENT, expand=ABSENT, limit=ABSENT, rerank=ABSENT,
                      max_hops=ABSENT, relation_limit=ABSENT) -> dict:
        """문서와 해당 문단에 근거한 관계·출처를 한 세대에서 반환한다."""
        validate_search_options({key: value for key, value in
            {SearchOptions.Field.MAX_HOPS: max_hops, SearchOptions.Field.RELATION_LIMIT: relation_limit}.items() if value is not RAGData.ABSENT})
        snapshot, hits = await self._retrieve(query, method=method, expand=expand, limit=limit, rerank=rerank)
        max_hops = require_config(snapshot[RAGDataSnapshot.SEARCH_OPTIONS], SearchOptions.Field.MAX_HOPS, scope=SearchOptions.SCOPE) if max_hops is RAGData.ABSENT else max_hops
        relation_limit = require_config(snapshot[RAGDataSnapshot.SEARCH_OPTIONS], SearchOptions.Field.RELATION_LIMIT, scope=SearchOptions.SCOPE) if relation_limit is RAGData.ABSENT else relation_limit
        validate_search_options({SearchOptions.Field.MAX_HOPS: max_hops, SearchOptions.Field.RELATION_LIMIT: relation_limit})
        return await self._async_call(self._combined, snapshot, query, hits,
                                     {SearchOptions.Field.MAX_HOPS: max_hops, SearchOptions.Field.RELATION_LIMIT: relation_limit})

    @workspace_locked
    def graph_search(self, seed: str, *, max_hops=ABSENT, limit=ABSENT) -> dict:
        """정확한 엔티티 이름을 아는 호출자를 위한 직접 관계 조회."""
        if not isinstance(seed, str) or not seed.strip():
            raise ValueError(RAGData.MESSAGE_SEED_MUST_BE_A_NONEMPTY_ENTITY_NAME)
        project, component = self._current()
        max_hops = require_config(component.search_options, SearchOptions.Field.MAX_HOPS, scope=SearchOptions.SCOPE) if max_hops is RAGData.ABSENT else max_hops
        limit = require_config(component.search_options, SearchOptions.Field.RELATION_LIMIT, scope=SearchOptions.SCOPE) if limit is RAGData.ABSENT else limit
        validate_search_options({SearchOptions.Field.MAX_HOPS: max_hops, SearchOptions.Field.RELATION_LIMIT: limit})
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
