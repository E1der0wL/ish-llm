"""Kuzu 연결과 출처별 관계 색인. 문서 삭제 시 다른 문서의 근거를 보존한다."""

from contextlib import contextmanager
from .graph_records import GraphEntity, GraphRelation, GraphRecord
from .document_records import RAGDocument
import json


class GraphQueries:
    """Kuzu schema, query text and parameter names; no traversal or admission policy."""
    KIND = "kind"
    EMPTY_ORDER = ""
    LIMIT_FORMAT = " LIMIT {limit}"
    INSERT_RELATION = 'MATCH (a:Entity),(b:Entity) WHERE a.id=$source AND b.id=$target CREATE (a)-[:Link {kind:$kind,document_id:$document_id,source_id:$source_id,evidence:$evidence,weight:$weight,metadata:$metadata,extracted_at:$extracted_at}]->(b)'
    CREATE_ENTITIES = 'CREATE NODE TABLE Entity(id STRING PRIMARY KEY, name STRING)'
    CREATE_RELATIONS = 'CREATE REL TABLE Link(FROM Entity TO Entity, kind STRING, document_id STRING, source_id STRING, evidence STRING, weight INT64, metadata STRING, extracted_at STRING)'
    INSERT_ENTITY = 'CREATE (:Entity {id:$id,name:$name})'
    DELETE_DOCUMENT = 'MATCH ()-[r:Link]->() WHERE r.document_id=$id DELETE r'
    DELETE_ENTITY = 'MATCH (n:Entity) WHERE n.id=$id DETACH DELETE n'
    MERGE_ENTITY = 'MERGE (n:Entity {id:$id}) SET n.name=$name'
    UPDATE_SUPPORT = 'MATCH (a:Entity)-[r:Link]->(b:Entity) WHERE a.id=$source AND b.id=$target AND r.kind=$kind SET r.weight=$weight'
    ORDER_PREFIX = ' ORDER BY '
    ORDER_SUPPORT = 'r.weight DESC,'
    ORDER_SOURCE = 'r.document_id,r.source_id,'
    ORDER_SOURCE_ID = 'a.id,'
    ORDER_TAIL = 'b.id,r.kind,r.evidence'
    COLUMNS = ' RETURN a.id,a.name,r.kind,b.id,b.name,r.document_id,r.source_id,r.evidence,r.weight,r.metadata,r.extracted_at'
    SELECT_NAME = 'MATCH (e:Entity) WHERE e.name=$name RETURN e.id,e.name'
    SELECT_SOURCE = 'MATCH (a:Entity)-[r:Link]->(b:Entity) WHERE r.source_id=$source_id'
    SELECT_OUTGOING = 'MATCH (a:Entity)-[r:Link]->(b:Entity) WHERE a.id=$id'
    SELECT_TARGET_COLUMNS = 'MATCH (a:Entity)-[r:Link]->(b:Entity) WHERE a.id=$id RETURN r.kind,b.id,b.name,r.document_id,r.source_id,r.evidence,r.weight,r.metadata,r.extracted_at '

    @classmethod
    def relation_parameters(cls, document_id, edge, weights):
        return {GraphRelation.Field.SOURCE: edge[GraphRelation.Field.SOURCE], GraphRelation.Field.TARGET: edge[GraphRelation.Field.TARGET], GraphQueries.KIND: edge[GraphRelation.Field.TYPE],
              GraphRelation.Field.DOCUMENT_ID: document_id, GraphRelation.Field.SOURCE_ID: edge[GraphRelation.Field.SOURCE_ID], GraphRelation.Field.EVIDENCE: edge[GraphRelation.Field.EVIDENCE],
              GraphRelation.Field.WEIGHT: weights[GraphRelation.triple(edge)],
              GraphRelation.Field.METADATA: json.dumps(edge[GraphRelation.Field.METADATA], ensure_ascii=GraphStore.ASCII_ONLY, allow_nan=GraphStore.ALLOW_NONFINITE),
              GraphRelation.Field.EXTRACTED_AT: edge[GraphRelation.Field.EXTRACTED_AT]}

    @classmethod
    def limit(cls, count):
        return cls.LIMIT_FORMAT.format(limit=count)


