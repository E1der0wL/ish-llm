"""문서·임베딩·색인의 세대 관리. 공개 핸들은 잠금 아래에서 이 구현을 호출한다."""

from copy import copy, deepcopy
import time
import shutil
from uuid import uuid4

from llm.components.base import validate_name
from llm.components.definitions import DefinitionComponent
from llm.services.infrastructure.storage import atomic_json, read_json, remove_named_tree, recover_deletions, revision_token
from llm.services.infrastructure.logging import log_event
from .data import RAGData
from .indexing import build_index, update_index
from .search import search, search_defaults, search_schema
from .splitting import split_markdown
from .extraction import validate_graph, enrich_graph, GraphValidationError
from .prompts import default_prompt, extraction_defaults, extraction_schema
from .graph_indexing import build_graph, update_graph
from .graph_search import graph_search, related_graph
from .files import copy_file
from llm.core.configuration import resolve_configuration
from llm.providers.requests import provider_defaults, provider_schema, error_code
from llm.providers.embeddings import validate_embeddings
from llm.providers.runtime import diagnostic, diagnostic_scope
from .ingestion import VectorCache, prepare_vectors


class RAGConflictError(RuntimeError):
    """준비 중 문서/프로젝트 상태가 바뀌었다. 자동 재시도 없이 호출자에게 알린다."""


