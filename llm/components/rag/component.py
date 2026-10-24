"""문서·임베딩·색인의 세대 관리. 공개 핸들은 잠금 아래에서 이 구현을 호출한다."""

from .configuration import RAGConfiguration
from .preparation import RAGEmbedding, RAGPreparation, PreparationFingerprint


from llm.components.tools.registry import ToolRegistry


from llm.components.definitions import DefinitionComponent
from .errors import RAGConflictError
from .corpus_storage import CorpusStorage
from .checkpoint_source import RAGCheckpointSource
from .data import RAGData
from .identity import RAGIdentity, RAGEvidenceIdentity
from .search import search, SearchOptions
from .chunk_records import RAGChunk, SplitDocument
from .document_records import RAGDocument, RAGDocumentInput
from .corpus_records import RAGCorpus
from .publication_records import CorpusCatalog
from .retrieval_records import RetrievalSource, CatalogRequest, GraphReadiness, CombinedResult
from .graph_records import GraphRelation, GraphRecord, GraphSearchResult
from .graph_indexing import GraphStore
from .graph_search import graph_search, related_graph
from llm.core.configuration import require_config
from llm.core.schema import object_schema, metadata_schema
from .ingestion import VectorCache
from .job_storage import JobStorage


class RAGComponent(DefinitionComponent):
    """Application 메모 CRUD와 문서 색인 API를 분리하고 모델 인자는 선택 구현체에 위임한다."""

    ABSENT = None
    LIFECYCLE_BOUND_TOOLS = "Search tools require a lifecycle-bound component data factory"
    CACHE_NAMESPACE_ATTRIBUTE = "_cache_namespace"
    EMBEDDING_DIMENSIONS_ATTRIBUTE = "_embedding_dimensions"

    name = directory = RAGIdentity.CAPABILITY
    capabilities = (RAGIdentity.CAPABILITY, ToolRegistry.CAPABILITY)
    data_class = RAGData
    # 일반 record CRUD는 Application 메모만 보관한다. 문서 색인은 document API가 소유한다.
    schema = object_schema({"metadata": metadata_schema()})
    parameter_layout = RAGConfiguration.LAYOUT

    def __init__(self, *, embedding=ABSENT, embedding_id=ABSENT, reranker=ABSENT, extractor=ABSENT):
        """모델 구현만 주입한다. 분할·동시성·검색 설정은 ProjectConfig에서 받는다."""
        self.embedding, self.reranker, self.extractor = embedding, reranker, extractor
        self.embedding_id = embedding_id
        values = {}
        RAGConfiguration.bind_values(self, values)
        from .search import LexicalCache

        self.search_cache = LexicalCache(LexicalCache.EMPTY_USAGE)
        RAGConfiguration.initial_options(self, values)
        self.vector_cache = VectorCache()
        self.query_cache = VectorCache()
        from .cache import SnapshotCache

        self.snapshot_cache = SnapshotCache()

    def _generation_path(self, project, generation):
        return CorpusStorage._generation_path(self, project, generation)

    @classmethod
    def _validate_identity(cls, value):
        return RAGEvidenceIdentity.validate(value)

    def initialize(self, project):
        root = self._checked(self.root(project))
        if self.name in project.initialized_components:
            # Reselecting/restoring existing data cannot manufacture missing proof.
            self.snapshot(project)
            JobStorage(project, self).validate_sources()
            super().initialize(project)
            return
        super().initialize(project)
        CorpusStorage.create(self, project, root)
        JobStorage(project, self).initialize()

    def _checked_tree(self, path):
        return CorpusStorage._checked_tree(self, path)

    def _build(self, path, documents):
        return CorpusStorage._build(self, path, documents)

    @classmethod
    def validate_input(cls, source):
        return RAGDocumentInput.validate(source)

    @classmethod
    def validate_batch_checkpoint(cls, job, key, receipt, payload):
        return RAGCheckpointSource.validate(job, key, receipt, payload)

    @classmethod
    def validate_document(cls, document):
        return RAGDocument.validate(document)

    @classmethod
    def validate_corpus(cls, value):
        """The selected Component callback validates every canonical corpus document."""
        return RAGCorpus.validate(value, cls.validate_document)

    @classmethod
    def _check_revision(cls, document, expected_revision, expected_generation):
        return RAGDocument.check_revision(document, expected_revision, expected_generation, RAGConflictError)

    def _identity(self):
        return RAGEmbedding.identity(self)

    def _preparation_fingerprint(self):
        return PreparationFingerprint.create(self)

    # 공개 API: 직접 호출 시 workspace 소유권은 호출자가 보장해야 한다.
    def resolve_config(self, project):
        return RAGConfiguration.resolve_config(self, project)

    def with_config(self, project, *, prompt=ABSENT):
        return RAGConfiguration.with_config(self, project, prompt=prompt)

    def describe_config(self):
        return RAGConfiguration.describe_config(self)

    def validate_config(self, data):
        return RAGConfiguration.validate_config(self, data)

    def validate_backup(self, project):
        super().validate_backup(project)
        JobStorage(project, self).validate_sources()
        snapshot = self.snapshot(project)
        CorpusStorage.validate_backup(self, project, snapshot)

    def resolve(self, project, capability):
        if capability == ToolRegistry.CAPABILITY:
            raise ValueError(self.LIFECYCLE_BOUND_TOOLS)
        return super().resolve(project, capability)

    def resolve_runtime(self, project, capability, *, data_factory):
        """프로젝트가 선택한 컴포넌트의 검색 Tool을 별도 설정 없이 제공한다."""
        if capability != ToolRegistry.CAPABILITY:
            return self.resolve(project, capability)
        from .tools import search_tools

        return search_tools(data_factory(self.name))

    def _read_evidence(self, path):
        return CorpusStorage._read_evidence(self, path)

    def _write_control(self, project, identity, generation, corpus, catalog):
        return CorpusStorage._write_control(self, project, identity, generation, corpus, catalog)

    def _corpus_control(self, project):
        return CorpusStorage._corpus_control(self, project)

    def corpus_version(self, project):
        return CorpusStorage.corpus_version(self, project)

    def source_version(self, project):
        return CorpusStorage.source_version(self, project)

    @classmethod
    def _catalog_rows(cls, documents):
        return CorpusCatalog.rows(documents)

    def _catalog(self, project, generation, control):
        return CorpusStorage._catalog(self, project, generation, control)

    def snapshot(self, project):
        return CorpusStorage.snapshot(self, project)

    def list_catalog(self, project, *, limit, cursor=CatalogRequest.ABSENT):
        return CorpusStorage.list_catalog(self, project, limit=limit, cursor=cursor)

    async def embed(self, texts: list, *, query=RAGEmbedding.DOCUMENT) -> list:
        return await RAGEmbedding.embed(self, texts, query=query)

    async def embed_query(self, query):
        return await RAGEmbedding.embed_query(self, query)

    async def prepare(
        self,
        identifier,
        title,
        content,
        metadata,
        revision,
        *,
        progress=ABSENT,
        previous=ABSENT,
        telemetry=ABSENT,
    ):
        return await RAGPreparation.prepare(
            self,
            identifier,
            title,
            content,
            metadata,
            revision,
            progress=progress,
            previous=previous,
            telemetry=telemetry,
        )

    def publish(self, project, previous, documents, *, progress=ABSENT):
        return CorpusStorage.publish(self, project, previous, documents, progress=progress)

    def recover_compaction(self, project):
        return CorpusStorage.recover_compaction(self, project)

    def compaction_plan(self, project):
        return CorpusStorage.compaction_plan(self, project)

    def compact(self, project, generations, *, expected_version):
        return CorpusStorage.compact(self, project, generations, expected_version=expected_version)

    def search(self, project, snapshot, query, vector, **options):
        if RAGCorpus(snapshot).generation is RAGComponent.ABSENT:
            return []
        path = self._checked_tree(self._generation_path(project, RAGCorpus(snapshot).generation))
        return search(
            path,
            RAGCorpus(snapshot).documents,
            query,
            vector,
            lexical_cache=self.search_cache,
            cache_chars=self.search_cache_chars,
            candidate_count=require_config(
                self.search_options, SearchOptions.Field.CANDIDATE_COUNT, scope=SearchOptions.SCOPE
            ),
            rrf_constant=require_config(
                self.search_options, SearchOptions.Field.RRF_CONSTANT, scope=SearchOptions.SCOPE
            ),
            **options,
        )

    def graph_search(self, project, *, seed, max_hops, limit):
        snapshot = self.snapshot(project)
        incomplete = GraphReadiness.incomplete_documents(RAGCorpus(snapshot).documents)
        status = GraphReadiness.create(incomplete)
        path = (
            self._generation_path(project, RAGCorpus(snapshot).generation) / GraphStore.FILE_NAME
            if RAGCorpus(snapshot).generation
            else RAGComponent.ABSENT
        )
        if path is RAGComponent.ABSENT:
            return GraphReadiness.attach(GraphSearchResult.seeded(seed, [], []), status)
        return GraphReadiness.attach(
            graph_search(
                path,
                seed,
                max_hops=max_hops,
                limit=limit,
                options=self.graph_options,
                ranking=self.search_options.get(SearchOptions.Field.RELATION_RANKING),
            ),
            status,
        )

    def combined_result(self, project, snapshot, query, hits, *, max_hops, relation_limit):
        """동일한 불변 세대에서 문서 검색 결과와 근거 관계를 묶는다."""
        path = (
            self._generation_path(project, RAGCorpus(snapshot).generation) / GraphStore.FILE_NAME
            if RAGCorpus(snapshot).generation
            else RAGComponent.ABSENT
        )
        graph = GraphRecord.create([], [])
        if path is not RAGComponent.ABSENT and hits:
            graph = related_graph(
                path,
                [RAGChunk(hit).identifier for hit in hits],
                max_hops=max_hops,
                limit=relation_limit,
                options=self.graph_options,
                ranking=self.search_options.get(SearchOptions.Field.RELATION_RANKING),
            )
        sources = {}
        for hit in hits:
            sources[RAGChunk(hit).identifier] = RetrievalSource.from_hit(hit)
        documents = RAGCorpus(snapshot).documents
        chunk_indexes = {}
        for relation in GraphRecord(graph).relations:
            if GraphRelation(relation).source_id in sources:
                continue
            identifier = GraphRelation(relation).document_id
            doc = documents[identifier]
            # 같은 문서를 근거로 하는 관계마다 전체 문단을 다시 순회하지 않는다.
            if identifier not in chunk_indexes:
                chunk_indexes[identifier] = {RAGChunk(c).identifier: c for c in SplitDocument(doc).chunks}
            chunk = chunk_indexes[identifier][GraphRelation(relation).source_id]
            sources[RAGChunk(chunk).identifier] = RetrievalSource.from_document(chunk, doc)
        incomplete = GraphReadiness.incomplete_documents(documents)
        return CombinedResult.create(query, hits, graph, sources, incomplete)

    def clone(self, source, destination):
        JobStorage(source, self).validate_sources()
        super().clone(source, destination)
        snapshot = self.snapshot(source)
        if RAGCorpus(snapshot).generation is not RAGComponent.ABSENT:
            # 불변 원문/벡터/관계를 복제해 DB를 재구축한다. 인증/모델 호출은 필요 없다.
            self.with_config(destination).publish(
                destination, self.snapshot(destination), RAGCorpus(snapshot).documents
            )
