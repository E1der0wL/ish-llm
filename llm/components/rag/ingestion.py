"""RAG 준비 단계의 유한 배치·캐시·진행 관찰. 세대 공개와 Job 소유권은 기존 서비스에 둔다."""

import hashlib
import threading
import time
import sys
from collections import OrderedDict

from llm.providers.embeddings import validate_vectors
from llm.providers.requests import error_code
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


def batches(texts, size, max_chars):
    """청크를 의미 없이 자르지 않는다. 한 청크가 문자 한도를 넘으면 호출 전에 거부한다."""
    offset, batch, chars = 0, [], 0
    for text in texts:
        if max_chars is not None and len(text) > max_chars:
            raise ValueError("Chunk exceeds embedding max_batch_chars; reduce chunk_size")
        if batch and (len(batch) >= size or max_chars is not None and chars + len(text) > max_chars):
            yield offset, batch
            offset += len(batch)
            batch, chars = [], 0
        batch.append(text)
        chars += len(text)
    if batch:
        yield offset, batch


async def prepare_vectors(component, document, *, previous=None, progress=None, telemetry=None):
    """완료 batch는 재사용하며 transient 오류만 제한 깊이 분할한다."""
    options = component.batching_options
    texts = [chunk["text"] for chunk in document["chunks"]]
    fingerprint = revision_token({"model": component._identity(),
        "params": getattr(component.embedding, "params", {}), "kwargs": component.document_kwargs,
        "client": type(component.embedding).__qualname__})
    # 주입 함수 교체는 설정 JSON에 나타나지 않는다. 프로세스 캐시만 함수 identity로 분리한다.
    cache_prefix = (getattr(component, "_cache_namespace", "standalone") + fingerprint
                    + str(id(getattr(component.embedding, "_call_fn", type(component.embedding))))
                    + str(id(component.embedding.__dict__.get("embed", type(component.embedding).embed))))
    maximum = options["cache_max_bytes"]
    reused = {}
    if previous and previous.get("embedding_fingerprint") == fingerprint:
        vectors = validate_vectors(previous["vectors"], dimensions=previous["profile"]["dimensions"])
        if len(vectors) != len(previous["chunks"]):
            raise ValueError("Stored chunk/vector count mismatch")
        reused = {vector_key(fingerprint, chunk["text"]): vector for chunk, vector in zip(previous["chunks"], vectors)}
    plan = list(batches(texts, min(component.embedding_batch_size, options["max_batch_size"]), options["max_batch_chars"]))
    stats = {"chunks_total": len(texts), "total_chars": sum(map(len, texts)),
             "embedding_batches_total": len(plan), "embedding_batches_completed": 0,
             "embedding_seconds": 0.0, "cache_hits": 0, "checkpoint_reuse": 0, "batch_splits": 0}
    stats.update(provider_retries=0, index_normalizations=0)

    def observe(event):
        if event.code == "provider_retry":
            stats["provider_retries"] += 1
        elif event.code == "embedding_index_normalized":
            stats["index_normalizations"] += 1

    async def publish(**values):
        stats.update(values)
        diagnostic("rag_ingestion_progress", **stats)
        if telemetry:
            await telemetry(dict(stats))

    async def split_batch(key, batch, depth):
        stats["batch_splits"] += 1
        middle = len(batch) // 2
        left = await embed_batch(key + "_l", batch[:middle], depth + 1)
        right = await embed_batch(key + "_r", batch[middle:], depth + 1)
        return validate_vectors(left + right)

    async def embed_batch(key, batch, depth=0):
        signature = revision_token({"fingerprint": fingerprint, "texts": batch})
        saved = await progress(key, signature) if progress else None
        if saved is not None:
            if len(saved) != len(batch):
                raise ValueError("Checkpoint embedding count mismatch")
            stats["checkpoint_reuse"] += len(batch)
            return validate_vectors(saved)
        split = await progress(key + "_split", signature) if progress else None
        if split is not None:
            if split != {"middle": len(batch) // 2} or len(batch) < 2 or depth >= options["max_split_depth"]:
                raise ValueError("Invalid or changed adaptive batch checkpoint; enqueue a new job")
            # 이미 분할한 큰 요청을 재호출하지 않고 완료된 하위 결과부터 복원한다.
            result = await split_batch(key, batch, depth)
            await progress(key, signature, result)
            return result
        result, missing, positions = [None] * len(batch), [], {}
        for position, text in enumerate(batch):
            key_hash = vector_key(fingerprint, text)
            vector = reused.get(key_hash)
            if vector is None:
                vector = component.vector_cache.get(vector_key(cache_prefix, text), maximum)
            if vector is not None:
                result[position] = vector
                stats["cache_hits"] += 1
            else:
                if text not in positions:
                    missing.append(text)
                    positions[text] = []
                positions[text].append(position)
        if missing:
            try:
                vectors = await component.embed(missing)
            except Exception as error:
                if (error_code(error) not in ("provider_timeout", "provider_unavailable")
                        or depth >= options["max_split_depth"] or len(batch) < 2):
                    raise
                if progress:
                    await progress(key + "_split", signature, {"middle": len(batch) // 2})
                result = await split_batch(key, batch, depth)
            else:
                for text, vector in zip(missing, vectors):
                    component.vector_cache.put(vector_key(cache_prefix, text), vector, maximum)
                    for position in positions[text]:
                        result[position] = vector
        result = validate_vectors(result)
        if progress:
            await progress(key, signature, result)
        return result

    await publish()
    vectors = []
    for offset, batch in plan:
        started = time.monotonic()
        try:
            with diagnostic_scope(observe):
                vectors.extend(await embed_batch(f"embedding_{offset}", batch))
            stats["embedding_batches_completed"] += 1
        finally:
            elapsed = time.monotonic() - started
            stats["embedding_seconds"] += elapsed
            await publish(batch_items=len(batch), batch_chars=sum(map(len, batch)), batch_seconds=elapsed)
    # 서로 다른 배치/캐시 사이의 차원 불일치도 공개 전에 거부한다.
    document["vectors"] = validate_vectors(vectors)
    document["embedding_fingerprint"] = fingerprint
    document["ingestion"] = stats