class GraphStore:
    FILE_NAME = "graph.kuzu"
    ABSENT = None
    ASCII_ONLY = False
    ALLOW_NONFINITE = False


def relation_weights(documents):
    """문서별 원본은 보존하고 코퍼스의 서로 다른 근거 청크 수를 색인 가중치로 쓴다."""
    support = {}
    for identifier, document in documents.items():
        for edge in document[GraphRecord.Field.GRAPH][GraphRecord.Field.RELATIONS]:
            key = GraphRelation.triple(edge)
            support.setdefault(key, set()).add((identifier, edge[GraphRelation.Field.SOURCE_ID]))
    return {key: len(sources) for key, sources in support.items()}


def insert_relations(conn, document, weights):
    for edge in document[GraphRecord.Field.GRAPH][GraphRecord.Field.RELATIONS]:
        rows(conn, GraphQueries.INSERT_RELATION,
             GraphQueries.relation_parameters(RAGDocument(document).identifier, edge, weights))


@contextmanager
def connection(path, *, options=GraphStore.ABSENT):
    import kuzu
    db = kuzu.Database(str(path), **(options or {}))
    try:
        conn = kuzu.Connection(db)
        try:
            yield conn
        finally:
            conn.close()
    finally:
        db.close()


def rows(conn, query: str, parameters=GraphStore.ABSENT) -> list:
    result = conn.execute(query, parameters or {})
    try:
        output = []
        while result.has_next():
            output.append(result.get_next())
        return output
    finally:
        result.close()


def build_graph(path, documents: dict, *, options=GraphStore.ABSENT) -> None:
    with connection(path, options=options) as conn:
        rows(conn, GraphQueries.CREATE_ENTITIES)
        rows(conn, GraphQueries.CREATE_RELATIONS)
        entities = {entity[GraphEntity.Field.ID]: entity for doc in documents.values() for entity in doc[GraphRecord.Field.GRAPH][GraphRecord.Field.ENTITIES]}
        for entity in entities.values():
            rows(conn, GraphQueries.INSERT_ENTITY, entity)
        weights = relation_weights(documents)
        for document in documents.values():
            insert_relations(conn, document, weights)


def update_graph(path, before, documents, *, options=GraphStore.ABSENT):
    """변경 문서의 관계만 교체하고 여러 문서가 공유하는 엔티티는 보존한다."""
    changed = {key for key in before.keys() | documents.keys()
               if key not in before or key not in documents or before[key][GraphRecord.Field.GRAPH] != documents[key][GraphRecord.Field.GRAPH]}
    if not changed:
        return
    entities = {e[GraphEntity.Field.ID]: e for d in documents.values() for e in d[GraphRecord.Field.GRAPH][GraphRecord.Field.ENTITIES]}
    old = {e[GraphEntity.Field.ID] for d in before.values() for e in d[GraphRecord.Field.GRAPH][GraphRecord.Field.ENTITIES]}
    weights = relation_weights(documents)
    old_weights = relation_weights(before)
    with connection(path, options=options) as conn:
        for identifier in changed:
            rows(conn, GraphQueries.DELETE_DOCUMENT, {GraphEntity.Field.ID: identifier})
        for identifier in old - entities.keys():
            rows(conn, GraphQueries.DELETE_ENTITY, {GraphEntity.Field.ID: identifier})
        for entity in entities.values():
            rows(conn, GraphQueries.MERGE_ENTITY, entity)
        for identifier in changed & documents.keys():
            insert_relations(conn, documents[identifier], weights)
        # 문서 수정/삭제는 남아 있는 동일 관계의 가중치도 낮춘다. 재등록 횟수는 누적하지 않는다.
        for (source, kind, target), weight in weights.items():
            if old_weights.get((source, kind, target)) != weight:
                rows(conn, GraphQueries.UPDATE_SUPPORT,
                     {GraphRelation.Field.SOURCE: source, GraphRelation.Field.TARGET: target, GraphQueries.KIND: kind, GraphRelation.Field.WEIGHT: weight})
