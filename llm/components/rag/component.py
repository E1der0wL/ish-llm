"""문서·임베딩·색인의 세대 관리. 공개 핸들은 잠금 아래에서 이 구현을 호출한다."""

from .prompts import ExtractionOptions
from .configuration import RAGConfiguration
from .preparation import RAGEmbedding, RAGPreparation, PreparationFingerprint

from llm.providers.diagnostic_values import ProviderRetryObservation

from llm.components.tools.registry import ToolRegistry

from llm.core.implementation_parameters import ImplementationParameters

from llm.core.configuration_views import ConfigurationView
from llm.core.configuration import ConfigurationSource

from llm.providers.codes import ProviderErrorCode
from copy import copy, deepcopy
from dataclasses import fields
from hashlib import sha256
import re
import time
import shutil
from uuid import uuid4

from llm.components.base import validate_name
from llm.components.definitions import DefinitionComponent
from llm.services.infrastructure.storage import atomic_json, read_json, remove_named_tree, recover_deletions, revision_token, tree_snapshot, check_revision
from llm.services.infrastructure.logging import log_event
from .data import RAGData
from .identity import RAGIdentity, RAGEvidenceIdentity
from .indexing import build_index, update_index, ChromaIndex
from .search import search, search_schema, SearchOptions
from .splitting import split_markdown
from .chunk_records import RAGChunk, RAGSection, SplitDocument
from .document_records import RAGDocument, RAGDocumentInput, ExtractionManifestEntry
from .corpus_records import RAGCorpus
from .publication_records import CorpusIdentity, CorpusPointer, CorpusControl, CorpusCatalog
from .retrieval_records import RetrievalSource, CatalogCursor, CatalogRequest, CatalogPage
from .graph_records import GraphEntity, GraphRelation, GraphRecord, GraphSearchResult, ExtractionCheckpoint, validate_graph, validate_extraction_checkpoint, validate_stored_graph, enrich_graph, group_evidence, select_graph, GraphValidationError
from .prompts import ExtractionFailurePolicy, extraction_schema
from .graph_indexing import build_graph, update_graph, GraphStore
from .graph_search import graph_search, related_graph
from .files import copy_file
from llm.core.configuration import resolve_config, require_config
from llm.core.models import ProjectConfig
from llm.core.json_data import JsonObject
from llm.core.contracts import Diagnostic, DiagnosticSeverity, ResourceRef
from llm.core.parameters import ParameterLayout
from llm.core.schema import object_schema, metadata_schema
from llm.providers.requests import provider_schema, error_code
from llm.core.model_protocol import EmbeddingResult
from llm.providers.runtime import diagnostic, diagnostic_scope
from .ingestion import VectorCache, prepare_vectors
from .ingestion_records import (BatchCheckpointName, EmbeddingCheckpoint, EmbeddingEvidence,
                                EmbeddingProfile, IngestionObservation)
from .job_storage import JobStorage, JobBatchReceipt
from .job_policy import JobAdmission


class RAGConflictError(RuntimeError):
    """준비 중 문서/프로젝트 상태가 바뀌었다. 자동 재시도 없이 호출자에게 알린다."""


