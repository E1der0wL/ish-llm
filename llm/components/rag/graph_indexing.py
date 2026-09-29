"""Kuzu 연결과 출처별 관계 색인. 문서 삭제 시 다른 문서의 근거를 보존한다."""

from contextlib import contextmanager
import json


def relation_weights(documents):
    """문서별 원본은 보존하고 코퍼스의 서로 다른 근거 청크 수를 색인 가중치로 쓴다."""
    support = {}
    for identifier, document in documents.items():
        for edge in document["graph"]["relations"]:
            key = (edge["source"], edge["type"], edge["target"])
            support.setdefault(key, set()).add((identifier, edge["source_id"]))
    return {key: len(sources) for key, sources in support.items()}


def insert_relations(conn, document, weights):
    for edge in document["graph"]["relations"]:
        rows(conn, "MATCH (a:Entity),(b:Entity) WHERE a.id=$source AND b.id=$target "
             "CREATE (a)-[:Link {kind:$kind,document_id:$document_id,source_id:$source_id,evidence:$evidence,"
             "weight:$weight,metadata:$metadata,extracted_at:$extracted_at}]->(b)",
             {"source": edge["source"], "target": edge["target"], "kind": edge["type"],
              "document_id": document["id"], "source_id": edge["source_id"], "evidence": edge["evidence"],
              "weight": weights[(edge["source"], edge["type"], edge["target"])],
              "metadata": json.dumps(edge["metadata"], ensure_ascii=False, allow_nan=False),
              "extracted_at": edge["extracted_at"]})


@contextmanager
def connection(path, *, options=None):
    import kuzu
    db = kuzu.Database(str(path), **(options or {"buffer_pool_size": 64 * 1024 * 1024, "max_num_threads": 2}))
    try:
        conn = kuzu.Connection(db)
        try:
            yield conn
        finally:
            conn.close()
    finally:
        db.close()


def rows(conn, query: str, parameters=None) -> list:
    result = conn.execute(query, parameters or {})
    try:
        output = []
        while result.has_next():
            output.append(result.get_next())
        return output
    finally:
        result.close()


def build_graph(path, documents: dict, *, options=None) -> None:
    with connection(path, options=options) as conn:
        rows(conn, "CREATE NODE TABLE Entity(id STRING PRIMARY KEY, name STRING)")
        rows(conn, "CREATE REL TABLE Link(FROM Entity TO Entity, kind STRING, document_id STRING, source_id STRING, evidence STRING, weight INT64, metadata STRING, extracted_at STRING)")
        entities = {entity["id"]: entity for doc in documents.values() for entity in doc["graph"]["entities"]}
        for entity in entities.values():
            rows(conn, "CREATE (:Entity {id:$id,name:$name})", entity)
        weights = relation_weights(documents)
        for document in documents.values():
            insert_relations(conn, document, weights)


def update_graph(path, before, documents, *, options=None):
    """변경 문서의 관계만 교체하고 여러 문서가 공유하는 엔티티는 보존한다."""
    changed = {key for key in before.keys() | documents.keys() if before.get(key) != documents.get(key)}
    entities = {e["id"]: e for d in documents.values() for e in d["graph"]["entities"]}
    old = {e["id"] for d in before.values() for e in d["graph"]["entities"]}
    weights = relation_weights(documents)
    old_weights = relation_weights(before)
    with connection(path, options=options) as conn:
        for identifier in changed:
            rows(conn, "MATCH ()-[r:Link]->() WHERE r.document_id=$id DELETE r", {"id": identifier})
        for identifier in old - entities.keys():
            rows(conn, "MATCH (n:Entity) WHERE n.id=$id DETACH DELETE n", {"id": identifier})
        for entity in entities.values():
            rows(conn, "MERGE (n:Entity {id:$id}) SET n.name=$name", entity)
        for identifier in changed & documents.keys():
            insert_relations(conn, documents[identifier], weights)
        # 문서 수정/삭제는 남아 있는 동일 관계의 가중치도 낮춘다. 재등록 횟수는 누적하지 않는다.
        for (source, kind, target), weight in weights.items():
            if old_weights.get((source, kind, target)) != weight:
                rows(conn, "MATCH (a:Entity)-[r:Link]->(b:Entity) "
                     "WHERE a.id=$source AND b.id=$target AND r.kind=$kind SET r.weight=$weight",
                     {"source": source, "target": target, "kind": kind, "weight": weight})
