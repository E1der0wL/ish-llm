"""Model preparation and reuse; publication and Job admission stay with their Owners."""

from copy import deepcopy
import time
from uuid import uuid4
from llm.components.base import validate_name
from llm.core.configuration import require_config
from llm.core.contracts import DiagnosticSeverity
from llm.core.model_protocol import EmbeddingResult
from llm.providers.codes import ProviderErrorCode
from llm.providers.diagnostic_values import ProviderRetryObservation
from llm.providers.requests import error_code
from llm.providers.runtime import diagnostic, diagnostic_scope
from llm.services.infrastructure.storage import revision_token
from ._client import ModelClient
from .configuration import RAGConfiguration, RAGConfigurationVersion
from .embedding import EmbeddingModel
from .chunk_records import RAGChunk, SplitDocument
from .document_records import RAGDocument, ExtractionManifestEntry
from .graph_records import (
    GraphEntity,
    GraphRelation,
    GraphRecord,
    ExtractionCheckpoint,
    validate_graph,
    validate_extraction_checkpoint,
    enrich_graph,
    group_evidence,
    select_graph,
    GraphValidationError,
)
from .ingestion import VectorCache, EmbeddingExecution, prepare_vectors
from .ingestion_records import (
    BatchCheckpointName,
    EmbeddingEvidence,
    EmbeddingProfile,
    EmbeddingFingerprint,
    IngestionObservation,
)
from .prompts import ExtractionFailurePolicy, ExtractionOptions
from .splitting import split_markdown


class RAGEmbedding:
    ABSENT = None
    DOCUMENT = False
    QUERY = True
    ONE_CHUNK = 1
    EMPTY_CACHE_KEY = ""
    REQUIRED_MODEL = "Configure RAGComponent(embedding=EmbeddingModel(...)) first"
    REQUIRED_IDENTITY = "Custom embedding clients require embedding_id"
    INVALID_BATCH = "RAG embedding requires one chunk per request"
    QUERY_MODEL_MISMATCH = "Query embedding model differs from indexed model"
    INVALID_RESULT = "Embedding model must return EmbeddingResult"
    QUERY_EVENT = "rag_query_embedding"

    @classmethod
    def observe_query(cls, cached):
        diagnostic(cls.QUERY_EVENT, cached=cached)

    @staticmethod
    def identity(component):
        if component.embedding is RAGEmbedding.ABSENT:
            raise ValueError(RAGEmbedding.REQUIRED_MODEL)
        identity = getattr(component.embedding, EmbeddingModel.identity.__name__, RAGEmbedding.ABSENT)
        identifier = component.embedding_id or (
            identity(**component.document_kwargs) if identity else RAGEmbedding.ABSENT
        )
        if not isinstance(identifier, str) or not identifier:
            raise ValueError(RAGEmbedding.REQUIRED_IDENTITY)
        return identifier

    @staticmethod
    async def embed(component, texts: list, *, query=DOCUMENT) -> list:
        if not isinstance(texts, list) or len(texts) != RAGEmbedding.ONE_CHUNK:
            raise ValueError(RAGEmbedding.INVALID_BATCH)
        component._identity()
        identity = getattr(component.embedding, EmbeddingModel.identity.__name__, RAGEmbedding.ABSENT)
        if (
            query
            and identity is not RAGEmbedding.ABSENT
            and (identity(**component.query_kwargs) != identity(**component.document_kwargs))
        ):
            raise ValueError(RAGEmbedding.QUERY_MODEL_MISMATCH)
        kwargs = component.query_kwargs if query else component.document_kwargs
        response = await component.embedding.embed(texts, **kwargs)
        extract = getattr(component.embedding, EmbeddingModel.extract_vector.__name__, RAGEmbedding.ABSENT)
        if not isinstance(response, EmbeddingResult):
            raise TypeError(RAGEmbedding.INVALID_RESULT)
        vector = extract(response, **kwargs) if extract else response.vector
        from llm.core.embeddings import validate_vectors

        return validate_vectors(
            [vector],
            dimensions=getattr(component, component.EMBEDDING_DIMENSIONS_ATTRIBUTE, RAGEmbedding.ABSENT),
        )

    @staticmethod
    async def embed_query(component, query):
        """Cache only validated query vectors, never retrieval results or SDK responses."""
        maximum = component.query_cache_max_bytes
        if not maximum:
            component.query_cache.get(RAGEmbedding.EMPTY_CACHE_KEY, EmbeddingExecution.DISABLED_CACHE_BYTES)
            return (await component.embed([query], query=RAGEmbedding.QUERY))[EmbeddingModel.FIRST_VECTOR]
        identity = getattr(component.embedding, EmbeddingModel.identity.__name__, RAGEmbedding.ABSENT)
        if identity is not RAGEmbedding.ABSENT and identity(**component.query_kwargs) != identity(
            **component.document_kwargs
        ):
            raise ValueError(RAGEmbedding.QUERY_MODEL_MISMATCH)
        client_identity = getattr(
            component.embedding, ModelClient.cache_identity.__name__, RAGEmbedding.ABSENT
        )
        fingerprint = QueryFingerprint.create(component, client_identity)
        key = VectorCache.key(fingerprint, query)
        vector = component.query_cache.get(key, maximum)
        cached = vector is not RAGEmbedding.ABSENT
        if vector is RAGEmbedding.ABSENT:
            vector = (await component.embed([query], query=RAGEmbedding.QUERY))[EmbeddingModel.FIRST_VECTOR]
        validate = getattr(component.embedding, EmbeddingModel.validate_vectors.__name__, RAGEmbedding.ABSENT)
        if validate is not RAGEmbedding.ABSENT:
            vector = validate([vector], **component.query_kwargs)[EmbeddingModel.FIRST_VECTOR]
        from llm.core.embeddings import validate_vectors

        vector = validate_vectors(
            [vector],
            dimensions=getattr(component, component.EMBEDDING_DIMENSIONS_ATTRIBUTE, RAGEmbedding.ABSENT),
        )[EmbeddingModel.FIRST_VECTOR]
        component.query_cache.put(key, vector, maximum)
        RAGEmbedding.observe_query(cached)
        return vector