class RAGComponent(DefinitionComponent):
    """Application 메모 CRUD와 문서 색인 API를 분리하고 모델 인자는 선택 구현체에 위임한다."""

    CACHE_NAMESPACE_ATTRIBUTE = "_cache_namespace"
    EMBEDDING_DIMENSIONS_ATTRIBUTE = "_embedding_dimensions"
    IDENTITY_FILE = "identity.json"
    ACTIVE_FILE = "active.json"
    CONTROL_FILE = "corpus.manifest.json"
    CORPUS_FILE = "corpus.json"
    CATALOG_FILE = "catalog.json"
    GENERATIONS_DIRECTORY = "generations"

    name = directory = RAGIdentity.CAPABILITY
    capabilities = (RAGIdentity.CAPABILITY, ToolRegistry.CAPABILITY)
    data_class = RAGData
    # 일반 record CRUD는 Application 메모만 보관한다. 문서 색인은 document API가 소유한다.
    schema = object_schema({"metadata": metadata_schema()})
    parameter_layout = RAGConfiguration.LAYOUT

    def __init__(self, *, embedding=None, embedding_id=None, reranker=None, extractor=None):
        """모델 구현만 주입한다. 분할·동시성·검색 설정은 ProjectConfig에서 받는다."""
        self.embedding, self.reranker, self.extractor = embedding, reranker, extractor
        self.embedding_id = embedding_id
        values = {}
        RAGConfiguration.bind_values(self, values)
        from .search import LexicalCache
        self.search_cache = LexicalCache(0)
        RAGConfiguration.initial_options(self, values)
        self.vector_cache = VectorCache()
        self.query_cache = VectorCache()
        from .cache import SnapshotCache
        self.snapshot_cache = SnapshotCache()

    def _generation_path(self, project, generation):
        self._validate_identity(generation)
        return self._checked(self.root(project) / self.GENERATIONS_DIRECTORY / generation)

    @classmethod
    def _validate_identity(cls, value):
        if not isinstance(value, str) or RAGEvidenceIdentity.PATTERN.fullmatch(value) is None:
            raise ValueError("RAG identity and generation require a stored UUID")

    def initialize(self, project):
        root = self._checked(self.root(project))
        if self.name in project.initialized_components:
            # Reselecting/restoring existing data cannot manufacture missing proof.
            self.snapshot(project)
            JobStorage(project, self).validate_sources()
            super().initialize(project)
            return
        super().initialize(project)
        identity = uuid4().hex
        atomic_json(root / self.IDENTITY_FILE, CorpusIdentity.create(identity))
        atomic_json(root / self.ACTIVE_FILE, CorpusPointer.create(RAGCorpus.ABSENT))
        self._write_control(project, identity, None, None, None)
        JobStorage(project, self).initialize()

    def _checked_tree(self, path):
        self._checked(path)
        for entry in path.rglob("*"):
            self._checked(entry)
        return path

    def _build(self, path, documents):
        build_index(path / ChromaIndex.DIRECTORY, documents, batch_size=self.index_batch_size)
        build_graph(path / GraphStore.FILE_NAME, documents, options=self.graph_options)

    @classmethod
    def validate_input(cls, source):
        return RAGDocumentInput.validate(source)

    @classmethod
    def validate_batch_checkpoint(cls, job, key, receipt, payload):
        name = key.removeprefix(JobStorage.BATCHES + "/").removesuffix(".json")
        batch = BatchCheckpointName.parse(name)
        if batch is None:
            return
        if "document_ids" in job:
            if batch[1] is None or int(batch[1]) >= len(job["document_ids"]):
                raise ValueError("RAG checkpoint document position changed")
        elif batch[1] is not None:
            raise ValueError("Single-document Job cannot have a batch document checkpoint")
        source = payload["documents"][int(batch[1])] if batch[1] is not None else payload
        value = receipt[JobBatchReceipt.Field.VALUE]
        if batch[2] == BatchCheckpointName.EMBEDDING:
            EmbeddingCheckpoint.validate(value, receipt[JobBatchReceipt.Field.SIGNATURE])
            actual = [EmbeddingCheckpoint(value).chunk]
        else:
            validate_extraction_checkpoint(value, receipt[JobBatchReceipt.Field.SIGNATURE])
            actual = value["chunks"]
        chunks = SplitDocument(split_markdown(source["content"], source["identifier"], chunk_size=value["chunk_size"])).chunks
        offset = int(batch[3])
        if revision_token(chunks[offset:offset + len(actual)]) != revision_token(actual):
            raise ValueError("RAG checkpoint differs from its original input chunks")

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

    def with_config(self, project, *, prompt=None):
        return RAGConfiguration.with_config(self, project, prompt=prompt)

    def describe_config(self):
        return RAGConfiguration.describe_config(self)

    def validate_config(self, data):
        return RAGConfiguration.validate_config(self, data)

    def validate_backup(self, project):
        super().validate_backup(project)
        JobStorage(project, self).validate_sources()
        snapshot = self.snapshot(project)
        generation = RAGCorpus(snapshot).generation
        if generation is not None:
            path = self._generation_path(project, generation)
            if not (path / ChromaIndex.DIRECTORY).is_dir() or not (path / GraphStore.FILE_NAME).is_file():
                raise ValueError("RAG backup requires matching vector and graph generation")

    def resolve(self, project, capability):
        if capability == ToolRegistry.CAPABILITY:
            raise ValueError("Search tools require a lifecycle-bound component data factory")
        return super().resolve(project, capability)

    def resolve_runtime(self, project, capability, *, data_factory):
        """프로젝트가 선택한 컴포넌트의 검색 Tool을 별도 설정 없이 제공한다."""
        if capability != ToolRegistry.CAPABILITY:
            return self.resolve(project, capability)
        from .tools import search_tools
        return search_tools(data_factory(self.name))

    def _read_evidence(self, path):
        path = self._checked(path)
        if not path.is_file():
            raise FileNotFoundError(f"RAG evidence requires a regular file: {path.name}")
        return read_json(path)

    def _write_control(self, project, identity, generation, corpus, catalog):
        atomic_json(self.root(project) / self.CONTROL_FILE,
                    CorpusControl.create(project.id, self.name, identity, generation, corpus, catalog))

    def _corpus_control(self, project):
        """Read the current owner proof without parsing the immutable corpus."""
        root = self.root(project)
        identity = self._read_evidence(root / self.IDENTITY_FILE)
        active = self._read_evidence(root / self.ACTIVE_FILE)
        CorpusIdentity.validate(identity)
        CorpusPointer.validate(active)
        control = self._read_evidence(root / self.CONTROL_FILE)
        CorpusControl.validate(control, project.id, self.name, identity, active)
        identity = CorpusIdentity(identity).identifier
        self._validate_identity(identity)
        generation = CorpusPointer(active).generation
        CorpusControl.validate_revisions(control, generation)
        if generation is None:
            generations = self._checked(root / self.GENERATIONS_DIRECTORY)
            if generations.exists() and any(generations.iterdir()):
                raise ValueError("Empty RAG pointer conflicts with stored generations")
        else:
            path = self._generation_path(project, generation)
            if not self._checked(path / self.CORPUS_FILE).is_file():
                raise FileNotFoundError("RAG active generation requires its corpus")
        return identity, generation, control

    def corpus_version(self, project):
        """잠금 안에서 작은 포인터와 연결 증거만 읽는다."""
        identity, generation, _ = self._corpus_control(project)
        return identity, generation

    def source_version(self, project):
        """Bind a read snapshot across model calls without parsing the corpus again."""
        identity, generation, control = self._corpus_control(project)
        version = (identity, generation, CorpusControl(control).corpus_revision, CorpusControl(control).catalog_revision)
        if generation is None:
            return version
        path = self._generation_path(project, generation)
        for name in (self.CORPUS_FILE, self.CATALOG_FILE):
            source = self._checked(path / name)
            if not source.is_file():
                raise FileNotFoundError(f"RAG source requires a regular file: {name}")
            info = source.stat()
            version += ((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns),)
        if not self._checked(path / ChromaIndex.DIRECTORY).is_dir():
            raise FileNotFoundError("RAG generation requires its vector index")
        if not self._checked(path / GraphStore.FILE_NAME).is_file():
            raise FileNotFoundError("RAG generation requires graph.kuzu")
        return version

    @classmethod
    def _catalog_rows(cls, documents):
        return CorpusCatalog.rows(documents)

    def _catalog(self, project, generation, control):
        rows = self._read_evidence(self._generation_path(project, generation) / self.CATALOG_FILE)
        return CorpusCatalog.validate(rows, control)

    def snapshot(self, project):
        identity, generation, control = self._corpus_control(project)
        if generation is None:
            return RAGCorpus.empty_snapshot(identity)
        path = self._generation_path(project, generation)
        source = self._checked(path / self.CORPUS_FILE)
        info = source.stat()  # Deletion/replacement must never be hidden by a cached generation.
        key = (str(source), identity, generation, CorpusControl(control).corpus_revision, info.st_dev, info.st_ino,
               info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        # Missing means inactive, not a tuning default or a materialized configuration value.
        maximum = 0 if self.snapshot_cache_max_bytes is None else self.snapshot_cache_max_bytes
        value = self.snapshot_cache.get(key, maximum)
        cached = value is not None
        if not cached:
            value = self.validate_corpus(read_json(source))
            if revision_token(value) != CorpusControl(control).corpus_revision:
                raise ValueError("RAG corpus integrity mismatch")
        catalog = self._catalog(project, generation, control)
        if revision_token(catalog) != revision_token(self._catalog_rows(RAGCorpus(value).documents)):
            raise ValueError("RAG catalog disagrees with its published corpus")
        if not self._checked(path / ChromaIndex.DIRECTORY).is_dir():
            raise FileNotFoundError("RAG generation requires its vector index")
        if not self._checked(path / GraphStore.FILE_NAME).is_file():
            raise FileNotFoundError("RAG generation requires graph.kuzu")
        if not cached:
            self.snapshot_cache.put(key, value, maximum)
        return RAGCorpus.snapshot(value, identity, generation)

    def list_catalog(self, project, *, limit, cursor=CatalogRequest.ABSENT):
        """현재 세대에 함께 공개된 설명 목록만 읽는다. 원본은 corpus가 소유한다."""
        if type(limit) is not int or limit < CatalogRequest.MINIMUM_LIMIT:
            raise ValueError(CatalogRequest.INVALID_LIMIT)
        identity, generation, control = self._corpus_control(project)
        after = CatalogRequest.FIRST_AFTER
        if cursor is not CatalogRequest.ABSENT:
            CatalogCursor.validate(cursor)
            if CatalogCursor(cursor).binding != (identity, generation):
                raise RAGConflictError(CatalogCursor.CONFLICT)
            after = validate_name(CatalogCursor(cursor).after)
        rows = [] if generation is CatalogRequest.ABSENT else self._catalog(project, generation, control)
        selected = [row for row in rows if RAGDocument(row).identifier > after][:limit + CatalogRequest.LOOKAHEAD]
        more = len(selected) > limit
        selected = selected[:limit]
        return CatalogPage.create(selected, generation,
            CatalogCursor.create(identity, generation, RAGDocument(selected[CatalogRequest.LAST_ROW]).identifier)
            if more else CatalogRequest.ABSENT)

    async def embed(self, texts: list, *, query=False) -> list:
        return await RAGEmbedding.embed(self, texts, query=query)

    async def embed_query(self, query):
        return await RAGEmbedding.embed_query(self, query)

    async def prepare(self, identifier, title, content, metadata, revision, *, progress=None, previous=None, telemetry=None):
        return await RAGPreparation.prepare(self, identifier, title, content, metadata, revision, progress=progress, previous=previous, telemetry=telemetry)

    def publish(self, project, previous, documents, *, progress=None):
        """DB를 닫고 포인터·필수 증거를 같은 workspace transaction으로 확정한다."""
        current = self.snapshot(project)
        if (RAGCorpus(current).identity, RAGCorpus(current).generation) != (RAGCorpus(previous).identity, RAGCorpus(previous).generation):
            raise RAGConflictError("Corpus changed during preparation; reload and retry explicitly")
        if progress:
            progress("validating")
        if not isinstance(documents, dict):
            raise ValueError("RAG corpus documents must be an object")
        profile = None
        if documents:
            first = next(iter(documents.values()))
            if not isinstance(first, dict) or EmbeddingEvidence.Field.PROFILE not in first:
                raise ValueError("RAG document requires its embedding profile")
            profile = first[EmbeddingEvidence.Field.PROFILE]
        corpus = self.validate_corpus(RAGCorpus.create(documents, profile))
        generation = uuid4().hex
        path = self._generation_path(project, generation)
        from llm.services.infrastructure.storage import make_directory
        if path.exists():
            raise FileExistsError(path)
        make_directory(path)
        try:
            if progress:
                progress("indexing")
            if RAGCorpus(previous).generation and type(self)._build is RAGComponent._build and "_build" not in self.__dict__:
                source = self._checked_tree(self._generation_path(project, RAGCorpus(previous).generation))
                shutil.copytree(source / ChromaIndex.DIRECTORY, path / ChromaIndex.DIRECTORY, copy_function=copy_file)
                copy_file(source / GraphStore.FILE_NAME, path / GraphStore.FILE_NAME)
                update_index(path / ChromaIndex.DIRECTORY, RAGCorpus(previous).documents, documents, batch_size=self.index_batch_size)
                update_graph(path / GraphStore.FILE_NAME, RAGCorpus(previous).documents, documents, options=self.graph_options)
            else:
                self._build(path, documents)
            if progress:
                progress("writing_metadata")
            catalog = self._catalog_rows(documents)
            atomic_json(path / self.CATALOG_FILE, catalog)
            atomic_json(path / self.CORPUS_FILE, corpus)
        except BaseException:
            remove_named_tree(path.parent, path, generation)
            raise
        # 포인터 교체 뒤 디렉토리 fsync가 실패할 수도 있다. 공개를 시도한 세대는
        # 오류가 나더라도 여기서 지우지 않아 active가 없는 DB를 가리키지 않게 한다.
        if progress:
            progress("publishing")
        atomic_json(self.root(project) / self.ACTIVE_FILE, CorpusPointer.create(generation))
        self._write_control(project, RAGCorpus(previous).identity, generation, corpus, catalog)
        # Finish only previously committed deletion intents; publication never selects old generations.
        try:
            self.recover_compaction(project)
        except (OSError, ValueError):
            log_event(project.paths.logs, "rag.cleanup_pending", entity_id=project.id)
        log_event(project.paths.logs, "rag.published", entity_id=project.id, count=len(documents))

    def recover_compaction(self, project):
        """Complete existing deletion intents without selecting new targets."""
        active = self.snapshot(project)["generation"]
        root = self._checked(self.root(project) / self.GENERATIONS_DIRECTORY)
        return recover_deletions(root, protected=(active,))

    def compaction_plan(self, project):
        """Inspect eligible generations; Application alone selects which ones to discard."""
        current = self.snapshot(project)
        root = self._checked(self.root(project) / self.GENERATIONS_DIRECTORY)
        journal = self._checked(root / ".deletions")
        if journal.exists() and any(journal.iterdir()):
            raise ValueError("Pending RAG generation deletion must be recovered first")
        candidates, protected = [], []
        for path in sorted(root.iterdir()) if root.exists() else ():
            if path.name == journal.name:
                continue
            generation = path.name
            self._generation_path(project, generation)
            if generation != RAGCorpus(current).generation:
                if {entry.name for entry in path.iterdir()} != {self.CORPUS_FILE, self.CATALOG_FILE, ChromaIndex.DIRECTORY, GraphStore.FILE_NAME}:
                    raise ValueError("Retired RAG generation has incomplete or unknown entries")
                corpus = self.validate_corpus(self._read_evidence(path / self.CORPUS_FILE))
                catalog = self._read_evidence(path / self.CATALOG_FILE)
                if revision_token(catalog) != revision_token(self._catalog_rows(RAGCorpus(corpus).documents)):
                    raise ValueError("Retired RAG catalog disagrees with its source corpus")
                if not self._checked(path / ChromaIndex.DIRECTORY).is_dir() or not self._checked(path / GraphStore.FILE_NAME).is_file():
                    raise ValueError("Retired RAG generation requires its stored indexes")
            files = tree_snapshot(path)
            row = {"generation": generation, "bytes": sum(item["bytes"] for item in files.values()),
                   "revision": revision_token(files)}
            (protected if generation == RAGCorpus(current).generation else candidates).append(row)
        result = {"identity": RAGCorpus(current).identity, "active_generation": RAGCorpus(current).generation,
                  "candidates": candidates, "protected": protected}
        return {**result, "version": revision_token(result)}

    def compact(self, project, generations, *, expected_version):
        if not isinstance(generations, (list, tuple)):
            raise ValueError("RAG compaction requires an explicit sequence of generations")
        for generation in generations:
            self._validate_identity(generation)
        if len(generations) != len(set(generations)):
            raise ValueError("RAG compaction targets must be unique")
        if not isinstance(expected_version, str) or not expected_version:
            raise ValueError("RAG compaction requires a current plan version")
        plan = self.compaction_plan(project)
        current = dict(plan)
        current.pop("version")
        check_revision(current, expected_version)
        eligible = {row["generation"] for row in plan["candidates"]}
        if any(generation not in eligible for generation in generations):
            raise ValueError("RAG compaction cannot delete protected or unknown generations")
        root = self._checked(self.root(project) / self.GENERATIONS_DIRECTORY)
        for generation in generations:
            remove_named_tree(root, self._generation_path(project, generation), generation)

    def search(self, project, snapshot, query, vector, **options):
        if RAGCorpus(snapshot).generation is None:
            return []
        path = self._checked_tree(self._generation_path(project, RAGCorpus(snapshot).generation))
        return search(path, RAGCorpus(snapshot).documents, query, vector, lexical_cache=self.search_cache,
                      cache_chars=self.search_cache_chars, candidate_count=require_config(self.search_options, SearchOptions.Field.CANDIDATE_COUNT, scope=SearchOptions.SCOPE),
                      rrf_constant=require_config(self.search_options, SearchOptions.Field.RRF_CONSTANT, scope=SearchOptions.SCOPE), **options)

    def graph_search(self, project, *, seed, max_hops, limit):
        snapshot = self.snapshot(project)
        incomplete = [identifier for identifier, doc in RAGCorpus(snapshot).documents.items() if doc.get(RAGDocument.Field.GRAPH_COMPLETE) is False]
        status = {RAGDocument.Field.GRAPH_COMPLETE: not incomplete, "graph_incomplete_documents": incomplete}
        path = (self._generation_path(project, RAGCorpus(snapshot).generation) / GraphStore.FILE_NAME
                if RAGCorpus(snapshot).generation else None)
        if path is None:
            return {**GraphSearchResult.seeded(seed, [], []), **status}
        return {**graph_search(path, seed, max_hops=max_hops, limit=limit, options=self.graph_options,
                              ranking=self.search_options.get(SearchOptions.Field.RELATION_RANKING)), **status}

    def combined_result(self, project, snapshot, query, hits, *, max_hops, relation_limit):
        """동일한 불변 세대에서 문서 검색 결과와 근거 관계를 묶는다."""
        path = (self._generation_path(project, RAGCorpus(snapshot).generation) / GraphStore.FILE_NAME
                if RAGCorpus(snapshot).generation else None)
        graph = GraphRecord.create([], [])
        if path is not None and hits:
            graph = related_graph(path, [hit["id"] for hit in hits], max_hops=max_hops, limit=relation_limit,
                                  options=self.graph_options, ranking=self.search_options.get(SearchOptions.Field.RELATION_RANKING))
        sources = {}
        for hit in hits:
            sources[RAGChunk(hit).identifier] = RetrievalSource.from_hit(hit)
        documents = RAGCorpus(snapshot).documents
        chunk_indexes = {}
        for relation in graph[GraphRecord.Field.RELATIONS]:
            if relation[GraphRelation.Field.SOURCE_ID] in sources:
                continue
            identifier = relation["document_id"]
            doc = documents[identifier]
            # 같은 문서를 근거로 하는 관계마다 전체 문단을 다시 순회하지 않는다.
            if identifier not in chunk_indexes:
                chunk_indexes[identifier] = {RAGChunk(c).identifier: c for c in SplitDocument(doc).chunks}
            chunk = chunk_indexes[identifier][relation[GraphRelation.Field.SOURCE_ID]]
            sources[RAGChunk(chunk).identifier] = RetrievalSource.from_document(chunk, doc)
        incomplete = [identifier for identifier, doc in documents.items() if doc.get(RAGDocument.Field.GRAPH_COMPLETE) is False]
        return {"query": query, "documents": hits, **graph, "sources": list(sources.values()),
                RAGDocument.Field.GRAPH_COMPLETE: not incomplete, "graph_incomplete_documents": incomplete}

    def clone(self, source, destination):
        JobStorage(source, self).validate_sources()
        super().clone(source, destination)
        snapshot = self.snapshot(source)
        if RAGCorpus(snapshot).generation is not None:
            # 불변 원문/벡터/관계를 복제해 DB를 재구축한다. 인증/모델 호출은 필요 없다.
            self.with_config(destination).publish(destination, self.snapshot(destination), RAGCorpus(snapshot).documents)
