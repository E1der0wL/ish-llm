"""불변 세대별 Chroma 색인. 연결은 작업 단위로 열고 닫아 복제·삭제를 허용한다."""

from contextlib import contextmanager


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
            chunks = document["chunks"]
            size = len(chunks) if batch_size is None else batch_size
            for offset in range(0, len(chunks), size):
                batch = chunks[offset:offset + size]
                index.add(ids=[c["id"] for c in batch], documents=[c["text"] for c in batch],
                          embeddings=document["vectors"][offset:offset + size],
                          metadatas=[{"document_id": document["id"], "section_id": c["section_id"]}
                                     for c in batch])


def update_index(path, before, documents, *, batch_size=None):
    """복사한 불변 세대에서 바뀐 문서만 갱신한다. 기존 검색 세대는 수정하지 않는다."""
    changed = {key for key in before.keys() | documents.keys() if before.get(key) != documents.get(key)}
    with collection(path) as index:
        for identifier in changed:
            if identifier in before:
                index.delete(ids=[c["id"] for c in before[identifier]["chunks"]])
            document = documents.get(identifier)
            if document is None:
                continue
            size = len(document["chunks"]) if batch_size is None else batch_size
            for offset in range(0, len(document["chunks"]), size):
                batch = document["chunks"][offset:offset + size]
                index.add(ids=[c["id"] for c in batch], documents=[c["text"] for c in batch],
                    embeddings=document["vectors"][offset:offset + size],
                    metadatas=[{"document_id": identifier, "section_id": c["section_id"]} for c in batch])