class RAGComponent(DefinitionComponent):
    """열린 정의 CRUD와 문서 색인 API를 분리한다. 모델 객체/인증은 런타임에만 둔다."""

    name = "rag"
    directory = "rag"
    capabilities = ("rag", "tools")
    data_class = RAGData

    def __init__(self, *, embedding=None, embedding_id=None, reranker=None, extractor=None):
        """모델 구현만 주입한다. 분할·배치·검색 설정은 ProjectConfig에서 받는다."""
        self.embedding, self.reranker, self.extractor = embedding, reranker, extractor
        self.embedding_id = embedding_id
        values = self.default_configuration()
        for name in ("chunk_size", "embedding_batch_size", "extraction_batch_size", "search_cache_chars",
                     "document_kwargs", "query_kwargs", "index_batch_size"):
            setattr(self, name, deepcopy(values[name]))
        from .search import LexicalCache
        self.search_cache = LexicalCache(values["search_cache_chars"])
        self.search_options, self.graph_options = values["search"], values["graph"]
        self.extraction_options, self.extraction_prompt = values["extraction"], default_prompt()
        self.provider_options, self.batching_options = values["provider"], values["embedding_batching"]
        self.vector_cache = VectorCache()

    def _generation_path(self, project, generation):
        return self._checked(self.root(project) / "generations" / validate_name(generation))

    def _checked_tree(self, path):
        self._checked(path)
        for entry in path.rglob("*"):
            self._checked(entry)
        return path

    def _build(self, path, documents):
        build_index(path / "chroma", documents, batch_size=self.index_batch_size)
        build_graph(path / "graph.kuzu", documents, options=self.graph_options)

    def _identity(self):
        if self.embedding is None:
            raise ValueError("Configure RAGComponent(embedding=EmbeddingModel(...)) first")
        identifier = self.embedding_id or self.document_kwargs.get("model", getattr(self.embedding, "params", {}).get("model"))
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("Custom embedding clients require embedding_id")
        return identifier

    # 공개 API: 직접 호출 시 workspace 소유권은 호출자가 보장해야 한다.
    def default_configuration(self):
        return {"chunk_size": 2000, "embedding_batch_size": 128, "extraction_batch_size": 32,
                "search_cache_chars": 1_000_000, "document_kwargs": {}, "query_kwargs": {},
                "embedding_params": {}, "extraction_params": {}, "rerank_params": {},
                "extraction": extraction_defaults(),
                "provider": provider_defaults(),
                "embedding_batching": {"max_batch_size": 128, "max_batch_chars": None,
                    "max_split_depth": 0, "cache_max_bytes": 16 * 1024 * 1024},
                "search": search_defaults(), "index_batch_size": 128,
                "graph": {"buffer_pool_size": 64 * 1024 * 1024, "max_num_threads": 2},
                "ingestion": {"max_active": 1}, "retention": {"job_max_age_seconds": None}}

    def effective_configuration(self, project):
        if getattr(self, "_configuration_source", None) is not None:
            return self._configuration_source.effective_configuration(project)
        client_defaults = {}
        runtime = []
        for name, client in (("embedding_params", self.embedding), ("extraction_params", self.extractor), ("rerank_params", self.reranker)):
            if client is not None:
                params = getattr(client, "params", {})
                try:
                    self.serialize(params)
                    client_defaults[name] = params
                except (ValueError, TypeError):
                    runtime.append(name)
        layers = [("client", client_defaults), *self.configuration_layers(project)]
        from .extraction import TripleExtractor
        if isinstance(self.extractor, TripleExtractor) or self.extractor is None and any(
                values.get("extraction_params") for _, values in layers):
            layers.append(("extraction_contract", {"extraction_params": {"temperature": 0}}))
        # SDK retry 설정은 그대로 노출하고 RAG 임베딩의 캐시 무결성 계약만 표시한다.
        from .embedding import EmbeddingModel
        if isinstance(self.embedding, EmbeddingModel) or self.embedding is None and any(
                values.get("embedding_params") for _, values in layers):
            layers.append(("embedding_integrity", {"embedding_params": {
                "caching": False, "cache": {"no-cache": True, "no-store": True}}}))
        view = resolve_configuration(self.default_configuration(), layers,
            schema=self.configuration_schema())
        view["runtime"] = runtime
        return view

    def configured(self, project, *, prompt=None):
        """저장 설정을 작업별 사본으로 바인딩한다. 등록된 객체와 SDK 함수를 변경하지 않는다."""
        from .embedding import EmbeddingModel
        from .extraction import TripleExtractor
        from .rerank import RerankModel
        values = self.effective_configuration(project)["values"]
        worker = copy(self)
        worker._configuration_source = self
        for name in ("chunk_size", "embedding_batch_size", "extraction_batch_size", "search_cache_chars", "document_kwargs", "query_kwargs", "index_batch_size"):
            setattr(worker, name, deepcopy(values[name]))
        worker.search_options, worker.graph_options = values["search"], values["graph"]
        worker.extraction_options = deepcopy(values["extraction"])
        worker.provider_options, worker.batching_options = values["provider"], values["embedding_batching"]
        worker._cache_namespace = project.id
        worker._extraction_error = (ValueError("RAG prompt_id requires the selected prompts component through its Project handle")
            if worker.extraction_options["prompt_id"] is not None and prompt is None else None)
        worker.extraction_prompt = deepcopy(default_prompt() if prompt is None else prompt)
        for name, key, factory in (("embedding", "embedding_params", EmbeddingModel),
                                   ("extractor", "extraction_params", TripleExtractor), ("reranker", "rerank_params", RerankModel)):
            client, params = getattr(self, name), values[key]
            if client is None and params:
                client = factory(**params)
            elif client is not None and params:
                configure = getattr(client, "configured", None)
                if configure is not None:
                    client = configure(params)
                elif self.configuration(project).get(key):
                    raise ValueError(f"Custom {name} requires configured(params) to apply project parameters")
            if client is not None and callable(getattr(client, "with_provider", None)):
                client = client.with_provider(worker.provider_options)
            setattr(worker, name, client)
        bind_extraction = getattr(worker.extractor, "with_extraction", None)
        if bind_extraction is not None:
            worker.extractor = bind_extraction(worker.extraction_options, worker.extraction_prompt)
        elif worker.extractor is not None and any(worker.extraction_options[key] != extraction_defaults()[key]
                 for key in ("repair_attempts", "prompt_id", "relation_types", "json_mode")):
            raise ValueError("Custom extractor requires with_extraction(options, prompt) to apply extraction policies")
        worker._configuration_version = revision_token({"configuration": values, "prompt": worker.extraction_prompt,
                                                       "prompt_resolved": worker._extraction_error is None})
        return worker

    def configuration_schema(self):
        from llm.core.schema import object_schema, field, completion_schema
        model_params = object_schema({key: value for key, value in completion_schema()["properties"].items()
                                      if key in ("model", "api_key", "api_base", "timeout", "num_retries")},
                                     **{"x-open-parameters": True})
        return object_schema({
            "chunk_size": field("integer", 2000, minimum=64),
            "embedding_batch_size": field("integer", 128, minimum=1),
            "extraction_batch_size": field("integer", 32, minimum=1),
            "search_cache_chars": field("integer", 1_000_000, minimum=0),
            "index_batch_size": field("integer", 128, minimum=1), "search": search_schema(),
            "graph": object_schema({"buffer_pool_size": field("integer", 64 * 1024 * 1024, minimum=1),
                                    "max_num_threads": field("integer", 2, minimum=1)}),
            "document_kwargs": object_schema(), "query_kwargs": object_schema(),
            "embedding_params": deepcopy(model_params), "extraction_params": completion_schema(),
            "extraction": extraction_schema(),
            "provider": provider_schema(),
            "embedding_batching": object_schema({
                "max_batch_size": field("integer", 128, minimum=1),
                "max_batch_chars": field(["integer", "null"], None, minimum=1),
                "max_split_depth": field("integer", 0, minimum=0, maximum=8),
                "cache_max_bytes": field("integer", 16 * 1024 * 1024, minimum=0)}),
            "rerank_params": deepcopy(model_params),
            "ingestion": object_schema({"max_active": field("integer", 1, "동시 색인 작업 수", minimum=1)}),
            "retention": object_schema({"job_max_age_seconds": field(["number", "null"], None,
                "완료·취소한 색인 작업의 입력/영수증 보관 기간. null은 무제한", exclusiveMinimum=0)})},
            default=self.default_configuration(),
            **{"x-runtime-configuration": ["embedding", "extractor", "reranker", "embedding_id"]})

    def validate_configuration(self, data):
        from jsonschema import Draft202012Validator
        error = next(Draft202012Validator(self.configuration_schema()).iter_errors(data), None)
        if error:
            raise ValueError("Invalid RAG configuration: " + error.message)
        for value in (data.get("ingestion", {}).get("max_active", 1),):
            if type(value) is not int:
                raise ValueError("ingestion.max_active requires an integer")
        for name in ("chunk_size", "embedding_batch_size", "extraction_batch_size", "search_cache_chars", "index_batch_size"):
            if name in data and type(data[name]) is not int:
                raise ValueError(f"{name} requires an integer")
        if type(data.get("extraction", {}).get("repair_attempts", 2)) is not int:
            raise ValueError("extraction.repair_attempts requires an integer")

    def maintenance(self, project, *, apply=False, expected_version=None):
        """활성 코퍼스와 실패 작업의 재개 자료를 보존하고, 만료된 종료 작업만 정리한다."""
        import fcntl
        from datetime import datetime, timezone
        maximum = self.configuration(project).get("retention", {}).get("job_max_age_seconds")
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
                ended = job.get("ended_at", job["created_at"])
                expired = maximum is not None and (datetime.now(timezone.utc) - datetime.fromisoformat(ended)).total_seconds() > maximum
                row = {"job_id": job["id"], "status": job["status"], "bytes": sum(s[0] for s in stamps.values())}
                if job["status"] not in ("completed", "cancelled") or not expired:
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
        active = self._checked(self.root(project) / "active.json")
        if active.exists():
            generation = validate_name(read_json(active)["generation"])
            path = self._generation_path(project, generation)
            corpus = read_json(self._checked(path / "corpus.json"))
            if not (path / "chroma").is_dir() or not (path / "graph.kuzu").is_file():
                raise ValueError("RAG backup requires matching vector and graph generation")
            for document in corpus["documents"].values():
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
        identity_path = self._checked(root / "identity.json")
        if not identity_path.exists():
            atomic_json(identity_path, {"id": uuid4().hex})
        identity = read_json(identity_path)["id"]
        active = self._checked(root / "active.json")
        if not active.exists():
            return identity, None
        generation = validate_name(read_json(active)["generation"])
        return identity, generation

    def snapshot(self, project):
        identity, generation = self.corpus_version(project)
        if generation is None:
            return {"identity": identity, "generation": None, "documents": {}, "profile": None}
        path = self._generation_path(project, generation)
        value = read_json(self._checked(path / "corpus.json"))
        if value.get("graph_schema_version") != 2:
            raise ValueError("Unsupported RAG graph schema; re-register source documents in a new RAG Project")
        if any(not isinstance(doc.get("graph"), dict) or
               not {"entities", "relations"}.issubset(doc["graph"])
               for doc in value["documents"].values()):
            raise ValueError("RAG documents require graph entities and relations")
        if not self._checked(path / "graph.kuzu").is_file():
            raise FileNotFoundError("RAG generation requires graph.kuzu")
        return {**value, "identity": identity, "generation": generation}

    async def embed(self, texts: list, *, query=False) -> list:
        self._identity()
        if query and self.query_kwargs.get("model", self._identity()) != self._identity():
            raise ValueError("Query embedding model differs from indexed model")
        response = await self.embedding.embed(texts, **(self.query_kwargs if query else self.document_kwargs))
        params = {**getattr(self.embedding, "params", {}), **(self.query_kwargs if query else self.document_kwargs)}
        return validate_embeddings(response, len(texts), dimensions=params.get("dimensions"))

    async def prepare(self, identifier, title, content, metadata, revision, *, progress=None, previous=None, telemetry=None):
        policy = self.extraction_options["failure_policy"]
        if policy != "disabled" and getattr(self, "_extraction_error", None) is not None:
            raise self._extraction_error
        if policy != "disabled" and self.extractor is None:
            raise ValueError("Configure RAGComponent(extractor=TripleExtractor(...)) before writing documents")
        validate_name(identifier)
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Document title must be nonempty text")
        metadata = self.deserialize(self.serialize(metadata))
        document = split_markdown(content, identifier, chunk_size=self.chunk_size)
        document.update(id=identifier, title=title, metadata=metadata, revision=revision)
        await prepare_vectors(self, document, previous=previous, progress=progress, telemetry=telemetry)
        document["profile"] = {"model": self._identity(), "dimensions": len(document["vectors"][0])}
        document.update(graph_complete=policy != "disabled", graph_diagnostics=[])
        document["ingestion"].update(extraction_batches=0, extraction_seconds=0.0)
        if policy == "disabled":
            document["graph_diagnostics"].append(diagnostic("graph_extraction_disabled").to_dict())
        entities, relations = {}, []
        def observe_extraction(event):
            if event.code == "provider_retry":
                document["ingestion"]["provider_retries"] += 1
        for offset in (range(0, len(document["chunks"]), self.extraction_batch_size) if policy != "disabled" else ()):
            batch = document["chunks"][offset:offset + self.extraction_batch_size]
            signature = revision_token({"chunks": batch, "extractor": type(self.extractor).__qualname__,
                                        "params": getattr(self.extractor, "params", {}),
                                        "extraction": self.extraction_options, "prompt": self.extraction_prompt}) if progress else None
            key = f"graph_{offset}"
            graph = await progress(key, signature) if progress else None
            if graph is None:
                started = time.monotonic()
                try:
                    with diagnostic_scope(observe_extraction):
                        graph = validate_graph(await self.extractor.extract(batch), batch,
                                               relation_types=self.extraction_options["relation_types"])
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
                    document["ingestion"]["extraction_batches"] += 1
                    document["ingestion"]["extraction_seconds"] += time.monotonic() - started
                    diagnostic("rag_extraction_progress", **document["ingestion"])
                    if telemetry:
                        await telemetry(dict(document["ingestion"]))
                if progress:
                    await progress(key, signature, graph)
            else:
                graph = validate_graph(graph, batch, relation_types=self.extraction_options["relation_types"])
            entities.update({e["id"]: e for e in graph["entities"]})
            relations.extend(graph["relations"])
        document["graph"] = enrich_graph(validate_graph(
            {"entities": list(entities.values()), "relations": relations}, document["chunks"],
            relation_types=self.extraction_options["relation_types"]), identifier)
        document["preparation_configuration"] = getattr(self, "_configuration_version", None)
        return document

    def publish(self, project, previous, documents):
        """DB를 완성·닫은 뒤 단일 포인터를 원자적으로 교체한다. 실패 세대는 검색되지 않는다."""
        if self.corpus_version(project) != (previous["identity"], previous["generation"]):
            raise RAGConflictError("Corpus changed during preparation; reload and retry explicitly")
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
            if previous["generation"] and type(self)._build is RAGComponent._build and "_build" not in self.__dict__:
                source = self._checked_tree(self._generation_path(project, previous["generation"]))
                shutil.copytree(source / "chroma", path / "chroma", copy_function=copy_file)
                copy_file(source / "graph.kuzu", path / "graph.kuzu")
                update_index(path / "chroma", previous["documents"], documents, batch_size=self.index_batch_size)
                update_graph(path / "graph.kuzu", previous["documents"], documents, options=self.graph_options)
            else:
                self._build(path, documents)
            atomic_json(path / "corpus.json", {"graph_schema_version": 2, "documents": documents,
                                              "profile": profiles[0] if profiles else None})
        except BaseException:
            remove_named_tree(path.parent, path, generation)
            raise
        # 포인터 교체 뒤 디렉토리 fsync가 실패할 수도 있다. 공개를 시도한 세대는
        # 오류가 나더라도 여기서 지우지 않아 active가 없는 DB를 가리키지 않게 한다.
        atomic_json(self.root(project) / "active.json", {"generation": generation})
        # 이미 공개한 세대의 성공을 정리 실패로 바꾸지 않는다. compact로 재시도할 수 있다.
        try:
            self.compact(project)
        except (OSError, ValueError):
            log_event(project.paths.logs, "rag.cleanup_pending", entity_id=project.id)
        log_event(project.paths.logs, "rag.published", entity_id=project.id, count=len(documents))

    def compact(self, project):
        # 삭제 전에는 활성 코퍼스가 실제로 읽히는지 확인해 손상된 포인터로 정리하지 않는다.
        active = self.snapshot(project)["generation"]
        root = self._checked(self.root(project) / "generations")
        recover_deletions(root, protected=(active,))
        for path in list(root.iterdir()) if root.exists() else ():
            if path.name != active:
                remove_named_tree(root, path, validate_name(path.name))

    def search(self, project, snapshot, query, vector, **options):
        if snapshot["generation"] is None:
            return []
        path = self._checked_tree(self._generation_path(project, snapshot["generation"]))
        return search(path, snapshot["documents"], query, vector, lexical_cache=self.search_cache,
                      cache_chars=self.search_cache_chars, candidate_count=self.search_options["candidate_count"],
                      rrf_constant=self.search_options["rrf_constant"], **options)

    def graph_search(self, project, *, seed, max_hops, limit):
        snapshot = self.snapshot(project)
        incomplete = [identifier for identifier, doc in snapshot["documents"].items() if doc.get("graph_complete") is False]
        status = {"graph_complete": not incomplete, "graph_incomplete_documents": incomplete}
        path = (self._generation_path(project, snapshot["generation"]) / "graph.kuzu"
                if snapshot["generation"] else None)
        if path is None:
            return {"seed": seed, "entities": [], "relations": [], **status}
        return {**graph_search(path, seed, max_hops=max_hops, limit=limit, options=self.graph_options), **status}

    def combined_result(self, project, snapshot, query, hits, *, max_hops, relation_limit):
        """동일한 불변 세대에서 문서 검색 결과와 근거 관계를 묶는다."""
        path = (self._generation_path(project, snapshot["generation"]) / "graph.kuzu"
                if snapshot["generation"] else None)
        graph = {"entities": [], "relations": []}
        if path is not None and hits:
            graph = related_graph(path, [hit["id"] for hit in hits], max_hops=max_hops, limit=relation_limit, options=self.graph_options)
        sources = {}
        for hit in hits:
            sources[hit["id"]] = {key: hit[key] for key in (
                "id", "document_id", "section_id", "heading", "text", "title", "revision", "metadata")}
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
            sources[chunk["id"]] = {**chunk, "title": doc["title"], "revision": doc["revision"],
                                     "metadata": doc["metadata"]}
        incomplete = [identifier for identifier, doc in documents.items() if doc.get("graph_complete") is False]
        return {"query": query, "documents": hits, **graph, "sources": list(sources.values()),
                "graph_complete": not incomplete, "graph_incomplete_documents": incomplete}

    def clone(self, source, destination):
        super().clone(source, destination)
        snapshot = self.snapshot(source)
        if snapshot["generation"] is not None:
            # 불변 원문/벡터/관계를 복제해 DB를 재구축한다. 인증/모델 호출은 필요 없다.
            self.configured(destination).publish(destination, self.snapshot(destination), snapshot["documents"])