class PreparationFingerprint:
    ABSENT = None
    TYPE_SEPARATOR = "."

    @staticmethod
    def create(component):
        """Only preparation inputs; cache/search tuning does not change document semantics."""
        return revision_token(
            {
                RAGConfiguration.Field.CHUNK_SIZE: component.chunk_size,
                EmbeddingProfile.Field.MODEL: component._identity(),
                RAGConfiguration.Model.EMBEDDING: type(component.embedding).__module__
                + PreparationFingerprint.TYPE_SEPARATOR
                + type(component.embedding).__qualname__,
                RAGConfiguration.Field.EMBEDDING_PARAMS: getattr(
                    component.embedding, ModelClient.PARAMETERS_ATTRIBUTE, {}
                ),
                RAGConfiguration.Field.DOCUMENT_KWARGS: component.document_kwargs,
                RAGConfiguration.Field.QUERY_KWARGS: component.query_kwargs,
                RAGConfiguration.Model.EXTRACTOR: type(component.extractor).__module__
                + PreparationFingerprint.TYPE_SEPARATOR
                + type(component.extractor).__qualname__,
                RAGConfiguration.Field.EXTRACTION_PARAMS: getattr(
                    component.extractor, ModelClient.PARAMETERS_ATTRIBUTE, {}
                ),
                ExtractionOptions.KEY: component.extraction_options,
                RAGConfigurationVersion.PROMPT: component.extraction_prompt,
                RAGConfiguration.Field.EXTRACTION_BATCH_SIZE: component.extraction_batch_size,
            }
        )


