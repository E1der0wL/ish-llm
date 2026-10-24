"""문서·임베딩·색인의 세대 관리. 공개 핸들은 잠금 아래에서 이 구현을 호출한다."""

from .prompts import ExtractionOptions

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
from .identity import RAGIdentity
from .indexing import build_index, update_index, ChromaIndex
from .search import search, search_schema, SearchOptions
from .splitting import split_markdown
from .chunk_records import RAGChunk, RAGSection, RAGDocument, SplitDocument
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
from .job_storage import JobStorage


class RAGConflictError(RuntimeError):
    """준비 중 문서/프로젝트 상태가 바뀌었다. 자동 재시도 없이 호출자에게 알린다."""


class RAGComponent(DefinitionComponent):
    """Application 메모 CRUD와 문서 색인 API를 분리하고 모델 인자는 선택 구현체에 위임한다."""

    CACHE_NAMESPACE_ATTRIBUTE = "_cache_namespace"
    EMBEDDING_DIMENSIONS_ATTRIBUTE = "_embedding_dimensions"
    IDENTITY_FILE = "identity.json"
    ACTIVE_FILE = "active.json"
    CONTROL_FILE = "corpus.manifest.json"
    SCHEMA_VERSION = 1
    CONTROL_FIELDS = frozenset(("schema_version", "project_id", "component", "identity_revision",
                                "active_revision", "corpus_revision", "catalog_revision"))
    CORPUS_FILE = "corpus.json"
    CATALOG_FILE = "catalog.json"
    CATALOG_FIELDS = frozenset(("id", "title", "generation", "revision"))
    GENERATIONS_DIRECTORY = "generations"
    IDENTITY_PATTERN = re.compile(r"[a-f0-9]{32}")
    GRAPH_SCHEMA_VERSION = 1
    INPUT_FIELDS = frozenset(("identifier", "title", "content", "metadata"))
    DOCUMENT_FIELDS = frozenset(("id", "title", "content", "metadata", "revision", "generation",
        "sections", "chunks", "sha256", EmbeddingEvidence.Field.VECTORS, EmbeddingEvidence.Field.FINGERPRINT, EmbeddingEvidence.Field.INGESTION, EmbeddingEvidence.Field.PROFILE,
        "graph_complete", "graph_diagnostics", GraphRecord.Field.GRAPH, "extraction_manifest",
        "preparation_configuration", "preparation_fingerprint"))

    name = directory = RAGIdentity.CAPABILITY
    capabilities = (RAGIdentity.CAPABILITY, ToolRegistry.CAPABILITY)
    data_class = RAGData
    # 일반 record CRUD는 Application 메모만 보관한다. 문서 색인은 document API가 소유한다.
    schema = object_schema({"metadata": metadata_schema()})
    POLICY_FIELDS = ("embedding_concurrency", "provider", "ingestion")
    parameter_layout = ParameterLayout(
        config=("chunk_size", "embedding_cache_max_bytes", "query_cache_max_bytes", "snapshot_cache_max_bytes", "extraction_batch_size", "search_cache_chars",
                "index_batch_size", SearchOptions.CONFIG_KEY, "graph", "document_kwargs", "query_kwargs",
                "embedding_params", "extraction_params", "rerank_params"),
        policy=POLICY_FIELDS,
        paths={**{"extraction." + key: "config.extraction." + key
                  for key in (ExtractionOptions.Field.JSON_MODE, ExtractionOptions.Field.PROMPT_ID, ExtractionOptions.Field.RELATION_TYPES)},
               **{"extraction." + key: "policy.extraction." + key
                  for key in (ExtractionOptions.Field.REPAIR_ATTEMPTS, ExtractionOptions.Field.FAILURE_POLICY)}})

    def __init__(self, *, embedding=None, embedding_id=None, reranker=None, extractor=None):
        """모델 구현만 주입한다. 분할·동시성·검색 설정은 ProjectConfig에서 받는다."""
        self.embedding, self.reranker, self.extractor = embedding, reranker, extractor
        self.embedding_id = embedding_id
        values = {}
        for name in ("chunk_size", "embedding_concurrency", "embedding_cache_max_bytes", "query_cache_max_bytes", "snapshot_cache_max_bytes", "extraction_batch_size", "search_cache_chars",
                     "document_kwargs", "query_kwargs", "index_batch_size"):
            setattr(self, name, deepcopy(values.get(name, {} if name.endswith("kwargs") else None)))
        from .search import LexicalCache
        self.search_cache = LexicalCache(0)
        self.search_options, self.graph_options = values.get(SearchOptions.CONFIG_KEY, {}), values.get("graph", {})
        self.extraction_options, self.extraction_prompt = values.get("extraction", {}), {"messages": []}
        self.provider_options = values.get("provider", {})
        self.vector_cache = VectorCache()
        self.query_cache = VectorCache()
        from .cache import SnapshotCache
        self.snapshot_cache = SnapshotCache()

    def _generation_path(self, project, generation):
        self._validate_identity(generation)
        return self._checked(self.root(project) / self.GENERATIONS_DIRECTORY / generation)

    @classmethod
    def _validate_identity(cls, value):
        if not isinstance(value, str) or cls.IDENTITY_PATTERN.fullmatch(value) is None:
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
        atomic_json(root / self.IDENTITY_FILE, {"id": identity})
        atomic_json(root / self.ACTIVE_FILE, {"generation": None})
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
        """Require a complete original document input without creation defaults."""
        if (not isinstance(source, dict) or not cls.INPUT_FIELDS <= source.keys()
                or source.keys() - cls.INPUT_FIELDS - {"description"}):
            raise ValueError("RAG document input fields are incomplete or unknown")
        JsonObject.validate(source)
        validate_name(source["identifier"])
        if (any(not isinstance(source[key], str) or not source[key].strip() for key in ("title", "content"))
                or not isinstance(source["metadata"], dict)
                or (source.get("description") is not None and not isinstance(source["description"], str))):
            raise ValueError("Invalid RAG document input")
        return source

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
        value = receipt["value"]
        if batch[2] == BatchCheckpointName.EMBEDDING:
            EmbeddingCheckpoint.validate(value, receipt["signature"])
            actual = [EmbeddingCheckpoint(value).chunk]
        else:
            validate_extraction_checkpoint(value, receipt["signature"])
            actual = value["chunks"]
        chunks = SplitDocument(split_markdown(source["content"], source["identifier"], chunk_size=value["chunk_size"])).chunks
        offset = int(batch[3])
        if revision_token(chunks[offset:offset + len(actual)]) != revision_token(actual):
            raise ValueError("RAG checkpoint differs from its original input chunks")

    @classmethod
    def validate_document(cls, document):
        """Validate a stored RAG document without filling fields or consulting live settings."""
        if not isinstance(document, dict):
            raise ValueError("Stored RAG document must be an object")
        identifier = validate_name(document.get("id"))
        cls._validate_versions({identifier: document})
        graph = document.get(GraphRecord.Field.GRAPH)
        if not isinstance(graph, dict) or not GraphRecord.FIELDS <= graph.keys():
            raise ValueError("RAG documents require graph entities and relations")
        if (not cls.DOCUMENT_FIELDS <= document.keys()
                or document.keys() - cls.DOCUMENT_FIELDS - {"description"}):
            raise ValueError("Prepared RAG document fields are incomplete or unknown")
        JsonObject.validate(document)
        if (not isinstance(document["title"], str) or not document["title"].strip()
                or not isinstance(document["content"], str) or not document["content"].strip()
                or not isinstance(document["metadata"], dict)
                or not isinstance(EmbeddingEvidence(document).observations, dict)
                or ("description" in document and not isinstance(document["description"], str))):
            raise ValueError("Invalid prepared RAG document source or observations")
        if document["sha256"] != sha256(document["content"].encode("utf-8")).hexdigest():
            raise ValueError("Prepared RAG content digest changed")
        for key in (EmbeddingEvidence.Field.FINGERPRINT, "preparation_fingerprint", "preparation_configuration"):
            value = document[key]
            if key == "preparation_configuration" and value is None:
                continue  # Standalone preparation has no bound Project configuration.
            if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None:
                raise ValueError("Invalid prepared RAG configuration evidence")
        sections, chunks = SplitDocument(document).sections, SplitDocument(document).chunks
        if not isinstance(sections, dict) or not sections or not isinstance(chunks, list) or not chunks:
            raise ValueError("Prepared RAG document requires sections and chunks")
        if sections.keys() != {RAGSection.identifier_for(identifier, position) for position in range(RAGSection.FIRST_ORDINAL, len(sections) + RAGSection.FIRST_ORDINAL)}:
            raise ValueError("Invalid prepared RAG section identities")
        for key, section in sections.items():
            RAGSection.validate_source(section, key, SplitDocument(document).content)
        for position, chunk in enumerate(chunks, RAGChunk.FIRST_ORDINAL):
            RAGChunk.validate_source(chunk, document_id=identifier, ordinal=position, sections=sections)
        profile = document[EmbeddingEvidence.Field.PROFILE]
        EmbeddingProfile.validate(profile)
        from llm.core.embeddings import validate_vectors
        vectors = validate_vectors(document[EmbeddingEvidence.Field.VECTORS], dimensions=EmbeddingProfile(profile).dimensions)
        if len(vectors) != len(chunks):
            raise ValueError("Prepared RAG chunk/vector count mismatch")
        validate_stored_graph(document[GraphRecord.Field.GRAPH], chunks, identifier)
        if type(document["graph_complete"]) is not bool or not isinstance(document["graph_diagnostics"], list):
            raise ValueError("Invalid prepared RAG graph observations")
        for value in document["graph_diagnostics"]:
            if not isinstance(value, dict) or value.keys() != {field.name for field in fields(Diagnostic)}:
                raise ValueError("Incomplete prepared RAG graph diagnostic")
            source = value["source"]
            if source is not None and (not isinstance(source, dict)
                    or source.keys() != {field.name for field in fields(ResourceRef)}):
                raise ValueError("Incomplete prepared RAG graph diagnostic source")
            Diagnostic.from_dict(value)
        manifest = document["extraction_manifest"]
        if not isinstance(manifest, list):
            raise ValueError("Invalid prepared RAG extraction manifest")
        grouped, covered = group_evidence(document[GraphRecord.Field.GRAPH]), set()
        for entry in manifest:
            if (not isinstance(entry, dict) or entry.keys() != {"signature", "entity_ids", "offset", "count", "digest"}
                    or type(entry["offset"]) is not int or type(entry["count"]) is not int
                    or entry["offset"] < 0 or entry["count"] < 1
                    or entry["offset"] + entry["count"] > len(chunks)
                    or not isinstance(entry["entity_ids"], list)
                    or any(not isinstance(item, str) for item in entry["entity_ids"])
                    or len(set(entry["entity_ids"])) != len(entry["entity_ids"])
                    or any(not isinstance(entry[key], str) or re.fullmatch(r"[a-f0-9]{64}", entry[key]) is None
                           for key in ("signature", "digest"))):
                raise ValueError("Invalid prepared RAG extraction batch")
            indices = set(range(entry["offset"], entry["offset"] + entry["count"]))
            if covered & indices:
                raise ValueError("Overlapping prepared RAG extraction batches")
            covered.update(indices)
            graph = select_graph(grouped, entry["entity_ids"], chunks[entry["offset"]:entry["offset"] + entry["count"]])
            if revision_token(graph) != entry["digest"]:
                raise GraphValidationError("Stored extraction batch integrity mismatch")
        if document["graph_complete"] and covered != set(range(len(chunks))):
            raise ValueError("Prepared RAG complete graph has missing extraction batches")
        return document

    @classmethod
    def _validate_versions(cls, documents):
        for identifier, document in documents.items():
            if (document.get("id") != identifier or type(document.get("revision")) is not int
                    or document["revision"] < 1 or not isinstance(document.get("generation"), str)
                    or cls.IDENTITY_PATTERN.fullmatch(document["generation"]) is None):
                raise ValueError("RAG documents require a stored generation and positive revision; re-register sources without them")

    @classmethod
    def validate_corpus(cls, value):
        """Published and prepared documents use the same owning field contract."""
        if (not isinstance(value, dict) or type(value.get("graph_schema_version")) is not int
                or value["graph_schema_version"] != cls.GRAPH_SCHEMA_VERSION):
            raise ValueError("Unsupported RAG graph schema; re-register source documents in a new RAG Project")
        if value.keys() != {"graph_schema_version", "documents", EmbeddingEvidence.Field.PROFILE} or not isinstance(value["documents"], dict):
            raise ValueError("Invalid RAG corpus fields")
        documents = value["documents"]
        for identifier, document in documents.items():
            cls.validate_document(document)
            if document["id"] != identifier:
                raise ValueError("RAG corpus document identity changed")
            if document[EmbeddingEvidence.Field.PROFILE] != value[EmbeddingEvidence.Field.PROFILE]:
                raise ValueError("RAG corpus and document embedding profiles disagree")
        if not documents and value[EmbeddingEvidence.Field.PROFILE] is not None:
            raise ValueError("Empty RAG corpus requires an explicit null profile")
        return value

    @classmethod
    def _check_revision(cls, document, expected_revision, expected_generation):
        """명시적인 CAS는 문서 생성 세대까지 비교한다. 코퍼스 publish 세대와는 별개다."""
        if expected_revision is None and expected_generation is None:
            return  # 두 값 모두 생략한 기존 unconditional document API 계약.
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("Document CAS requires a positive expected_revision")
        if not isinstance(expected_generation, str) or cls.IDENTITY_PATTERN.fullmatch(expected_generation) is None:
            raise ValueError("Document CAS requires expected_generation from the document")
        if (document["generation"], document["revision"]) != (expected_generation, expected_revision):
            raise RAGConflictError("Document generation or revision changed")

    def _identity(self):
        if self.embedding is None:
            raise ValueError("Configure RAGComponent(embedding=EmbeddingModel(...)) first")
        identity = getattr(self.embedding, "identity", None)
        identifier = self.embedding_id or (identity(**self.document_kwargs) if identity else None)
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("Custom embedding clients require embedding_id")
        return identifier

    def _preparation_fingerprint(self):
        """Only preparation inputs; cache/search tuning does not change document semantics."""
        return revision_token({"chunk_size": self.chunk_size, "model": self._identity(),
            "embedding": type(self.embedding).__module__ + "." + type(self.embedding).__qualname__,
            "embedding_params": getattr(self.embedding, "params", {}),
            "document_kwargs": self.document_kwargs, "query_kwargs": self.query_kwargs,
            "extractor": type(self.extractor).__module__ + "." + type(self.extractor).__qualname__,
            "extraction_params": getattr(self.extractor, "params", {}),
            "extraction": self.extraction_options, "prompt": self.extraction_prompt,
            "extraction_batch_size": self.extraction_batch_size})

    # 공개 API: 직접 호출 시 workspace 소유권은 호출자가 보장해야 한다.
    def resolve_config(self, project):
        if getattr(self, "_configuration_source", None) is not None:
            return self._configuration_source.resolve_config(project)
        client_values = {}
        runtime = []
        for name, client in (("embedding_params", self.embedding), ("extraction_params", self.extractor), ("rerank_params", self.reranker)):
            if client is not None:
                params = getattr(client, "params", {})
                try:
                    self.serialize(params)
                    client_values[name] = params
                except (ValueError, TypeError):
                    runtime.append(name)
        if getattr(self.extractor, "extraction", None):
            client_values["extraction"] = deepcopy(self.extractor.extraction)
        layers = [(ConfigurationSource.CLIENT, self.parameter_layout.pack(client_values)), *self.get_config_layers(project)]
        view = resolve_config(layers, schema=self.describe_config())
        from .embedding import EmbeddingModel
        selected = EmbeddingModel() if self.embedding is None else self.embedding
        view["enforced"] = {"config": {"embedding_params": deepcopy(getattr(selected, "enforced_configuration", {}))}}
        view["model_providers"] = {name: resolve_config([
            (ConfigurationSource.CLIENT, getattr(getattr(self, name), "provider_options", {})),
            (ConfigurationSource.PROJECT, ImplementationParameters.read(ConfigurationView.read(view).values).policy.get("provider", {}))], schema=provider_schema())
            for name in ("embedding", "extractor", "reranker")}
        view["runtime"] = runtime
        return view

    def with_config(self, project, *, prompt=None):
        """저장 설정을 작업별 사본으로 바인딩한다. 등록된 객체와 SDK 함수를 변경하지 않는다."""
        from .embedding import EmbeddingModel
        from .extraction import TripleExtractor
        from .rerank import RerankModel
        view = self.resolve_config(project)
        values = self.parameter_layout.unpack(ConfigurationView.read(view).values)
        worker = copy(self)
        worker._configuration_source = self
        for name in ("chunk_size", "embedding_concurrency", "embedding_cache_max_bytes", "query_cache_max_bytes", "snapshot_cache_max_bytes", "extraction_batch_size", "search_cache_chars", "document_kwargs", "query_kwargs", "index_batch_size"):
            setattr(worker, name, deepcopy(values.get(name, {} if name.endswith("kwargs") else None)))
        worker.search_options, worker.graph_options = values.get(SearchOptions.CONFIG_KEY, {}), values.get("graph", {})
        worker.extraction_options = deepcopy(values.get("extraction", {}))
        worker.provider_options = values.get("provider", {})
        worker._cache_namespace = project.id
        worker._extraction_error = (ValueError("RAG prompt_id requires the selected prompts component through its Project handle")
            if worker.extraction_options.get(ExtractionOptions.Field.PROMPT_ID) is not None and prompt is None else None)
        worker.extraction_prompt = deepcopy({"messages": []} if prompt is None else prompt)
        for name, key, factory in (("embedding", "embedding_params", EmbeddingModel),
                                   ("extractor", "extraction_params", TripleExtractor), ("reranker", "rerank_params", RerankModel)):
            client, params = getattr(self, name), values.get(key, {})
            if client is None and params:
                client = factory(**params)
            elif client is not None and params:
                configure = getattr(client, "with_config", None)
                if configure is not None:
                    client = configure(params)
                elif self._options(project).get(key):
                    raise ValueError(f"Custom {name} requires with_config(params) to apply project parameters")
            if client is not None and callable(getattr(client, "with_provider", None)):
                client = client.with_provider(ConfigurationView.read(view["model_providers"][name]).values)
            setattr(worker, name, client)
        bind_extraction = getattr(worker.extractor, "with_extraction", None)
        if bind_extraction is not None:
            worker.extraction_options = ProjectConfig.merge(getattr(worker.extractor, "extraction", {}), worker.extraction_options)
            if prompt is None and getattr(worker.extractor, "prompt", None) is not None:
                worker.extraction_prompt = deepcopy(worker.extractor.prompt)
            worker.extractor = bind_extraction(worker.extraction_options, worker.extraction_prompt)
        elif worker.extractor is not None and worker.extraction_options.keys() - {ExtractionOptions.Field.FAILURE_POLICY}:
            raise ValueError("Custom extractor requires with_extraction(options, prompt) to apply extraction policies")
        worker._configuration_version = revision_token({"configuration": values,
            "model_providers": {k: ConfigurationView.read(v).values for k, v in view["model_providers"].items()}, "prompt": worker.extraction_prompt,
                                                       "prompt_resolved": worker._extraction_error is None})
        return worker

    def describe_config(self):
        from llm.core.schema import object_schema, open_schema, field
        from .search import LexicalCache
        from .embedding import EmbeddingModel
        from .extraction import TripleExtractor
        from .rerank import RerankModel
        def child_schema(client, factory):
            selected = factory() if client is None else client
            describe = getattr(selected, "describe_config", None)
            return describe() if describe else open_schema(type(selected).__qualname__ + ".with_config", category="implementation")
        embedding_schema = child_schema(self.embedding, EmbeddingModel)
        return self.parameter_layout.schema(object_schema({
            "chunk_size": field("integer", minimum=1),
            "embedding_concurrency": field("integer", minimum=1),
            "embedding_cache_max_bytes": field("integer", minimum=0),
            "query_cache_max_bytes": field("integer", "Query vector cache capacity; absent or zero disables caching", minimum=0),
            "snapshot_cache_max_bytes": field("integer", "Corpus projection cache capacity; absent or zero disables caching", minimum=0),
            "extraction_batch_size": field("integer", minimum=1),
            "search_cache_chars": LexicalCache.capacity_schema(),
            "index_batch_size": field("integer", minimum=1), SearchOptions.CONFIG_KEY: search_schema(),
            "graph": object_schema({"buffer_pool_size": field("integer", minimum=1),
                                    "max_num_threads": field("integer", minimum=1)}),
            "document_kwargs": deepcopy(embedding_schema), "query_kwargs": deepcopy(embedding_schema),
            "embedding_params": embedding_schema, "extraction_params": child_schema(self.extractor, TripleExtractor),
            "extraction": extraction_schema(),
            "provider": provider_schema(),
            "rerank_params": child_schema(self.reranker, RerankModel),
            "ingestion": object_schema({"max_active": field("integer", "동시 색인 작업 수", minimum=1)})},
            **{"x-runtime-configuration": ["embedding", "extractor", "reranker", "embedding_id"]}))

    def validate_config(self, data):
        if any(key in data for key in ("embedding_batch_size", "embedding_batching")):
            raise ValueError("Embedding batch configuration were removed; use embedding_concurrency and embedding_cache_max_bytes")
        from jsonschema import Draft202012Validator
        error = next(Draft202012Validator(self.describe_config()).iter_errors(data), None)
        if error:
            raise ValueError("Invalid RAG configuration: " + error.message)
        data = self.parameter_layout.unpack(data)
        for value in (data.get("ingestion", {}).get("max_active"),):
            if value is not None and type(value) is not int:
                raise ValueError("ingestion.max_active requires an integer")
        for name in ("chunk_size", "embedding_concurrency", "embedding_cache_max_bytes", "query_cache_max_bytes", "snapshot_cache_max_bytes", "extraction_batch_size", "search_cache_chars", "index_batch_size"):
            if name in data and type(data[name]) is not int:
                raise ValueError(f"{name} requires an integer")
        from .embedding import EmbeddingModel
        from .extraction import TripleExtractor
        from .rerank import RerankModel
        for client, factory, key in ((self.embedding, EmbeddingModel, "embedding_params"),
                                    (self.extractor, TripleExtractor, "extraction_params"),
                                    (self.reranker, RerankModel, "rerank_params")):
            selected = factory() if client is None else client
            validate = getattr(selected, "validate_config", None)
            if validate is not None and key in data:
                validate(deepcopy(data[key]))

    def validate_backup(self, project):
        super().validate_backup(project)
        JobStorage(project, self).validate_sources()
        snapshot = self.snapshot(project)
        generation = snapshot["generation"]
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
        atomic_json(self.root(project) / self.CONTROL_FILE, {
            "schema_version": self.SCHEMA_VERSION, "project_id": project.id, "component": self.name,
            "identity_revision": revision_token({"id": identity}),
            "active_revision": revision_token({"generation": generation}),
            "corpus_revision": None if corpus is None else revision_token(corpus),
            "catalog_revision": None if catalog is None else revision_token(catalog)})

    def _corpus_control(self, project):
        """Read the current owner proof without parsing the immutable corpus."""
        root = self.root(project)
        identity = self._read_evidence(root / self.IDENTITY_FILE)
        active = self._read_evidence(root / self.ACTIVE_FILE)
        if not isinstance(identity, dict) or identity.keys() != {"id"}:
            raise ValueError("RAG identity record is incomplete or changed")
        if not isinstance(active, dict) or active.keys() != {"generation"}:
            raise ValueError("RAG active pointer is incomplete or changed")
        control = self._read_evidence(root / self.CONTROL_FILE)
        if not isinstance(control, dict) or control.keys() != self.CONTROL_FIELDS:
            raise ValueError("RAG corpus manifest is incomplete or changed")
        if type(control["schema_version"]) is not int or control["schema_version"] != self.SCHEMA_VERSION:
            raise ValueError("Unsupported RAG corpus manifest version")
        if control["project_id"] != project.id or control["component"] != self.name:
            raise ValueError("RAG corpus manifest ownership mismatch")
        if (control["identity_revision"] != revision_token(identity)
                or control["active_revision"] != revision_token(active)):
            raise ValueError("RAG identity or active pointer integrity mismatch")
        identity = identity["id"]
        self._validate_identity(identity)
        generation = active["generation"]
        for name in ("corpus_revision", "catalog_revision"):
            value = control[name]
            if generation is None:
                if value is not None:
                    raise ValueError("Empty RAG storage cannot declare published evidence")
            elif not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None:
                raise ValueError("RAG publication requires its corpus and catalog revisions")
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
        version = (identity, generation, control["corpus_revision"], control["catalog_revision"])
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
        return [{key: doc[key] for key in (*sorted(cls.CATALOG_FIELDS), "description") if key in doc}
                for _, doc in sorted(documents.items())]

    def _catalog(self, project, generation, control):
        rows = self._read_evidence(self._generation_path(project, generation) / self.CATALOG_FILE)
        if not isinstance(rows, list):
            raise ValueError("RAG catalog must be a list")
        identifiers = []
        for row in rows:
            if (not isinstance(row, dict) or not self.CATALOG_FIELDS <= row.keys()
                    or row.keys() - self.CATALOG_FIELDS - {"description"}):
                raise ValueError("RAG catalog requires complete current document fields")
            identifier = validate_name(row["id"])
            self._validate_versions({identifier: row})
            if not isinstance(row["title"], str) or not row["title"].strip():
                raise ValueError("RAG catalog title must be non-empty text")
            if "description" in row and not isinstance(row["description"], str):
                raise ValueError("RAG catalog description must be text")
            identifiers.append(identifier)
        if identifiers != sorted(set(identifiers)):
            raise ValueError("RAG catalog identifiers must be unique and sorted")
        if revision_token(rows) != control["catalog_revision"]:
            raise ValueError("RAG catalog integrity mismatch")
        return rows

    def snapshot(self, project):
        identity, generation, control = self._corpus_control(project)
        if generation is None:
            return {"identity": identity, "generation": None, "documents": {}, EmbeddingEvidence.Field.PROFILE: None}
        path = self._generation_path(project, generation)
        source = self._checked(path / self.CORPUS_FILE)
        info = source.stat()  # Deletion/replacement must never be hidden by a cached generation.
        key = (str(source), identity, generation, control["corpus_revision"], info.st_dev, info.st_ino,
               info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        # Missing means inactive, not a tuning default or a materialized configuration value.
        maximum = 0 if self.snapshot_cache_max_bytes is None else self.snapshot_cache_max_bytes
        value = self.snapshot_cache.get(key, maximum)
        cached = value is not None
        if not cached:
            value = self.validate_corpus(read_json(source))
            if revision_token(value) != control["corpus_revision"]:
                raise ValueError("RAG corpus integrity mismatch")
        catalog = self._catalog(project, generation, control)
        if revision_token(catalog) != revision_token(self._catalog_rows(value["documents"])):
            raise ValueError("RAG catalog disagrees with its published corpus")
        if not self._checked(path / ChromaIndex.DIRECTORY).is_dir():
            raise FileNotFoundError("RAG generation requires its vector index")
        if not self._checked(path / GraphStore.FILE_NAME).is_file():
            raise FileNotFoundError("RAG generation requires graph.kuzu")
        if not cached:
            self.snapshot_cache.put(key, value, maximum)
        return {**value, "identity": identity, "generation": generation}

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
        if not isinstance(texts, list) or len(texts) != 1:
            raise ValueError("RAG embedding requires one chunk per request")
        self._identity()
        identity = getattr(self.embedding, "identity", None)
        if query and identity is not None and identity(**self.query_kwargs) != identity(**self.document_kwargs):
            raise ValueError("Query embedding model differs from indexed model")
        kwargs = self.query_kwargs if query else self.document_kwargs
        response = await self.embedding.embed(texts, **kwargs)
        extract = getattr(self.embedding, "extract_vector", None)
        if not isinstance(response, EmbeddingResult):
            raise TypeError("Embedding model must return EmbeddingResult")
        vector = extract(response, **kwargs) if extract else response.vector
        from llm.core.embeddings import validate_vectors
        return validate_vectors([vector], dimensions=getattr(self, self.EMBEDDING_DIMENSIONS_ATTRIBUTE, None))

    async def embed_query(self, query):
        """Cache only validated query vectors, never retrieval results or SDK responses."""
        maximum = self.query_cache_max_bytes
        if not maximum:
            self.query_cache.get("", 0)
            return (await self.embed([query], query=True))[0]
        identity = getattr(self.embedding, "identity", None)
        if identity is not None and identity(**self.query_kwargs) != identity(**self.document_kwargs):
            raise ValueError("Query embedding model differs from indexed model")
        client_identity = getattr(self.embedding, "cache_identity", None)
        fingerprint = revision_token({"model": self._identity(),
            "params": getattr(self.embedding, "params", {}), "query_kwargs": self.query_kwargs,
            "document_kwargs": self.document_kwargs, "client": type(self.embedding).__qualname__,
            "implementation": str(client_identity() if client_identity else id(self.embedding)),
            "project": getattr(self, self.CACHE_NAMESPACE_ATTRIBUTE, None)})
        key = VectorCache.key(fingerprint, query)
        vector = self.query_cache.get(key, maximum)
        cached = vector is not None
        if vector is None:
            vector = (await self.embed([query], query=True))[0]
        validate = getattr(self.embedding, "validate_vectors", None)
        if validate is not None:
            vector = validate([vector], **self.query_kwargs)[0]
        from llm.core.embeddings import validate_vectors
        vector = validate_vectors([vector], dimensions=getattr(self, self.EMBEDDING_DIMENSIONS_ATTRIBUTE, None))[0]
        self.query_cache.put(key, vector, maximum)
        diagnostic("rag_query_embedding", cached=cached)
        return vector

    async def prepare(self, identifier, title, content, metadata, revision, *, progress=None, previous=None, telemetry=None):
        if telemetry:
            await telemetry({"phase": "validating_input"})
        policy = require_config(self.extraction_options, ExtractionOptions.Field.FAILURE_POLICY, scope="rag.extraction")
        if self.chunk_size is None:
            raise ValueError("Missing required setting: rag.chunk_size")
        if policy != ExtractionFailurePolicy.DISABLED.value and self.extraction_batch_size is None:
            raise ValueError("Missing required setting: rag.extraction_batch_size")
        if policy != ExtractionFailurePolicy.DISABLED.value and getattr(self, "_extraction_error", None) is not None:
            raise self._extraction_error
        if policy != ExtractionFailurePolicy.DISABLED.value and self.extractor is None:
            raise ValueError("Configure RAGComponent(extractor=TripleExtractor(...)) before writing documents")
        validate_name(identifier)
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Document title must be nonempty text")
        metadata = self.deserialize(self.serialize(metadata))
        fingerprint = self._preparation_fingerprint()
        if (previous is not None and previous["content"] == content
                and previous.get("preparation_fingerprint") == fingerprint
                and (previous["graph_complete"] or policy == ExtractionFailurePolicy.DISABLED.value)):
            from llm.core.embeddings import validate_vectors
            vectors = previous[EmbeddingEvidence.Field.VECTORS]
            validate = getattr(self.embedding, "validate_vectors", None)
            if validate is not None:
                vectors = validate(vectors, **self.document_kwargs)
            dimensions = getattr(self, self.EMBEDDING_DIMENSIONS_ATTRIBUTE, None)
            vectors = validate_vectors(vectors, dimensions=EmbeddingEvidence(previous).profile.dimensions if dimensions is None else dimensions)
            if len(vectors) != len(SplitDocument(previous).chunks):
                raise ValueError("Stored chunk/vector count mismatch")
            validate_graph(previous[GraphRecord.Field.GRAPH], SplitDocument(previous).chunks,
                           relation_types=self.extraction_options.get(ExtractionOptions.Field.RELATION_TYPES, ()))
            document = deepcopy(previous)
            document.update(title=title, metadata=metadata, revision=revision,
                            preparation_configuration=getattr(self, "_configuration_version", None))
            document[EmbeddingEvidence.Field.INGESTION] = IngestionObservation.metadata_only(len(vectors))
            return document
        if telemetry:
            await telemetry({"phase": "splitting"})
        document = split_markdown(content, identifier, chunk_size=self.chunk_size)
        document.update(id=identifier, title=title, metadata=metadata, revision=revision,
                        generation=previous["generation"] if previous is not None else uuid4().hex)
        await prepare_vectors(self, document, previous=previous, progress=progress, telemetry=telemetry)
        document[EmbeddingEvidence.Field.PROFILE] = EmbeddingProfile.create(self._identity(), len(EmbeddingEvidence(document).vectors[0]))
        document.update(graph_complete=policy != ExtractionFailurePolicy.DISABLED.value, graph_diagnostics=[])
        IngestionObservation.start_extraction(EmbeddingEvidence(document).observations)
        if policy == ExtractionFailurePolicy.DISABLED.value:
            document["graph_diagnostics"].append(diagnostic("graph_extraction_disabled").to_dict())
        entities, relations = {}, []
        manifest, reused_sources = [], set()
        previous_batches = {entry["signature"]: entry for entry in previous.get("extraction_manifest", [])} if previous else {}
        previous_graph = group_evidence(previous[GraphRecord.Field.GRAPH]) if previous_batches else None
        extraction_total = ((len(SplitDocument(document).chunks) + self.extraction_batch_size - 1) // self.extraction_batch_size
                            if policy != ExtractionFailurePolicy.DISABLED.value else 0)
        extraction_done = 0
        async def extraction_progress():
            if telemetry:
                await telemetry(IngestionObservation.extraction_progress(EmbeddingEvidence(document).observations, extraction_done, extraction_total))
        if policy != ExtractionFailurePolicy.DISABLED.value:
            await extraction_progress()
        def observe_extraction(event):
            if event.code == ProviderRetryObservation.RETRY:
                EmbeddingEvidence(document).observations[IngestionObservation.Field.RETRIES] += 1
        for offset in (range(0, len(SplitDocument(document).chunks), self.extraction_batch_size) if policy != ExtractionFailurePolicy.DISABLED.value else ()):
            batch = SplitDocument(document).chunks[offset:offset + self.extraction_batch_size]
            # Keep the existing Job checkpoint binding unchanged. Cross-update reuse has
            # its own implementation identity; it is not a replacement checkpoint format.
            signature = revision_token({"chunks": batch, "extractor": type(self.extractor).__qualname__,
                                        "params": getattr(self.extractor, "params", {}),
                                        "extraction": self.extraction_options, "prompt": self.extraction_prompt})
            reuse_signature = revision_token({"request": signature,
                "implementation": type(self.extractor).__module__ + "." + type(self.extractor).__qualname__})
            key = BatchCheckpointName.graph(offset)
            saved = await progress(key, signature) if progress else None
            graph = validate_extraction_checkpoint(saved, signature) if saved is not None else None
            if graph is None and reuse_signature in previous_batches:
                entry = previous_batches[reuse_signature]
                graph = select_graph(previous_graph, entry["entity_ids"], batch,
                                     relation_types=self.extraction_options.get(ExtractionOptions.Field.RELATION_TYPES, ()))
                if revision_token(graph) != entry["digest"]:
                    raise GraphValidationError("Stored extraction batch integrity mismatch")
                reused_sources.update(RAGChunk(chunk).identifier for chunk in batch)
                EmbeddingEvidence(document).observations[IngestionObservation.Field.EXTRACTION_REUSED] += 1
                if progress:
                    await progress(key, signature, {ExtractionCheckpoint.Field.CHUNKS: batch, ExtractionCheckpoint.Field.CHUNK_SIZE: self.chunk_size, ExtractionCheckpoint.Field.GRAPH: graph})
            if graph is None:
                started = time.monotonic()
                try:
                    with diagnostic_scope(observe_extraction):
                        graph = validate_graph(await self.extractor.extract(batch), batch,
                                               relation_types=self.extraction_options.get(ExtractionOptions.Field.RELATION_TYPES, ()))
                except Exception as error:
                    code = error_code(error)
                    # 저장/한도/프로그래밍 오류는 best_effort로 숨기지 않는다.
                    if policy != ExtractionFailurePolicy.BEST_EFFORT.value or not (isinstance(error, GraphValidationError) or code in (
                            ProviderErrorCode.TIMEOUT.value, ProviderErrorCode.CONNECTION.value, ProviderErrorCode.UNAVAILABLE.value,
                            ProviderErrorCode.RATE_LIMIT.value, ProviderErrorCode.EMPTY_RESPONSE.value, ProviderErrorCode.INVALID_RESPONSE.value)):
                        raise
                    document["graph_complete"] = False
                    document["graph_diagnostics"].append(diagnostic("graph_incomplete", severity=DiagnosticSeverity.WARNING.value,
                        batch_offset=offset, cause=code if code != ProviderErrorCode.FAILED.value else GraphValidationError.code).to_dict())
                    continue
                finally:
                    extraction_done += 1
                    EmbeddingEvidence(document).observations[IngestionObservation.Field.EXTRACTION_BATCHES] += 1
                    EmbeddingEvidence(document).observations[IngestionObservation.Field.EXTRACTION_SECONDS] += time.monotonic() - started
                    diagnostic(IngestionObservation.EXTRACTION_EVENT, **EmbeddingEvidence(document).observations)
                    await extraction_progress()
                if progress:
                    await progress(key, signature, {ExtractionCheckpoint.Field.CHUNKS: batch, ExtractionCheckpoint.Field.CHUNK_SIZE: self.chunk_size, ExtractionCheckpoint.Field.GRAPH: graph})
            else:
                graph = validate_graph(graph, batch,
                                       relation_types=self.extraction_options.get(ExtractionOptions.Field.RELATION_TYPES, ()))
                extraction_done += 1
                await extraction_progress()
            entities.update({e[GraphEntity.Field.ID]: e for e in graph[GraphRecord.Field.ENTITIES]})
            relations.extend(graph[GraphRecord.Field.RELATIONS])
            manifest.append({"signature": reuse_signature, "entity_ids": [e[GraphEntity.Field.ID] for e in graph[GraphRecord.Field.ENTITIES]],
                             "offset": offset, "count": len(batch)})
        document[GraphRecord.Field.GRAPH] = enrich_graph(validate_graph(
            GraphRecord.create(list(entities.values()), relations), SplitDocument(document).chunks,
            relation_types=self.extraction_options.get(ExtractionOptions.Field.RELATION_TYPES, ())), identifier)
        # Manifest stores identities/digests, not a second copy of the graph.
        grouped = group_evidence(document[GraphRecord.Field.GRAPH])
        for entry in manifest:
            batch = SplitDocument(document).chunks[entry["offset"]:entry["offset"] + entry["count"]]
            entry["digest"] = revision_token(select_graph(grouped, entry["entity_ids"], batch,
                relation_types=self.extraction_options.get(ExtractionOptions.Field.RELATION_TYPES, ())))
        if reused_sources:
            def edge_key(edge):
                return GraphRelation.evidence_key(edge)
            stamps = {edge_key(edge): edge[GraphRelation.Field.EXTRACTED_AT] for edge in previous[GraphRecord.Field.GRAPH][GraphRecord.Field.RELATIONS]
                      if edge[GraphRelation.Field.SOURCE_ID] in reused_sources}
            for edge in document[GraphRecord.Field.GRAPH][GraphRecord.Field.RELATIONS]:
                if edge_key(edge) in stamps:
                    edge[GraphRelation.Field.EXTRACTED_AT] = stamps[edge_key(edge)]
        document["extraction_manifest"] = manifest
        document["preparation_configuration"] = getattr(self, "_configuration_version", None)
        document["preparation_fingerprint"] = fingerprint
        return document

    def publish(self, project, previous, documents, *, progress=None):
        """DB를 닫고 포인터·필수 증거를 같은 workspace transaction으로 확정한다."""
        current = self.snapshot(project)
        if (current["identity"], current["generation"]) != (previous["identity"], previous["generation"]):
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
        corpus = self.validate_corpus({"graph_schema_version": self.GRAPH_SCHEMA_VERSION,
            "documents": documents, EmbeddingEvidence.Field.PROFILE: profile})
        generation = uuid4().hex
        path = self._generation_path(project, generation)
        from llm.services.infrastructure.storage import make_directory
        if path.exists():
            raise FileExistsError(path)
        make_directory(path)
        try:
            if progress:
                progress("indexing")
            if previous["generation"] and type(self)._build is RAGComponent._build and "_build" not in self.__dict__:
                source = self._checked_tree(self._generation_path(project, previous["generation"]))
                shutil.copytree(source / ChromaIndex.DIRECTORY, path / ChromaIndex.DIRECTORY, copy_function=copy_file)
                copy_file(source / GraphStore.FILE_NAME, path / GraphStore.FILE_NAME)
                update_index(path / ChromaIndex.DIRECTORY, previous["documents"], documents, batch_size=self.index_batch_size)
                update_graph(path / GraphStore.FILE_NAME, previous["documents"], documents, options=self.graph_options)
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
        atomic_json(self.root(project) / self.ACTIVE_FILE, {"generation": generation})
        self._write_control(project, previous["identity"], generation, corpus, catalog)
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
            if generation != current["generation"]:
                if {entry.name for entry in path.iterdir()} != {self.CORPUS_FILE, self.CATALOG_FILE, ChromaIndex.DIRECTORY, GraphStore.FILE_NAME}:
                    raise ValueError("Retired RAG generation has incomplete or unknown entries")
                corpus = self.validate_corpus(self._read_evidence(path / self.CORPUS_FILE))
                catalog = self._read_evidence(path / self.CATALOG_FILE)
                if revision_token(catalog) != revision_token(self._catalog_rows(corpus["documents"])):
                    raise ValueError("Retired RAG catalog disagrees with its source corpus")
                if not self._checked(path / ChromaIndex.DIRECTORY).is_dir() or not self._checked(path / GraphStore.FILE_NAME).is_file():
                    raise ValueError("Retired RAG generation requires its stored indexes")
            files = tree_snapshot(path)
            row = {"generation": generation, "bytes": sum(item["bytes"] for item in files.values()),
                   "revision": revision_token(files)}
            (protected if generation == current["generation"] else candidates).append(row)
        result = {"identity": current["identity"], "active_generation": current["generation"],
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
        if snapshot["generation"] is None:
            return []
        path = self._checked_tree(self._generation_path(project, snapshot["generation"]))
        return search(path, snapshot["documents"], query, vector, lexical_cache=self.search_cache,
                      cache_chars=self.search_cache_chars, candidate_count=require_config(self.search_options, SearchOptions.Field.CANDIDATE_COUNT, scope=SearchOptions.SCOPE),
                      rrf_constant=require_config(self.search_options, SearchOptions.Field.RRF_CONSTANT, scope=SearchOptions.SCOPE), **options)

    def graph_search(self, project, *, seed, max_hops, limit):
        snapshot = self.snapshot(project)
        incomplete = [identifier for identifier, doc in snapshot["documents"].items() if doc.get("graph_complete") is False]
        status = {"graph_complete": not incomplete, "graph_incomplete_documents": incomplete}
        path = (self._generation_path(project, snapshot["generation"]) / GraphStore.FILE_NAME
                if snapshot["generation"] else None)
        if path is None:
            return {**GraphSearchResult.seeded(seed, [], []), **status}
        return {**graph_search(path, seed, max_hops=max_hops, limit=limit, options=self.graph_options,
                              ranking=self.search_options.get(SearchOptions.Field.RELATION_RANKING)), **status}

    def combined_result(self, project, snapshot, query, hits, *, max_hops, relation_limit):
        """동일한 불변 세대에서 문서 검색 결과와 근거 관계를 묶는다."""
        path = (self._generation_path(project, snapshot["generation"]) / GraphStore.FILE_NAME
                if snapshot["generation"] else None)
        graph = GraphRecord.create([], [])
        if path is not None and hits:
            graph = related_graph(path, [hit["id"] for hit in hits], max_hops=max_hops, limit=relation_limit,
                                  options=self.graph_options, ranking=self.search_options.get(SearchOptions.Field.RELATION_RANKING))
        sources = {}
        for hit in hits:
            sources[RAGChunk(hit).identifier] = RetrievalSource.from_hit(hit)
        documents = snapshot["documents"]
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
        incomplete = [identifier for identifier, doc in documents.items() if doc.get("graph_complete") is False]
        return {"query": query, "documents": hits, **graph, "sources": list(sources.values()),
                "graph_complete": not incomplete, "graph_incomplete_documents": incomplete}

    def clone(self, source, destination):
        JobStorage(source, self).validate_sources()
        super().clone(source, destination)
        snapshot = self.snapshot(source)
        if snapshot["generation"] is not None:
            # 불변 원문/벡터/관계를 복제해 DB를 재구축한다. 인증/모델 호출은 필요 없다.
            self.with_config(destination).publish(destination, self.snapshot(destination), snapshot["documents"])
