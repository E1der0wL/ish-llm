"""RAG 준비 단계의 제한된 청크 병렬 처리·캐시·진행 관찰. 세대 공개와 Job 소유권은 기존 서비스에 둔다."""

from llm.providers.diagnostic_values import ProviderRetryObservation

from .chunk_records import RAGChunk, RAGContent, SplitDocument
from ._client import ModelClient
from .embedding import EmbeddingModel
from .ingestion_records import (BatchCheckpointName, EmbeddingCheckpoint, EmbeddingEvidence,
                                EmbeddingFingerprint, IngestionObservation)

import asyncio
import threading
import time
import sys
from collections import OrderedDict

from llm.core.embeddings import validate_vectors
from llm.providers.runtime import diagnostic, diagnostic_scope


class VectorCache:
    """성공·검증된 벡터만 보관하는 프로세스 내 LRU. 원문/인증값은 저장하지 않는다."""

    ENTRY_OVERHEAD_BYTES = 128
    EMPTY_BYTES = 0
    MISSING = None
    OLDEST_FIRST = False
    KEY_SEPARATOR = ":"

    @classmethod
    def key(cls, fingerprint, text):
        return fingerprint + cls.KEY_SEPARATOR + RAGContent.digest(text)

    def __init__(self):
        self.entries, self.bytes = OrderedDict(), self.EMPTY_BYTES
        self.lock = threading.Lock()

    def _trim(self, maximum):
        while self.entries and self.bytes > maximum:
            key, vector = self.entries.popitem(last=self.OLDEST_FIRST)
            self.bytes -= self._size(key, vector)

    @staticmethod
    def _size(key, vector):
        return VectorCache.ENTRY_OVERHEAD_BYTES + sys.getsizeof(key) + sys.getsizeof(vector) + sum(sys.getsizeof(n) for n in vector)

    def get(self, key, maximum):
        with self.lock:
            self._trim(maximum)
            if key not in self.entries:
                return self.MISSING
            vector = self.entries.pop(key)
            self.entries[key] = vector
            return list(vector)

    def put(self, key, vector, maximum):
        with self.lock:
            if key in self.entries:
                self.bytes -= self._size(key, self.entries.pop(key))
            self.entries[key] = tuple(vector)
            self.bytes += self._size(key, self.entries[key])
            self._trim(maximum)


class EmbeddingExecution:
    ABSENT = None
    MINIMUM_CONCURRENCY = 1
    MINIMUM_CHUNK_SIZE = 1
    DISABLED_CACHE_BYTES = 0
    STANDALONE_CACHE_NAMESPACE = "standalone"
    FIRST_RESULT = 0
    SINGLE_RESULT = 1
    COUNT_INCREMENT = 1
    COLLECT_CANCELLED = True
    WORKER_FORMAT = "rag-embedding-{index}"
    INVALID_CONCURRENCY = "embedding_concurrency must be a positive integer"
    INVALID_CHUNK_SIZE = "Checkpoint source requires a positive chunk_size"
    STORED_COUNT_MISMATCH = "Stored chunk/vector count mismatch"
    CONFLICTING_STORED_VECTORS = "Stored identical chunks have conflicting vectors"
    CONFLICTING_CHECKPOINTS = "Duplicate chunk checkpoints have conflicting vectors"
    INVALID_RESULT = "Single chunk requires one vector"