class RAGPreparation:
    ABSENT = None
    MISSING_CHUNK_SIZE = "Missing required setting: rag.chunk_size"
    MISSING_EXTRACTION_BATCH = "Missing required setting: rag.extraction_batch_size"
    REQUIRED_EXTRACTOR = "Configure RAGComponent(extractor=TripleExtractor(...)) before writing documents"
    INVALID_TITLE = "Document title must be nonempty text"
    INVALID_VECTOR_COUNT = "Stored chunk/vector count mismatch"
    BEST_EFFORT_PROVIDER_CODES = (
        ProviderErrorCode.TIMEOUT.value,
        ProviderErrorCode.CONNECTION.value,
        ProviderErrorCode.UNAVAILABLE.value,
        ProviderErrorCode.RATE_LIMIT.value,
        ProviderErrorCode.EMPTY_RESPONSE.value,
        ProviderErrorCode.INVALID_RESPONSE.value,
    )

    @staticmethod
    async def prepare(
        component,
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
        if telemetry:
            await telemetry(IngestionObservation.phase(IngestionObservation.VALIDATING_INPUT_PHASE))
        policy = require_config(
            component.extraction_options,
            ExtractionOptions.Field.FAILURE_POLICY,
            scope=ExtractionOptions.SCOPE,
        )
        if component.chunk_size is RAGPreparation.ABSENT:
            raise ValueError(RAGPreparation.MISSING_CHUNK_SIZE)
        if (
            policy != ExtractionFailurePolicy.DISABLED.value
            and component.extraction_batch_size is RAGPreparation.ABSENT
        ):
            raise ValueError(RAGPreparation.MISSING_EXTRACTION_BATCH)
        if (
            policy != ExtractionFailurePolicy.DISABLED.value
            and getattr(component, RAGConfiguration.EXTRACTION_ERROR_ATTRIBUTE, RAGPreparation.ABSENT)
            is not RAGPreparation.ABSENT
        ):
            raise component._extraction_error
        if policy != ExtractionFailurePolicy.DISABLED.value and component.extractor is RAGPreparation.ABSENT:
            raise ValueError(RAGPreparation.REQUIRED_EXTRACTOR)
        validate_name(identifier)
        if not isinstance(title, str) or not title.strip():
            raise ValueError(RAGPreparation.INVALID_TITLE)
        metadata = component.deserialize(component.serialize(metadata))
        fingerprint = component._preparation_fingerprint()
        if (
            previous is not RAGPreparation.ABSENT
            and SplitDocument(previous).content == content
            and (RAGDocument(previous).prior_preparation_fingerprint == fingerprint)
            and (RAGDocument(previous).graph_complete or policy == ExtractionFailurePolicy.DISABLED.value)
        ):
            from llm.core.embeddings import validate_vectors

            vectors = EmbeddingEvidence(previous).vectors
            validate = getattr(
                component.embedding, EmbeddingModel.validate_vectors.__name__, RAGPreparation.ABSENT
            )
            if validate is not RAGPreparation.ABSENT:
                vectors = validate(vectors, **component.document_kwargs)
            dimensions = getattr(component, component.EMBEDDING_DIMENSIONS_ATTRIBUTE, RAGPreparation.ABSENT)
            vectors = validate_vectors(
                vectors,
                dimensions=(
                    EmbeddingEvidence(previous).profile.dimensions
                    if dimensions is RAGPreparation.ABSENT
                    else dimensions
                ),
            )
            if len(vectors) != len(SplitDocument(previous).chunks):
                raise ValueError(RAGPreparation.INVALID_VECTOR_COUNT)
            validate_graph(
                RAGDocument(previous).graph,
                SplitDocument(previous).chunks,
                relation_types=component.extraction_options.get(ExtractionOptions.Field.RELATION_TYPES, ()),
            )
            document = deepcopy(previous)
            RAGDocument.update_preparation_metadata(
                document,
                title,
                metadata,
                revision,
                getattr(component, RAGConfiguration.VERSION_ATTRIBUTE, RAGPreparation.ABSENT),
            )
            EmbeddingEvidence.record_observations(document, IngestionObservation.metadata_only(len(vectors)))
            return document
        if telemetry:
            await telemetry(IngestionObservation.phase(IngestionObservation.SPLITTING_PHASE))
        document = split_markdown(content, identifier, chunk_size=component.chunk_size)
        RAGDocument.identify_preparation(
            document,
            identifier,
            title,
            metadata,
            revision,
            RAGDocument(previous).generation if previous is not RAGPreparation.ABSENT else uuid4().hex,
        )
        await prepare_vectors(component, document, previous=previous, progress=progress, telemetry=telemetry)
        EmbeddingEvidence.record_profile(
            document,
            EmbeddingProfile.create(
                component._identity(), len(EmbeddingEvidence(document).vectors[EmbeddingModel.FIRST_VECTOR])
            ),
        )
        RAGDocument.begin_graph(document, policy != ExtractionFailurePolicy.DISABLED.value)
        IngestionObservation.start_extraction(EmbeddingEvidence(document).observations)
        if policy == ExtractionFailurePolicy.DISABLED.value:
            RAGDocument.add_graph_diagnostic(document, PreparationObservation.disabled())
        entities, relations = ({}, [])
        manifest, reused_sources = ([], set())
        previous_batches = (
            {
                ExtractionManifestEntry(entry).signature: entry
                for entry in RAGDocument(previous).prior_extraction_manifest
            }
            if previous
            else {}
        )
        previous_graph = (
            group_evidence(RAGDocument(previous).graph) if previous_batches else RAGPreparation.ABSENT
        )
        extraction_total = (
            (len(SplitDocument(document).chunks) + component.extraction_batch_size - 1)
            // component.extraction_batch_size
            if policy != ExtractionFailurePolicy.DISABLED.value
            else IngestionObservation.ZERO
        )
        extraction_done = IngestionObservation.ZERO

        async def extraction_progress():
            if telemetry:
                await telemetry(
                    IngestionObservation.extraction_progress(
                        EmbeddingEvidence(document).observations, extraction_done, extraction_total
                    )
                )

        if policy != ExtractionFailurePolicy.DISABLED.value:
            await extraction_progress()

        def observe_extraction(event):
            if event.code == ProviderRetryObservation.RETRY:
                IngestionObservation.record_retry(EmbeddingEvidence(document).observations)

        for offset in (
            range(0, len(SplitDocument(document).chunks), component.extraction_batch_size)
            if policy != ExtractionFailurePolicy.DISABLED.value
            else ()
        ):
            batch = SplitDocument(document).chunks[offset : offset + component.extraction_batch_size]
            signature = ExtractionBatchFingerprint.checkpoint(
                batch, component.extractor, component.extraction_options, component.extraction_prompt
            )
            reuse_signature = ExtractionBatchFingerprint.reuse(signature, component.extractor)
            key = BatchCheckpointName.graph(offset)
            saved = await progress(key, signature) if progress else RAGPreparation.ABSENT
            graph = (
                validate_extraction_checkpoint(saved, signature)
                if saved is not RAGPreparation.ABSENT
                else RAGPreparation.ABSENT
            )
            if graph is RAGPreparation.ABSENT and reuse_signature in previous_batches:
                entry = previous_batches[reuse_signature]
                graph = select_graph(
                    previous_graph,
                    ExtractionManifestEntry(entry).entity_ids,
                    batch,
                    relation_types=component.extraction_options.get(
                        ExtractionOptions.Field.RELATION_TYPES, ()
                    ),
                )
                if revision_token(graph) != ExtractionManifestEntry(entry).digest:
                    raise GraphValidationError(RAGDocument.ERROR_STORED_EXTRACTION_BATCH_INTEGRITY_MISMATCH)
                reused_sources.update((RAGChunk(chunk).identifier for chunk in batch))
                IngestionObservation.record_reused_extraction(EmbeddingEvidence(document).observations)
                if progress:
                    await progress(
                        key, signature, ExtractionCheckpoint.create(batch, component.chunk_size, graph)
                    )
            if graph is RAGPreparation.ABSENT:
                started = time.monotonic()
                try:
                    with diagnostic_scope(observe_extraction):
                        graph = validate_graph(
                            await component.extractor.extract(batch),
                            batch,
                            relation_types=component.extraction_options.get(
                                ExtractionOptions.Field.RELATION_TYPES, ()
                            ),
                        )
                except Exception as error:
                    code = error_code(error)
                    if policy != ExtractionFailurePolicy.BEST_EFFORT.value or not (
                        isinstance(error, GraphValidationError)
                        or code in RAGPreparation.BEST_EFFORT_PROVIDER_CODES
                    ):
                        raise
                    RAGDocument.mark_graph_incomplete(document)
                    RAGDocument.add_graph_diagnostic(
                        document,
                        PreparationObservation.incomplete(
                            offset,
                            code if code != ProviderErrorCode.FAILED.value else GraphValidationError.code,
                        ),
                    )
                    continue
                finally:
                    extraction_done += IngestionObservation.COUNT_INCREMENT
                    IngestionObservation.record_extraction_batch(EmbeddingEvidence(document).observations)
                    IngestionObservation.record_extraction_seconds(
                        EmbeddingEvidence(document).observations, time.monotonic() - started
                    )
                    diagnostic(
                        IngestionObservation.EXTRACTION_EVENT, **EmbeddingEvidence(document).observations
                    )
                    await extraction_progress()
                if progress:
                    await progress(
                        key, signature, ExtractionCheckpoint.create(batch, component.chunk_size, graph)
                    )
            else:
                graph = validate_graph(
                    graph,
                    batch,
                    relation_types=component.extraction_options.get(
                        ExtractionOptions.Field.RELATION_TYPES, ()
                    ),
                )
                extraction_done += IngestionObservation.COUNT_INCREMENT
                await extraction_progress()
            entities.update({GraphEntity(e).stored_id: e for e in GraphRecord(graph).entities})
            relations.extend(GraphRecord(graph).relations)
            manifest.append(
                ExtractionManifestEntry.create(
                    reuse_signature,
                    [GraphEntity(e).stored_id for e in GraphRecord(graph).entities],
                    offset,
                    len(batch),
                )
            )
        RAGDocument.record_graph(
            document,
            enrich_graph(
                validate_graph(
                    GraphRecord.create(list(entities.values()), relations),
                    SplitDocument(document).chunks,
                    relation_types=component.extraction_options.get(
                        ExtractionOptions.Field.RELATION_TYPES, ()
                    ),
                ),
                identifier,
            ),
        )
        grouped = group_evidence(RAGDocument(document).graph)
        for entry in manifest:
            batch = ExtractionManifestEntry(entry).chunks(SplitDocument(document).chunks)
            ExtractionManifestEntry.record_digest(
                entry,
                revision_token(
                    select_graph(
                        grouped,
                        ExtractionManifestEntry(entry).entity_ids,
                        batch,
                        relation_types=component.extraction_options.get(
                            ExtractionOptions.Field.RELATION_TYPES, ()
                        ),
                    )
                ),
            )
        if reused_sources:

            def edge_key(edge):
                return GraphRelation.evidence_key(edge)

            stamps = {
                edge_key(edge): GraphRelation(edge).extracted_at
                for edge in GraphRecord(RAGDocument(previous).graph).relations
                if GraphRelation(edge).source_id in reused_sources
            }
            for edge in GraphRecord(RAGDocument(document).graph).relations:
                if edge_key(edge) in stamps:
                    GraphRelation.restore_extracted_at(edge, stamps[edge_key(edge)])
        RAGDocument.record_extraction_manifest(document, manifest)
        RAGDocument.record_preparation_configuration(
            document, getattr(component, RAGConfiguration.VERSION_ATTRIBUTE, RAGPreparation.ABSENT)
        )
        RAGDocument.record_preparation_fingerprint(document, fingerprint)
        return document


class QueryFingerprint:
    IMPLEMENTATION = "implementation"
    PROJECT = "project"
    ABSENT = None

    @classmethod
    def create(cls, component, client_identity):
        return revision_token(
            {
                EmbeddingFingerprint.Field.MODEL: component._identity(),
                EmbeddingFingerprint.Field.PARAMETERS: ModelClient.fingerprint_parameters(
                    component.embedding
                ),
                EmbeddingFingerprint.Field.QUERY_OPTIONS: component.query_kwargs,
                EmbeddingFingerprint.Field.DOCUMENT_OPTIONS: component.document_kwargs,
                EmbeddingFingerprint.Field.CLIENT: type(component.embedding).__qualname__,
                cls.IMPLEMENTATION: str(client_identity() if client_identity else id(component.embedding)),
                cls.PROJECT: getattr(component, component.CACHE_NAMESPACE_ATTRIBUTE, cls.ABSENT),
            }
        )


class ExtractionBatchFingerprint:
    REQUEST = "request"
    IMPLEMENTATION = "implementation"

    @classmethod
    def checkpoint(cls, batch, extractor, options, prompt):
        return revision_token(
            {
                ExtractionCheckpoint.Field.CHUNKS: batch,
                RAGConfiguration.Model.EXTRACTOR: type(extractor).__qualname__,
                ModelClient.PARAMETERS_ATTRIBUTE: ModelClient.fingerprint_parameters(extractor),
                ExtractionOptions.KEY: options,
                RAGConfigurationVersion.PROMPT: prompt,
            }
        )

    @classmethod
    def reuse(cls, signature, extractor):
        return revision_token(
            {
                cls.REQUEST: signature,
                cls.IMPLEMENTATION: type(extractor).__module__
                + PreparationFingerprint.TYPE_SEPARATOR
                + type(extractor).__qualname__,
            }
        )


class PreparationObservation:
    DISABLED = "graph_extraction_disabled"
    INCOMPLETE = "graph_incomplete"

    @classmethod
    def disabled(cls):
        return diagnostic(cls.DISABLED).to_dict()

    @classmethod
    def incomplete(cls, offset, cause):
        return diagnostic(
            cls.INCOMPLETE, severity=DiagnosticSeverity.WARNING.value, batch_offset=offset, cause=cause
        ).to_dict()
