"""문서·임베딩·색인의 세대 관리. 공개 핸들은 잠금 아래에서 이 구현을 호출한다."""

from copy import copy, deepcopy
import re
import time
import shutil
from uuid import uuid4

from llm.components.base import validate_name
from llm.components.definitions import DefinitionComponent
from llm.services.infrastructure.storage import atomic_json, read_json, remove_named_tree, recover_deletions, revision_token
from llm.services.infrastructure.logging import log_event
from .data import RAGData
from .indexing import build_index, update_index
from .search import search, search_schema
from .splitting import split_markdown
from .extraction import validate_graph, enrich_graph, group_evidence, select_graph, GraphValidationError
from .prompts import extraction_schema
from .graph_indexing import build_graph, update_graph
from .graph_search import graph_search, related_graph
from .files import copy_file
from llm.core.configuration import resolve_config, require_config
from llm.core.models import ProjectConfig
from llm.core.parameters import ParameterLayout
from llm.core.schema import object_schema, metadata_schema
from llm.providers.requests import provider_schema, error_code
from llm.providers.embeddings import extract_single_embedding
from llm.providers.runtime import diagnostic, diagnostic_scope
from .ingestion import VectorCache, prepare_vectors, vector_key


class RAGConflictError(RuntimeError):
    """준비 중 문서/프로젝트 상태가 바뀌었다. 자동 재시도 없이 호출자에게 알린다."""


