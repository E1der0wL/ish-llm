"""RAG 준비 단계의 제한된 청크 병렬 처리·캐시·진행 관찰. 세대 공개와 Job 소유권은 기존 서비스에 둔다."""

import asyncio
import hashlib
import threading
import time
import sys
from collections import OrderedDict

from llm.providers.embeddings import validate_vectors
from llm.providers.runtime import diagnostic, diagnostic_scope
from llm.services.infrastructure.storage import revision_token


class VectorCache:
    """성공·검증된 벡터만 보관하는 프로세스 내 LRU. 원문/인증값은 저장하지 않는다."""

    def __init__(self):
        self.entries, self.bytes = OrderedDict(), 0
        self.lock = threading.Lock()

    def _trim(self, maximum):
        while self.entries and self.bytes > maximum:
            key, vector = self.entries.popitem(last=False)
            self.bytes -= self._size(key, vector)

    @staticmethod
    def _size(key, vector):
        return 128 + sys.getsizeof(key) + sys.getsizeof(vector) + sum(sys.getsizeof(n) for n in vector)

    def get(self, key, maximum):
        with self.lock:
            self._trim(maximum)
            if key not in self.entries:
                return None
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


def vector_key(fingerprint, text):
    return fingerprint + ":" + hashlib.sha256(text.encode("utf-8")).hexdigest()


async def prepare_vectors(component, document, *, previous=None, progress=None, telemetry=None):
    """고정 개수 worker가 단일 청크 요청을 수행하고 원래 위치에 결과를 복원한다.

    progress는 서비스가 소유하는 원자적 checkpoint 읽기/쓰기 경계다.
    실패 시 새 작업 배정을 멈추고 진행 중인 성공 결과는 보존한다.
    취소 시 worker를 모두 취소·회수한 뒤 반환하므로 미완료 세대는 공개되지 않는다.
    """
    concurrency = component.embedding_concurrency
    if type(concurrency) is not int or not 1 <= concurrency <= 32:
        raise ValueError("embedding_concurrency must be an integer from 1 to 32")
    texts = [chunk["text"] for chunk in document["chunks"]]
    fingerprint = revision_token({"contract": "single-chunk-v1", "model": component._identity(),
        "params": getattr(component.embedding, "params", {}), "document_kwargs": component.document_kwargs,
        "query_kwargs": component.query_kwargs, "client": type(component.embedding).__qualname__})
    cache_prefix = (getattr(component, "_cache_namespace", "standalone") + fingerprint
        + str(id(getattr(component.embedding, "_call_fn", type(component.embedding))))
        + str(id(component.embedding.__dict__.get("embed", type(component.embedding).embed))))
    maximum = component.embedding_cache_max_bytes
    dimensions = getattr(component, "_embedding_dimensions", None)
    if dimensions is None:
        dimensions = {**getattr(component.embedding, "params", {}), **component.document_kwargs}.get("dimensions")

    def checked(vector):
        nonlocal dimensions
        vector = validate_vectors([vector], dimensions=dimensions)[0]
        dimensions = len(vector)
        return vector

    reused = {}
    if previous and previous.get("embedding_fingerprint") == fingerprint:
        vectors = validate_vectors(previous["vectors"], dimensions=previous["profile"]["dimensions"])
        if len(vectors) != len(previous["chunks"]):
            raise ValueError("Stored chunk/vector count mismatch")
        for chunk, vector in zip(previous["chunks"], vectors):
            key = vector_key(fingerprint, chunk["text"])
            if key in reused and reused[key] != vector:
                raise ValueError("Stored identical chunks have conflicting vectors")
            reused[key] = vector
    positions = {}
    for position, text in enumerate(texts):
        positions.setdefault(text, []).append(position)
    vectors = [None] * len(texts)
    stats = dict(chunks_total=len(texts), chunks_completed=0, chunks_reused=0, chunks_cached=0,
                 chunks_requested=0, embedding_concurrency=concurrency, embedding_seconds=0.0,
                 provider_retries=0, checkpoint_reuse=0, active_embedding_requests=0,
                 peak_embedding_requests=0)
    started, telemetry_lock = time.monotonic(), asyncio.Lock()

    def observe(event):
        if event.code == "provider_retry":
            stats["provider_retries"] += 1

    async def publish():
        async with telemetry_lock:
            stats["embedding_seconds"] = time.monotonic() - started
            diagnostic("rag_ingestion_progress", **stats)
            if telemetry:
                await telemetry(dict(stats))

    async def embed_chunk(text, ordinals):
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        signature = revision_token({"fingerprint": fingerprint, "text_hash": text_hash})
        saved_positions, vector = set(), None
        if progress:
            for position in ordinals:
                saved = await progress(f"embedding_{position}", signature)
                if saved is None:
                    continue
                if (not isinstance(saved, dict) or saved.get("fingerprint") != fingerprint
                        or saved.get("text_hash") != text_hash):
                    raise ValueError("Chunk checkpoint identity mismatch")
                candidate = checked(saved.get("vector"))
                if vector is not None and candidate != vector:
                    raise ValueError("Duplicate chunk checkpoints have conflicting vectors")
                vector = candidate
                saved_positions.add(position)
            stats["checkpoint_reuse"] += len(saved_positions)
        if vector is None:
            vector = reused.get(vector_key(fingerprint, text))
            if vector is not None:
                stats["chunks_reused"] += len(ordinals)
            else:
                vector = component.vector_cache.get(vector_key(cache_prefix, text), maximum)
                if vector is not None:
                    stats["chunks_cached"] += len(ordinals)
        if vector is None:
            stats["chunks_requested"] += 1
            stats["chunks_reused"] += len(ordinals) - 1
            stats["active_embedding_requests"] += 1
            stats["peak_embedding_requests"] = max(stats["peak_embedding_requests"], stats["active_embedding_requests"])
            try:
                result = await component.embed([text])
                if not isinstance(result, list) or len(result) != 1:
                    raise ValueError("Single chunk requires one vector")
                vector = checked(result[0])
            finally:
                stats["active_embedding_requests"] -= 1
        else:
            vector = checked(vector)
        # provider index는 읽지 않는다. 검증된 벡터와 앱이 소유한 ordinal만 연결한다.
        for position in ordinals:
            if progress and position not in saved_positions:
                await progress(f"embedding_{position}", signature,
                    {"fingerprint": fingerprint, "text_hash": text_hash, "vector": vector})
            vectors[position] = list(vector)
            stats["chunks_completed"] += 1
        component.vector_cache.put(vector_key(cache_prefix, text), vector, maximum)
        await publish()

    pending, errors = iter(positions.items()), []

    async def worker():
        while not errors:
            item = next(pending, None)
            if item is None:
                return
            try:
                await embed_chunk(*item)
            except Exception as error:
                errors.append(error)
                return

    await publish()
    with diagnostic_scope(observe):
        workers = [asyncio.create_task(worker(), name=f"rag-embedding-{i}")
                   for i in range(min(concurrency, len(positions)))]
        try:
            await asyncio.gather(*workers)
        except BaseException:
            for task in workers:
                task.cancel()
            from llm.services.infrastructure.storage import drain_on_cancel
            await drain_on_cancel(asyncio.gather(*workers, return_exceptions=True))
            raise
        finally:
            await publish()
    if errors:
        raise errors[0]
    document["vectors"] = validate_vectors(vectors, dimensions=dimensions)
    document["embedding_fingerprint"] = fingerprint
    document["ingestion"] = stats
