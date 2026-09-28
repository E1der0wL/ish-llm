"""Kuzu 연결과 출처별 관계 색인. 문서 삭제 시 다른 문서의 근거를 보존한다."""

from contextlib import contextmanager


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
        rows(conn, "CREATE REL TABLE Link(FROM Entity TO Entity, kind STRING, document_id STRING, source_id STRING, evidence STRING)")
        entities = {entity["id"]: entity for doc in documents.values() for entity in doc["graph"]["entities"]}
        for entity in entities.values():
            rows(conn, "CREATE (:Entity {id:$id,name:$name})", entity)
        for document in documents.values():
            for edge in document["graph"]["relations"]:
                rows(conn, "MATCH (a:Entity),(b:Entity) WHERE a.id=$source AND b.id=$target "
                     "CREATE (a)-[:Link {kind:$kind,document_id:$document_id,source_id:$source_id,evidence:$evidence}]->(b)",
                     {"source": edge["source"], "target": edge["target"], "kind": edge["type"],
                      "document_id": document["id"], "source_id": edge["source_id"], "evidence": edge["evidence"]})


def update_graph(path, before, documents, *, options=None):
    """변경 문서의 관계만 교체하고 여러 문서가 공유하는 엔티티는 보존한다."""
    changed = {key for key in before.keys() | documents.keys() if before.get(key) != documents.get(key)}
    entities = {e["id"]: e for d in documents.values() for e in d["graph"]["entities"]}
    old = {e["id"] for d in before.values() for e in d["graph"]["entities"]}
    with connection(path, options=options) as conn:
        for identifier in changed:
            rows(conn, "MATCH ()-[r:Link]->() WHERE r.document_id=$id DELETE r", {"id": identifier})
        for identifier in old - entities.keys():
            rows(conn, "MATCH (n:Entity) WHERE n.id=$id DETACH DELETE n", {"id": identifier})
        for entity in entities.values():
            rows(conn, "MERGE (n:Entity {id:$id}) SET n.name=$name", entity)
        for identifier in changed & documents.keys():
            for edge in documents[identifier]["graph"]["relations"]:
                rows(conn, "MATCH (a:Entity),(b:Entity) WHERE a.id=$source AND b.id=$target "
                     "CREATE (a)-[:Link {kind:$kind,document_id:$document_id,source_id:$source_id,evidence:$evidence}]->(b)",
                     {"source": edge["source"], "target": edge["target"], "kind": edge["type"],
                      "document_id": identifier, "source_id": edge["source_id"], "evidence": edge["evidence"]})
