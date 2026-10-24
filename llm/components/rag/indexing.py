"""불변 세대별 Chroma 색인. 연결은 작업 단위로 열고 닫아 복제·삭제를 허용한다."""

from .ingestion_records import EmbeddingEvidence

from .chunk_records import RAGChunk, SplitDocument

from contextlib import contextmanager


class ChunkIndexMetadata:
    DOCUMENT_ID = RAGChunk.Field.DOCUMENT_ID
    SECTION_ID = RAGChunk.Field.SECTION_ID

    @classmethod
    def project(cls, identifier, chunk):
        return {cls.DOCUMENT_ID: identifier, cls.SECTION_ID: RAGChunk(chunk).section_id}


@contextmanager
def collection(path, *, create=False):
    import chromadb
    client = chromadb.PersistentClient(str(path))
    try:
        if create:
            value = client.create_collection("documents", embedding_function=None)
        else:
            value = client.get_collection("documents", embedding_function=None)
        yield value
    finally:
        client.close()


def build_index(path, documents: dict, *, batch_size=None) -> None:
    """저장된 벡터를 재사용하여 새로운 검색 세대를 만든다. 모델은 호출하지 않는다."""
    with collection(path, create=True) as index:
        for document in documents.values():
            chunks = SplitDocument(document).chunks
            size = len(chunks) if batch_size is None else batch_size
            for offset in range(0, len(chunks), size):
                batch = chunks[offset:offset + size]
                index.add(ids=[RAGChunk(c).identifier for c in batch], documents=[RAGChunk(c).text for c in batch],
                          embeddings=EmbeddingEvidence(document).vectors[offset:offset + size],
                          metadatas=[ChunkIndexMetadata.project(document["id"], c)
                                     for c in batch])


def update_index(path, before, documents, *, batch_size=None):
    """복사한 불변 세대에서 바뀐 문서만 갱신한다. 기존 검색 세대는 수정하지 않는다."""
    changed = {key for key in before.keys() | documents.keys()
               if key not in before or key not in documents or any(
                   before[key][field] != documents[key][field] for field in (SplitDocument.Field.CHUNKS, EmbeddingEvidence.Field.VECTORS))}
    if not changed:
        return
    with collection(path) as index:
        for identifier in changed:
            if identifier in before:
                index.delete(ids=[RAGChunk(c).identifier for c in SplitDocument(before[identifier]).chunks])
            document = documents.get(identifier)
            if document is None:
                continue
            size = len(SplitDocument(document).chunks) if batch_size is None else batch_size
            for offset in range(0, len(SplitDocument(document).chunks), size):
                batch = SplitDocument(document).chunks[offset:offset + size]
                index.add(ids=[RAGChunk(c).identifier for c in batch], documents=[RAGChunk(c).text for c in batch],
                    embeddings=EmbeddingEvidence(document).vectors[offset:offset + size],
                    metadatas=[ChunkIndexMetadata.project(identifier, c) for c in batch])