async def prepare_vectors(component, document, *, previous=EmbeddingExecution.ABSENT, progress=EmbeddingExecution.ABSENT, telemetry=EmbeddingExecution.ABSENT):
    """고정 개수 worker가 단일 청크 요청을 수행하고 원래 위치에 결과를 복원한다.

    progress는 서비스가 소유하는 원자적 checkpoint 읽기/쓰기 경계다.
    실패 시 새 작업 배정을 멈추고 진행 중인 성공 결과는 보존한다.
    취소 시 worker를 모두 취소·회수한 뒤 반환하므로 미완료 세대는 공개되지 않는다.
    """
    from .component import RAGComponent

    concurrency = component.embedding_concurrency
    if type(concurrency) is not int or concurrency < EmbeddingExecution.MINIMUM_CONCURRENCY:
        raise ValueError(EmbeddingExecution.INVALID_CONCURRENCY)
    texts = [RAGChunk(chunk).text for chunk in SplitDocument(document).chunks]
    if progress:
        if type(component.chunk_size) is not int or component.chunk_size < EmbeddingExecution.MINIMUM_CHUNK_SIZE:
            raise ValueError(EmbeddingExecution.INVALID_CHUNK_SIZE)
        for chunk in SplitDocument(document).chunks:
            RAGChunk.validate(chunk)
    fingerprint = EmbeddingFingerprint.create(component._identity(), ModelClient.fingerprint_parameters(component.embedding),
        component.document_kwargs, component.query_kwargs, type(component.embedding).__qualname__)
    identity = getattr(component.embedding, ModelClient.cache_identity.__name__, EmbeddingExecution.ABSENT)
    cache_prefix = (getattr(component, RAGComponent.CACHE_NAMESPACE_ATTRIBUTE, EmbeddingExecution.STANDALONE_CACHE_NAMESPACE) + fingerprint
                    + str(identity() if identity else id(component.embedding)))
    maximum = component.embedding_cache_max_bytes
    # No configured cache capacity means the optional cache is inactive.
    maximum = EmbeddingExecution.DISABLED_CACHE_BYTES if maximum is EmbeddingExecution.ABSENT else maximum
    dimensions = getattr(component, RAGComponent.EMBEDDING_DIMENSIONS_ATTRIBUTE, EmbeddingExecution.ABSENT)

    def checked(vector):
        nonlocal dimensions
        validate = getattr(component.embedding, EmbeddingModel.validate_vectors.__name__, EmbeddingExecution.ABSENT)
        if validate:
            vector = validate([vector], **component.document_kwargs)[EmbeddingExecution.FIRST_RESULT]
        vector = validate_vectors([vector], dimensions=dimensions)[EmbeddingExecution.FIRST_RESULT]
        dimensions = len(vector)
        return vector

    reused = {}
    if previous and EmbeddingEvidence(previous).fingerprint == fingerprint:
        vectors = validate_vectors(EmbeddingEvidence(previous).vectors, dimensions=EmbeddingEvidence(previous).profile.dimensions)
        if len(vectors) != len(SplitDocument(previous).chunks):
            raise ValueError(EmbeddingExecution.STORED_COUNT_MISMATCH)
        for chunk, vector in zip(SplitDocument(previous).chunks, vectors):
            key = VectorCache.key(fingerprint, RAGChunk(chunk).text)
            if key in reused and reused[key] != vector:
                raise ValueError(EmbeddingExecution.CONFLICTING_STORED_VECTORS)
            reused[key] = vector
    positions = {}
    for position, text in enumerate(texts):
        positions.setdefault(text, []).append(position)
    vectors = [EmbeddingExecution.ABSENT] * len(texts)
    stats = IngestionObservation.embedding(len(texts), concurrency)
    started, telemetry_lock = time.monotonic(), asyncio.Lock()

    def observe(event):
        if event.code == ProviderRetryObservation.RETRY:
            stats[IngestionObservation.Field.RETRIES] += EmbeddingExecution.COUNT_INCREMENT

    async def publish():
        async with telemetry_lock:
            stats[IngestionObservation.Field.SECONDS] = time.monotonic() - started
            diagnostic(IngestionObservation.EMBEDDING_EVENT, **stats)
            if telemetry:
                await telemetry(IngestionObservation.embedding_progress(stats))

    async def embed_chunk(text, ordinals):
        text_hash = RAGContent.digest(text)
        signature = EmbeddingCheckpoint.signature(fingerprint, text_hash)
        saved_positions, vector = set(), EmbeddingExecution.ABSENT
        if progress:
            for position in ordinals:
                saved = await progress(BatchCheckpointName.embedding(position), signature)
                if saved is EmbeddingExecution.ABSENT:
                    continue
                EmbeddingCheckpoint.validate(saved, signature)
                EmbeddingCheckpoint.require_identity(saved, fingerprint, text_hash)
                candidate = checked(EmbeddingCheckpoint(saved).vector)
                if vector is not EmbeddingExecution.ABSENT and candidate != vector:
                    raise ValueError(EmbeddingExecution.CONFLICTING_CHECKPOINTS)
                vector = candidate
                saved_positions.add(position)
            stats[IngestionObservation.Field.CHECKPOINT_REUSE] += len(saved_positions)
        if vector is EmbeddingExecution.ABSENT:
            vector = reused.get(VectorCache.key(fingerprint, text))
            if vector is not EmbeddingExecution.ABSENT:
                stats[IngestionObservation.Field.REUSED] += len(ordinals)
            else:
                vector = component.vector_cache.get(VectorCache.key(cache_prefix, text), maximum)
                if vector is not EmbeddingExecution.ABSENT:
                    stats[IngestionObservation.Field.CACHED] += len(ordinals)
        if vector is EmbeddingExecution.ABSENT:
            stats[IngestionObservation.Field.REQUESTED] += EmbeddingExecution.COUNT_INCREMENT
            stats[IngestionObservation.Field.REUSED] += len(ordinals) - EmbeddingExecution.COUNT_INCREMENT
            stats[IngestionObservation.Field.ACTIVE] += EmbeddingExecution.COUNT_INCREMENT
            stats[IngestionObservation.Field.PEAK] = max(stats[IngestionObservation.Field.PEAK], stats[IngestionObservation.Field.ACTIVE])
            try:
                result = await component.embed([text])
                if not isinstance(result, list) or len(result) != EmbeddingExecution.SINGLE_RESULT:
                    raise ValueError(EmbeddingExecution.INVALID_RESULT)
                vector = checked(result[EmbeddingExecution.FIRST_RESULT])
            finally:
                stats[IngestionObservation.Field.ACTIVE] -= EmbeddingExecution.COUNT_INCREMENT
        else:
            vector = checked(vector)
        # provider index는 읽지 않는다. 검증된 벡터와 앱이 소유한 ordinal만 연결한다.
        for position in ordinals:
            if progress and position not in saved_positions:
                await progress(BatchCheckpointName.embedding(position), signature, EmbeddingCheckpoint.validate(
                    EmbeddingCheckpoint.create(fingerprint, text_hash, vector,
                        SplitDocument(document).chunks[position], component.chunk_size), signature))
            vectors[position] = list(vector)
            stats[IngestionObservation.Field.COMPLETED] += EmbeddingExecution.COUNT_INCREMENT
        component.vector_cache.put(VectorCache.key(cache_prefix, text), vector, maximum)
        await publish()

    pending, errors = iter(positions.items()), []

    async def worker():
        while not errors:
            item = next(pending, EmbeddingExecution.ABSENT)
            if item is EmbeddingExecution.ABSENT:
                return
            try:
                await embed_chunk(*item)
            except Exception as error:
                errors.append(error)
                return

    await publish()
    with diagnostic_scope(observe):
        workers = [asyncio.create_task(worker(), name=EmbeddingExecution.WORKER_FORMAT.format(index=i))
                   for i in range(min(concurrency, len(positions)))]
        try:
            await asyncio.gather(*workers)
        except BaseException:
            for task in workers:
                task.cancel()
            from llm.services.infrastructure.storage import drain_on_cancel
            await drain_on_cancel(asyncio.gather(*workers, return_exceptions=EmbeddingExecution.COLLECT_CANCELLED))
            raise
        finally:
            await publish()
    if errors:
        raise errors[EmbeddingExecution.FIRST_RESULT]
    document[EmbeddingEvidence.Field.VECTORS] = validate_vectors(vectors, dimensions=dimensions)
    document[EmbeddingEvidence.Field.FINGERPRINT] = fingerprint
    document[EmbeddingEvidence.Field.INGESTION] = stats