class RAGComponent(DefinitionComponent):
    """Application 메모 CRUD와 문서 색인 API를 분리하고 모델 인자는 선택 구현체에 위임한다."""

    IDENTITY_FILE = "identity.json"
    ACTIVE_FILE = "active.json"
    GENERATIONS_DIRECTORY = "generations"
    IDENTITY_PATTERN = re.compile(r"[a-f0-9]{32}")
    GRAPH_SCHEMA_VERSION = 1

    name = "rag"
    directory = "rag"
    capabilities = ("rag", "tools")
    data_class = RAGData
    # 일반 record CRUD는 Application 메모만 보관한다. 문서 색인은 document API가 소유한다.
    schema = object_schema({"metadata": metadata_schema()})
    parameter_layout = ParameterLayout(
        config=("chunk_size", "embedding_cache_max_bytes", "query_cache_max_bytes", "snapshot_cache_max_bytes", "extraction_batch_size", "search_cache_chars",
                "index_batch_size", "search", "graph", "document_kwargs", "query_kwargs",
                "embedding_params", "extraction_params", "rerank_params"),
        policy=("embedding_concurrency", "provider", "ingestion", "retention"),
        paths={**{"extraction." + key: "config.extraction." + key
                  for key in ("json_mode", "prompt_id", "relation_types")},
               **{"extraction." + key: "policy.extraction." + key
                  for key in ("repair_attempts", "failure_policy")}})

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
        self.search_options, self.graph_options = values.get("search", {}), values.get("graph", {})
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
        if root.exists():
            # Reselecting/restoring existing data cannot manufacture missing proof.
            self.snapshot(project)
            super().initialize(project)
            return
        super().initialize(project)
        atomic_json(root / self.IDENTITY_FILE, {"id": uuid4().hex})
        atomic_json(root / self.ACTIVE_FILE, {"generation": None})

    def _checked_tree(self, path):
        self._checked(path)
        for entry in path.rglob("*"):
            self._checked(entry)
        return path

    def _build(self, path, documents):
        build_index(path / "chroma", documents, batch_size=self.index_batch_size)
        build_graph(path / "graph.kuzu", documents, options=self.graph_options)

    @classmethod
    def _validate_versions(cls, documents):
        for identifier, document in documents.items():
            if (document.get("id") != identifier or type(document.get("revision")) is not int
                    or document["revision"] < 1 or not isinstance(document.get("generation"), str)
                    or cls.IDENTITY_PATTERN.fullmatch(document["generation"]) is None):
                raise ValueError("RAG documents require a stored generation and positive revision; re-register sources without them")

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
        layers = [("client", self.parameter_layout.pack(client_values)), *self.get_config_layers(project)]
        view = resolve_config(layers, schema=self.describe_config())
        from .embedding import EmbeddingModel
        selected = EmbeddingModel if self.embedding is None else self.embedding
        view["enforced"] = {"config": {"embedding_params": deepcopy(getattr(selected, "enforced_configuration", {}))}}
        view["model_providers"] = {name: resolve_config([
            ("client", getattr(getattr(self, name), "provider_options", {})),
            ("project", view["values"].get("policy", {}).get("provider", {}))], schema=provider_schema())
            for name in ("embedding", "extractor", "reranker")}
        view["runtime"] = runtime
        return view

    def with_config(self, project, *, prompt=None):
        """저장 설정을 작업별 사본으로 바인딩한다. 등록된 객체와 SDK 함수를 변경하지 않는다."""
        from .embedding import EmbeddingModel
        from .extraction import TripleExtractor
        from .rerank import RerankModel
        view = self.resolve_config(project)
        values = self.parameter_layout.unpack(view["values"])
        worker = copy(self)
        worker._configuration_source = self
        for name in ("chunk_size", "embedding_concurrency", "embedding_cache_max_bytes", "query_cache_max_bytes", "snapshot_cache_max_bytes", "extraction_batch_size", "search_cache_chars", "document_kwargs", "query_kwargs", "index_batch_size"):
            setattr(worker, name, deepcopy(values.get(name, {} if name.endswith("kwargs") else None)))
        worker.search_options, worker.graph_options = values.get("search", {}), values.get("graph", {})
        worker.extraction_options = deepcopy(values.get("extraction", {}))
        worker.provider_options = values.get("provider", {})
        worker._cache_namespace = project.id
        worker._extraction_error = (ValueError("RAG prompt_id requires the selected prompts component through its Project handle")
            if worker.extraction_options.get("prompt_id") is not None and prompt is None else None)
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
                client = client.with_provider(view["model_providers"][name]["values"])
            setattr(worker, name, client)
        bind_extraction = getattr(worker.extractor, "with_extraction", None)
        if bind_extraction is not None:
            worker.extraction_options = ProjectConfig.merge(getattr(worker.extractor, "extraction", {}), worker.extraction_options)
            if prompt is None and getattr(worker.extractor, "prompt", None) is not None:
                worker.extraction_prompt = deepcopy(worker.extractor.prompt)
            worker.extractor = bind_extraction(worker.extraction_options, worker.extraction_prompt)
        elif worker.extractor is not None and worker.extraction_options.keys() - {"failure_policy"}:
            raise ValueError("Custom extractor requires with_extraction(options, prompt) to apply extraction policies")
        worker._configuration_version = revision_token({"configuration": values,
            "model_providers": {k: v["values"] for k, v in view["model_providers"].items()}, "prompt": worker.extraction_prompt,
                                                       "prompt_resolved": worker._extraction_error is None})
        return worker

    def describe_config(self):
        from llm.core.schema import object_schema, open_schema, field
        from .embedding import EmbeddingModel
        from .extraction import TripleExtractor
        from .rerank import RerankModel
        def child_schema(client, factory):
            selected = factory if client is None else client
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
            "search_cache_chars": field("integer", minimum=0),
            "index_batch_size": field("integer", minimum=1), "search": search_schema(),
            "graph": object_schema({"buffer_pool_size": field("integer", minimum=1),
                                    "max_num_threads": field("integer", minimum=1)}),
            "document_kwargs": deepcopy(embedding_schema), "query_kwargs": deepcopy(embedding_schema),
            "embedding_params": embedding_schema, "extraction_params": child_schema(self.extractor, TripleExtractor),
            "extraction": extraction_schema(),
            "provider": provider_schema(),
            "rerank_params": child_schema(self.reranker, RerankModel),
            "ingestion": object_schema({"max_active": field("integer", "동시 색인 작업 수", minimum=1)}),
            "retention": object_schema({"job_max_age_seconds": field(["number", "null"],
                "완료·취소·명시적으로 재개를 포기한 색인 작업의 입력/영수증 보관 기간. null은 무제한", exclusiveMinimum=0)})},
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
            selected = factory if client is None else client
            validate = getattr(selected, "validate_config", None)
            if validate is not None and key in data:
                validate(deepcopy(data[key]))

    def maintenance(self, project, *, apply=False, expected_version=None):
        """활성 코퍼스와 재개 자료를 보존하고, 만료된 종료/명시적 포기 작업만 정리한다."""
        import fcntl
        from datetime import datetime, timezone
        maximum = self._options(project).get("retention", {}).get("job_max_age_seconds")
        root = self._checked(self.root(project) / "jobs")
        candidates, protected, leases, sources = [], [], [], []
        try:
            for path in sorted(root.glob("*/job.json")):
                path = self._checked(path)
                job = read_json(path)
                if job["id"] != path.parent.name:
                    raise ValueError("Invalid RAG job identity")
                stamps = {}
                for entry in self._checked_tree(path.parent).rglob("*"):
                    if entry.is_file() and entry.name != "worker.lock":
                        stat = entry.stat()
                        stamps[str(entry.relative_to(path.parent))] = [stat.st_size, stat.st_mtime_ns]
                sources.append([job, stamps])
                abandoned = None
                if "resume_abandonment" in job:
                    from llm.core.checkpoints import ResumeAbandonment
                    abandoned = ResumeAbandonment.from_dict(job["resume_abandonment"])
                ended = abandoned.abandoned_at if abandoned else job.get("ended_at", job["created_at"])
                expired = maximum is not None and (datetime.now(timezone.utc) - datetime.fromisoformat(ended)).total_seconds() > maximum
                row = {"job_id": job["id"], "status": job["status"], "bytes": sum(s[0] for s in stamps.values())}
                if (job["status"] not in ("completed", "cancelled") and abandoned is None) or not expired:
                    protected.append({**row, "reason": "unfinished_or_retained"})
                    continue
                stream = self._checked(path.parent / "worker.lock").open("a+b")
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    stream.close()
                    protected.append({**row, "reason": "active_worker"})
                    continue
                except BaseException:
                    stream.close()
                    raise
                leases.append(stream)
                candidates.append(row)
            pending = [read_json(self._checked(p)) for p in sorted(self._checked(root / ".deletions").glob("*.json"))]
            result = {"candidates": candidates, "protected": protected, "pending": pending}
            version = revision_token({**result, "sources": sources, "max_age_seconds": maximum})
            if apply:
                if not expected_version or expected_version != version:
                    raise ValueError("maintenance_conflict: review a fresh component plan")
                recover_deletions(root)
                for row in candidates:
                    identifier = validate_name(row["job_id"])
                    if (root / identifier).exists():
                        remove_named_tree(root, root / identifier, identifier)
            return {**result, "version": version}
        finally:
            for stream in leases:
                stream.close()

    def validate_backup(self, project):
        super().validate_backup(project)
        snapshot = self.snapshot(project)
        generation = snapshot["generation"]
        if generation is not None:
            path = self._generation_path(project, generation)
            if not (path / "chroma").is_dir() or not (path / "graph.kuzu").is_file():
                raise ValueError("RAG backup requires matching vector and graph generation")
            for document in snapshot["documents"].values():
                validate_graph(document["graph"], document["chunks"])

    def resolve(self, project, capability):
        if capability == "tools":
            raise ValueError("Search tools require a lifecycle-bound component data factory")
        return super().resolve(project, capability)

    def resolve_runtime(self, project, capability, *, data_factory):
        """프로젝트가 선택한 컴포넌트의 검색 Tool을 별도 설정 없이 제공한다."""
        if capability != "tools":
            return self.resolve(project, capability)
        from .tools import search_tools
        return search_tools(data_factory(self.name))

    def corpus_version(self, project):
        """잠금 안에서 작은 포인터만 읽는다. 불변 코퍼스 전체를 다시 읽지 않는다."""
        root = self.root(project)
        identity = read_json(self._checked(root / self.IDENTITY_FILE))
        active = read_json(self._checked(root / self.ACTIVE_FILE))
        if not isinstance(identity, dict) or identity.keys() != {"id"}:
            raise ValueError("RAG identity record is incomplete or changed")
        if not isinstance(active, dict) or active.keys() != {"generation"}:
            raise ValueError("RAG active pointer is incomplete or changed")
        identity = identity["id"]
        self._validate_identity(identity)
        generation = active["generation"]
        if generation is None:
            generations = self._checked(root / self.GENERATIONS_DIRECTORY)
            if generations.exists() and any(generations.iterdir()):
                raise ValueError("Empty RAG pointer conflicts with stored generations")
        else:
            path = self._generation_path(project, generation)
            if not self._checked(path / "corpus.json").is_file():
                raise FileNotFoundError("RAG active generation requires its corpus")
        return identity, generation

    def snapshot(self, project):
        identity, generation = self.corpus_version(project)
        if generation is None:
            return {"identity": identity, "generation": None, "documents": {}, "profile": None}
        path = self._generation_path(project, generation)
        source = self._checked(path / "corpus.json")
        info = source.stat()  # Deletion/replacement must never be hidden by a cached generation.
        key = (str(source), identity, generation, info.st_dev, info.st_ino,
               info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        # Missing means inactive, not a tuning default or a materialized configuration value.
        maximum = 0 if self.snapshot_cache_max_bytes is None else self.snapshot_cache_max_bytes
        value = self.snapshot_cache.get(key, maximum)
        cached = value is not None
        if not cached:
            value = read_json(source)
        if (type(value.get("graph_schema_version")) is not int
                or value["graph_schema_version"] != self.GRAPH_SCHEMA_VERSION):
            raise ValueError("Unsupported RAG graph schema; re-register source documents in a new RAG Project")
        self._validate_versions(value["documents"])
        if any(not isinstance(doc.get("graph"), dict) or
               not {"entities", "relations"}.issubset(doc["graph"])
               for doc in value["documents"].values()):
            raise ValueError("RAG documents require graph entities and relations")
        if not self._checked(path / "graph.kuzu").is_file():
            raise FileNotFoundError("RAG generation requires graph.kuzu")
        if not cached:
            self.snapshot_cache.put(key, value, maximum)
        return {**value, "identity": identity, "generation": generation}

    def list_catalog(self, project, *, limit, cursor=None):
        """현재 세대에 함께 공개된 설명 목록만 읽는다. 원본은 corpus가 소유한다."""
        if type(limit) is not int or limit < 1:
            raise ValueError("Catalog limit must be a positive integer")
        identity, generation = self.corpus_version(project)
        after = ""
        if cursor is not None:
            if not isinstance(cursor, dict) or set(cursor) != {"identity", "generation", "after"}:
                raise ValueError("Invalid catalog cursor")
            if (cursor["identity"], cursor["generation"]) != (identity, generation):
                raise RAGConflictError("RAG catalog changed; restart pagination")
            after = validate_name(cursor["after"])
        rows = [] if generation is None else read_json(self._checked(self._generation_path(project, generation) / "catalog.json"))
        selected = [row for row in rows if row["id"] > after][:limit + 1]
        more = len(selected) > limit
        selected = selected[:limit]
        return {"documents": selected, "generation": generation,
                "next_cursor": {"identity": identity, "generation": generation, "after": selected[-1]["id"]} if more else None}

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
        vector = extract(response, **kwargs) if extract else extract_single_embedding(response)
        from llm.providers.embeddings import validate_vectors
        return validate_vectors([vector], dimensions=getattr(self, "_embedding_dimensions", None))

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
            "project": getattr(self, "_cache_namespace", None)})
        key = vector_key(fingerprint, query)
        vector = self.query_cache.get(key, maximum)
        cached = vector is not None
        if vector is None:
            vector = (await self.embed([query], query=True))[0]
        validate = getattr(self.embedding, "validate_vectors", None)
        if validate is not None:
            vector = validate([vector], **self.query_kwargs)[0]
        from llm.providers.embeddings import validate_vectors
        vector = validate_vectors([vector], dimensions=getattr(self, "_embedding_dimensions", None))[0]
        self.query_cache.put(key, vector, maximum)
        diagnostic("rag_query_embedding", cached=cached)
        return vector

    async def prepare(self, identifier, title, content, metadata, revision, *, progress=None, previous=None, telemetry=None):
        if telemetry:
            await telemetry({"phase": "validating_input"})
        policy = require_config(self.extraction_options, "failure_policy", scope="rag.extraction")
        if self.chunk_size is None:
            raise ValueError("Missing required setting: rag.chunk_size")
        if policy != "disabled" and self.extraction_batch_size is None:
            raise ValueError("Missing required setting: rag.extraction_batch_size")
        if policy != "disabled" and getattr(self, "_extraction_error", None) is not None:
            raise self._extraction_error
        if policy != "disabled" and self.extractor is None:
            raise ValueError("Configure RAGComponent(extractor=TripleExtractor(...)) before writing documents")
        validate_name(identifier)
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Document title must be nonempty text")
        metadata = self.deserialize(self.serialize(metadata))
        fingerprint = self._preparation_fingerprint()
        if (previous is not None and previous["content"] == content
                and previous.get("preparation_fingerprint") == fingerprint
                and (previous["graph_complete"] or policy == "disabled")):
            from llm.providers.embeddings import validate_vectors
            vectors = previous["vectors"]
            validate = getattr(self.embedding, "validate_vectors", None)
            if validate is not None:
                vectors = validate(vectors, **self.document_kwargs)
            dimensions = getattr(self, "_embedding_dimensions", None)
            vectors = validate_vectors(vectors, dimensions=previous["profile"]["dimensions"] if dimensions is None else dimensions)
            if len(vectors) != len(previous["chunks"]):
                raise ValueError("Stored chunk/vector count mismatch")
            validate_graph(previous["graph"], previous["chunks"],
                           relation_types=self.extraction_options.get("relation_types", ()))
            document = deepcopy(previous)
            document.update(title=title, metadata=metadata, revision=revision,
                            preparation_configuration=getattr(self, "_configuration_version", None))
            document["ingestion"] = {"chunks_total": len(vectors), "chunks_completed": len(vectors),
                "chunks_reused": len(vectors), "chunks_cached": 0, "chunks_requested": 0,
                "embedding_seconds": 0.0, "extraction_seconds": 0.0, "extraction_batches": 0,
                "provider_retries": 0, "checkpoint_reuse": 0, "metadata_only": True}
            return document
        if telemetry:
            await telemetry({"phase": "splitting"})
        document = split_markdown(content, identifier, chunk_size=self.chunk_size)
        document.update(id=identifier, title=title, metadata=metadata, revision=revision,
                        generation=previous["generation"] if previous is not None else uuid4().hex)
        await prepare_vectors(self, document, previous=previous, progress=progress, telemetry=telemetry)
        document["profile"] = {"model": self._identity(), "dimensions": len(document["vectors"][0])}
        document.update(graph_complete=policy != "disabled", graph_diagnostics=[])
        document["ingestion"].update(extraction_batches=0, extraction_seconds=0.0, extraction_batches_reused=0)
        if policy == "disabled":
            document["graph_diagnostics"].append(diagnostic("graph_extraction_disabled").to_dict())
        entities, relations = {}, []
        manifest, reused_sources = [], set()
        previous_batches = {entry["signature"]: entry for entry in previous.get("extraction_manifest", [])} if previous else {}
        previous_graph = group_evidence(previous["graph"]) if previous_batches else None
        extraction_total = ((len(document["chunks"]) + self.extraction_batch_size - 1) // self.extraction_batch_size
                            if policy != "disabled" else 0)
        extraction_done = 0
        async def extraction_progress():
            if telemetry:
                await telemetry({**document["ingestion"], "phase": "extracting", "completed": extraction_done,
                                 "total": extraction_total, "unit": "batch"})
        if policy != "disabled":
            await extraction_progress()
        def observe_extraction(event):
            if event.code == "provider_retry":
                document["ingestion"]["provider_retries"] += 1
        for offset in (range(0, len(document["chunks"]), self.extraction_batch_size) if policy != "disabled" else ()):
            batch = document["chunks"][offset:offset + self.extraction_batch_size]
            # Keep the existing Job checkpoint binding unchanged. Cross-update reuse has
            # its own implementation identity; it is not a replacement checkpoint format.
            signature = revision_token({"chunks": batch, "extractor": type(self.extractor).__qualname__,
                                        "params": getattr(self.extractor, "params", {}),
                                        "extraction": self.extraction_options, "prompt": self.extraction_prompt})
            reuse_signature = revision_token({"request": signature,
                "implementation": type(self.extractor).__module__ + "." + type(self.extractor).__qualname__})
            key = f"graph_{offset}"
            graph = await progress(key, signature) if progress else None
            if graph is None and reuse_signature in previous_batches:
                entry = previous_batches[reuse_signature]
                graph = select_graph(previous_graph, entry["entity_ids"], batch,
                                     relation_types=self.extraction_options.get("relation_types", ()))
                if revision_token(graph) != entry["digest"]:
                    raise GraphValidationError("Stored extraction batch integrity mismatch")
                reused_sources.update(chunk["id"] for chunk in batch)
                document["ingestion"]["extraction_batches_reused"] += 1
                if progress:
                    await progress(key, signature, graph)
            if graph is None:
                started = time.monotonic()
                try:
                    with diagnostic_scope(observe_extraction):
                        graph = validate_graph(await self.extractor.extract(batch), batch,
                                               relation_types=self.extraction_options.get("relation_types", ()))
                except Exception as error:
                    code = error_code(error)
                    # 저장/한도/프로그래밍 오류는 best_effort로 숨기지 않는다.
                    if policy != "best_effort" or not (isinstance(error, GraphValidationError) or code in (
                            "provider_timeout", "provider_connection", "provider_unavailable",
                            "provider_rate_limit", "provider_empty_response", "provider_invalid_response")):
                        raise
                    document["graph_complete"] = False
                    document["graph_diagnostics"].append(diagnostic("graph_incomplete", severity="warning",
                        batch_offset=offset, cause=code if code != "provider_failed" else "graph_validation_failed").to_dict())
                    continue
                finally:
                    extraction_done += 1
                    document["ingestion"]["extraction_batches"] += 1
                    document["ingestion"]["extraction_seconds"] += time.monotonic() - started
                    diagnostic("rag_extraction_progress", **document["ingestion"])
                    await extraction_progress()
                if progress:
                    await progress(key, signature, graph)
            else:
                graph = validate_graph(graph, batch, relation_types=self.extraction_options.get("relation_types", ()))
                extraction_done += 1
                await extraction_progress()
            entities.update({e["id"]: e for e in graph["entities"]})
            relations.extend(graph["relations"])
            manifest.append({"signature": reuse_signature, "entity_ids": [e["id"] for e in graph["entities"]],
                             "offset": offset, "count": len(batch)})
        document["graph"] = enrich_graph(validate_graph(
            {"entities": list(entities.values()), "relations": relations}, document["chunks"],
            relation_types=self.extraction_options.get("relation_types", ())), identifier)
        # Manifest stores identities/digests, not a second copy of the graph.
        grouped = group_evidence(document["graph"])
        for entry in manifest:
            batch = document["chunks"][entry["offset"]:entry["offset"] + entry["count"]]
            entry["digest"] = revision_token(select_graph(grouped, entry["entity_ids"], batch,
                relation_types=self.extraction_options.get("relation_types", ())))
        if reused_sources:
            def edge_key(edge):
                return tuple(edge[key] for key in ("source", "type", "target", "source_id", "evidence"))
            stamps = {edge_key(edge): edge["extracted_at"] for edge in previous["graph"]["relations"]
                      if edge["source_id"] in reused_sources}
            for edge in document["graph"]["relations"]:
                if edge_key(edge) in stamps:
                    edge["extracted_at"] = stamps[edge_key(edge)]
        document["extraction_manifest"] = manifest
        document["preparation_configuration"] = getattr(self, "_configuration_version", None)
        document["preparation_fingerprint"] = fingerprint
        return document

    def publish(self, project, previous, documents, *, progress=None):
        """DB를 완성·닫은 뒤 단일 포인터를 원자적으로 교체한다. 실패 세대는 검색되지 않는다."""
        if self.corpus_version(project) != (previous["identity"], previous["generation"]):
            raise RAGConflictError("Corpus changed during preparation; reload and retry explicitly")
        if progress:
            progress("validating")
        self._validate_versions(documents)
        profiles = [doc["profile"] for doc in documents.values()]
        if profiles and any(profile != profiles[0] for profile in profiles):
            raise ValueError("Cannot mix embedding models/dimensions in one corpus")
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
                shutil.copytree(source / "chroma", path / "chroma", copy_function=copy_file)
                copy_file(source / "graph.kuzu", path / "graph.kuzu")
                update_index(path / "chroma", previous["documents"], documents, batch_size=self.index_batch_size)
                update_graph(path / "graph.kuzu", previous["documents"], documents, options=self.graph_options)
            else:
                self._build(path, documents)
            if progress:
                progress("writing_metadata")
            atomic_json(path / "catalog.json", [{key: doc[key] for key in
                ("id", "title", "description", "generation", "revision") if key in doc}
                for _, doc in sorted(documents.items())])
            atomic_json(path / "corpus.json", {"graph_schema_version": self.GRAPH_SCHEMA_VERSION, "documents": documents,
                                              "profile": profiles[0] if profiles else None})
        except BaseException:
            remove_named_tree(path.parent, path, generation)
            raise
        # 포인터 교체 뒤 디렉토리 fsync가 실패할 수도 있다. 공개를 시도한 세대는
        # 오류가 나더라도 여기서 지우지 않아 active가 없는 DB를 가리키지 않게 한다.
        if progress:
            progress("publishing")
        atomic_json(self.root(project) / self.ACTIVE_FILE, {"generation": generation})
        # 이미 공개한 세대의 성공을 정리 실패로 바꾸지 않는다. compact로 재시도할 수 있다.
        try:
            self.compact(project)
        except (OSError, ValueError):
            log_event(project.paths.logs, "rag.cleanup_pending", entity_id=project.id)
        log_event(project.paths.logs, "rag.published", entity_id=project.id, count=len(documents))

    def compact(self, project):
        # 삭제 전에는 활성 코퍼스가 실제로 읽히는지 확인해 손상된 포인터로 정리하지 않는다.
        active = self.snapshot(project)["generation"]
        root = self._checked(self.root(project) / self.GENERATIONS_DIRECTORY)
        recover_deletions(root, protected=(active,))
        for path in list(root.iterdir()) if root.exists() else ():
            if path.name != active:
                remove_named_tree(root, path, validate_name(path.name))

    def search(self, project, snapshot, query, vector, **options):
        if snapshot["generation"] is None:
            return []
        path = self._checked_tree(self._generation_path(project, snapshot["generation"]))
        return search(path, snapshot["documents"], query, vector, lexical_cache=self.search_cache,
                      cache_chars=self.search_cache_chars, candidate_count=require_config(self.search_options, "candidate_count", scope="rag.search"),
                      rrf_constant=require_config(self.search_options, "rrf_constant", scope="rag.search"), **options)

    def graph_search(self, project, *, seed, max_hops, limit):
        snapshot = self.snapshot(project)
        incomplete = [identifier for identifier, doc in snapshot["documents"].items() if doc.get("graph_complete") is False]
        status = {"graph_complete": not incomplete, "graph_incomplete_documents": incomplete}
        path = (self._generation_path(project, snapshot["generation"]) / "graph.kuzu"
                if snapshot["generation"] else None)
        if path is None:
            return {"seed": seed, "entities": [], "relations": [], **status}
        return {**graph_search(path, seed, max_hops=max_hops, limit=limit, options=self.graph_options,
                              ranking=self.search_options.get("relation_ranking")), **status}

    def combined_result(self, project, snapshot, query, hits, *, max_hops, relation_limit):
        """동일한 불변 세대에서 문서 검색 결과와 근거 관계를 묶는다."""
        path = (self._generation_path(project, snapshot["generation"]) / "graph.kuzu"
                if snapshot["generation"] else None)
        graph = {"entities": [], "relations": []}
        if path is not None and hits:
            graph = related_graph(path, [hit["id"] for hit in hits], max_hops=max_hops, limit=relation_limit,
                                  options=self.graph_options, ranking=self.search_options.get("relation_ranking"))
        sources = {}
        for hit in hits:
            sources[hit["id"]] = {key: hit[key] for key in (
                "id", "document_id", "section_id", "heading", "text", "title", "generation", "revision", "metadata")}
        documents = snapshot["documents"]
        chunk_indexes = {}
        for relation in graph["relations"]:
            if relation["source_id"] in sources:
                continue
            identifier = relation["document_id"]
            doc = documents[identifier]
            # 같은 문서를 근거로 하는 관계마다 전체 문단을 다시 순회하지 않는다.
            if identifier not in chunk_indexes:
                chunk_indexes[identifier] = {c["id"]: c for c in doc["chunks"]}
            chunk = chunk_indexes[identifier][relation["source_id"]]
            sources[chunk["id"]] = {**chunk, "title": doc["title"], "generation": doc["generation"], "revision": doc["revision"],
                                     "metadata": doc["metadata"]}
        incomplete = [identifier for identifier, doc in documents.items() if doc.get("graph_complete") is False]
        return {"query": query, "documents": hits, **graph, "sources": list(sources.values()),
                "graph_complete": not incomplete, "graph_incomplete_documents": incomplete}

    def clone(self, source, destination):
        super().clone(source, destination)
        snapshot = self.snapshot(source)
        if snapshot["generation"] is not None:
            # 불변 원문/벡터/관계를 복제해 DB를 재구축한다. 인증/모델 호출은 필요 없다.
            self.with_config(destination).publish(destination, self.snapshot(destination), snapshot["documents"])
