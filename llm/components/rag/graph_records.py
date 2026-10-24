"""RAG graph records, source evidence and strict stored-response validation."""

import json
import re
from datetime import datetime, timezone
from llm.errors import CodedError
from .chunk_records import RAGChunk, RAGContent


class GraphEntity:
    class Field:
        ID = "id"
        NAME = "name"

    FIELDS = frozenset((Field.ID, Field.NAME))

    def __init__(self, value):
        self._value = value

    @property
    def stored_id(self):
        return self._value[self.Field.ID]

    @classmethod
    def identifier(cls, name):
        return RAGContent.digest(name.casefold())

    @classmethod
    def create(cls, identifier, name):
        return {cls.Field.ID: identifier, cls.Field.NAME: name}


class GraphRelation:
    class Field:
        SOURCE = "source"
        TARGET = "target"
        TYPE = "type"
        SOURCE_ID = "source_id"
        EVIDENCE = "evidence"
        METADATA = "metadata"
        DOCUMENT_ID = "document_id"
        EXTRACTED_AT = "extracted_at"
        WEIGHT = "weight"

    TRIPLE_SIZE = 3
    ENDPOINT_FIELDS = (Field.SOURCE, Field.TARGET)
    TEXT_FIELDS = (Field.SOURCE, Field.TARGET, Field.TYPE, Field.SOURCE_ID, Field.EVIDENCE)
    RESPONSE_FIELDS = frozenset((*TEXT_FIELDS, Field.METADATA))
    ENRICHMENT_FIELDS = frozenset((Field.DOCUMENT_ID, Field.EXTRACTED_AT, Field.WEIGHT))
    EVIDENCE_KEY_FIELDS = (Field.SOURCE, Field.TYPE, Field.TARGET, Field.SOURCE_ID, Field.EVIDENCE)

    def __init__(self, value):
        self._value = value

    @property
    def source_id(self):
        return self._value[self.Field.SOURCE_ID]

    @property
    def extracted_at(self):
        return self._value[self.Field.EXTRACTED_AT]

    @classmethod
    def restore_extracted_at(cls, edge, timestamp):
        edge[cls.Field.EXTRACTED_AT] = timestamp

    @classmethod
    def triple(cls, edge):
        return edge[cls.Field.SOURCE], edge[cls.Field.TYPE], edge[cls.Field.TARGET]

    @classmethod
    def evidence_key(cls, edge):
        return tuple(edge[key] for key in cls.EVIDENCE_KEY_FIELDS)


class GraphRecord:
    class Field:
        ENTITIES = "entities"
        RELATIONS = "relations"
        GRAPH = "graph"

    FIELDS = frozenset((Field.ENTITIES, Field.RELATIONS))

    def __init__(self, value):
        self._value = value

    @property
    def entities(self):
        return self._value[self.Field.ENTITIES]

    @property
    def relations(self):
        return self._value[self.Field.RELATIONS]

    @classmethod
    def create(cls, entities, relations):
        return {cls.Field.ENTITIES: entities, cls.Field.RELATIONS: relations}


class GraphSearchResult:
    class Field:
        SEED = "seed"
        HOP = "hop"
        SUPPORT_COUNT = "support_count"

    @classmethod
    def seeded(cls, seed, entities, relations):
        return {cls.Field.SEED: seed, GraphRecord.Field.ENTITIES: entities,
                GraphRecord.Field.RELATIONS: relations}

    @classmethod
    def edge(cls, source, target, kind, document_id, source_id, evidence, hop, support_count, metadata, extracted_at):
        return {GraphRelation.Field.SOURCE: source, GraphRelation.Field.TARGET: target,
            GraphRelation.Field.TYPE: kind, GraphRelation.Field.DOCUMENT_ID: document_id,
            GraphRelation.Field.SOURCE_ID: source_id, GraphRelation.Field.EVIDENCE: evidence,
            cls.Field.HOP: hop, cls.Field.SUPPORT_COUNT: support_count,
            GraphRelation.Field.METADATA: json.loads(metadata), GraphRelation.Field.EXTRACTED_AT: extracted_at}


class ExtractionCheckpoint:
    class Field:
        CHUNKS = "chunks"
        CHUNK_SIZE = "chunk_size"
        GRAPH = GraphRecord.Field.GRAPH

    FIELDS = frozenset((Field.CHUNKS, Field.CHUNK_SIZE, Field.GRAPH))
    MINIMUM_CHUNK_SIZE = 1
    SIGNATURE_PATTERN = r"[a-f0-9]{64}"

    @classmethod
    def create(cls, chunks, chunk_size, graph):
        return {cls.Field.CHUNKS: chunks, cls.Field.CHUNK_SIZE: chunk_size, cls.Field.GRAPH: graph}


class GraphEncoding:
    ASCII_ONLY = False
    ALLOW_NONFINITE = False
    ABSENT = None
    MINIMUM_SUPPORT = 1


class GraphValidationText:
    REQUIRE_LISTS = "Extraction requires entities and relations lists"
    ENTITY_OBJECT = "entities[{index}] must be an object"
    ENTITY_IDENTITY = "Entities require unique IDs and nonempty names"
    RELATION_OBJECT = "relations[{index}] must be an object"
    UNKNOWN_ENTITY = "Relation references unknown entity at relations[{index}]; source/target must be entities[].id"
    REQUIRE_EVIDENCE = ("Relation requires a type and an exact source quotation at relations[{index}]; "
                        "source_id must be a supplied chunk ID and evidence a literal substring of its text")
    METADATA_OBJECT = "relations[{index}].metadata must be a JSON object"
    FINITE_METADATA = "relations[{index}].metadata must contain finite JSON values"
    STORED_LISTS = "Stored extraction checkpoint requires entities and relations"
    STORED_ENTITY = "Stored extraction entity is incomplete or noncanonical"
    STORED_IDENTITY = "Stored extraction entity identity changed or is duplicated"
    STORED_RELATION = "Stored extraction relation requires all evidence fields"
    STORED_TEXT = "Stored extraction evidence must be non-empty text"
    STORED_UNKNOWN_ENTITY = "Stored extraction relation references an unknown entity"
    STORED_CANONICAL = "Stored extraction relation is not canonical"
    STORED_METADATA = "Stored extraction metadata requires finite JSON"
    CHECKPOINT_SOURCE = "Incomplete extraction checkpoint source"
    CHECKPOINT_REQUEST = "Invalid stored extraction request chunks or signature"
    CHECKPOINT_CANONICAL = "Stored extraction response is not canonical"
    GRAPH_ENTITIES = "Stored graph entities are not canonical"
    GRAPH_EVIDENCE = "Incomplete or invalid stored graph evidence"
    DUPLICATE_EVIDENCE = "Duplicate stored graph evidence"
    GRAPH_SUPPORT = "Stored graph weight differs from its source evidence"
    MANIFEST_ENTITY = "Extraction manifest references unknown entities"


class GraphValidationError(CodedError, ValueError):
    """구문/의미 계약 실패. 공급자 retry와 분리된 repair/best_effort 대상이다."""
    code = "graph_validation_failed"


def validate_graph(graph: dict, chunks: list, *, relation_types=()) -> dict:
    """관계의 양 끝과 정확한 원문 인용을 검증하고 이름 기반 ID로 정규화한다."""
    if not isinstance(graph, dict) or not isinstance(graph.get(GraphRecord.Field.ENTITIES), list) or not isinstance(graph.get(GraphRecord.Field.RELATIONS), list):
        raise GraphValidationError(GraphValidationText.REQUIRE_LISTS)
    source = {RAGChunk(c).identifier: RAGChunk(c).text for c in chunks}
    canonical_types = {kind.casefold(): kind for kind in relation_types}
    identities, entities = {}, {}
    for index, entity in enumerate(graph[GraphRecord.Field.ENTITIES]):
        if not isinstance(entity, dict):
            raise GraphValidationError(GraphValidationText.ENTITY_OBJECT.format(index=index))
        identifier, name = entity.get(GraphEntity.Field.ID), entity.get(GraphEntity.Field.NAME)
        if not isinstance(identifier, str) or not identifier or identifier in identities or not isinstance(name, str) or not name.strip():
            raise GraphValidationError(GraphValidationText.ENTITY_IDENTITY)
        name = name.strip()
        key = GraphEntity.identifier(name)
        identities[identifier] = key
        entities[key] = GraphEntity.create(key, name)
    relations = []
    for index, edge in enumerate(graph[GraphRecord.Field.RELATIONS]):
        if not isinstance(edge, dict):
            raise GraphValidationError(GraphValidationText.RELATION_OBJECT.format(index=index))
        if any(not isinstance(edge.get(key), str) or edge[key] not in identities for key in GraphRelation.ENDPOINT_FIELDS):
            raise GraphValidationError(GraphValidationText.UNKNOWN_ENTITY.format(index=index))
        evidence, kind = edge.get(GraphRelation.Field.EVIDENCE), edge.get(GraphRelation.Field.TYPE)
        source_id = edge.get(GraphRelation.Field.SOURCE_ID)
        if (not isinstance(kind, str) or not kind.strip() or not isinstance(source_id, str)
                or source_id not in source or not isinstance(evidence, str) or not evidence.strip()
                or evidence not in source[source_id]):
            raise GraphValidationError(GraphValidationText.REQUIRE_EVIDENCE.format(index=index))
        metadata = edge.get(GraphRelation.Field.METADATA, {})
        if not isinstance(metadata, dict):
            raise GraphValidationError(GraphValidationText.METADATA_OBJECT.format(index=index))
        try:
            metadata = json.loads(json.dumps(metadata, ensure_ascii=GraphEncoding.ASCII_ONLY, allow_nan=GraphEncoding.ALLOW_NONFINITE))
        except (TypeError, ValueError) as error:
            raise GraphValidationError(GraphValidationText.FINITE_METADATA.format(index=index)) from error
        relations.append({GraphRelation.Field.SOURCE: identities[edge[GraphRelation.Field.SOURCE]], GraphRelation.Field.TARGET: identities[edge[GraphRelation.Field.TARGET]],
            GraphRelation.Field.TYPE: canonical_types.get(kind.strip().casefold(), kind.strip()),
            GraphRelation.Field.SOURCE_ID: source_id, GraphRelation.Field.EVIDENCE: evidence, GraphRelation.Field.METADATA: metadata})
    return GraphRecord.create(list(entities.values()), relations)


def enrich_graph(graph: dict, document_id: str) -> dict:
    """검증한 관계에 신뢰한 출처/시각을 부여한다. 반복 근거 수는 확률이 아니다."""
    stamp = datetime.now(timezone.utc).isoformat()
    edges, sources = {}, {}
    for edge in graph[GraphRecord.Field.RELATIONS]:
        triple = GraphRelation.triple(edge)
        key = (*triple, edge[GraphRelation.Field.SOURCE_ID], edge[GraphRelation.Field.EVIDENCE])
        # 같은 출력의 중복은 한 근거로 취급한다. 서로 다른 청크만 가중치를 높인다.
        edges.setdefault(key, {**edge, GraphRelation.Field.DOCUMENT_ID: document_id, GraphRelation.Field.EXTRACTED_AT: stamp})
        sources.setdefault(triple, set()).add(edge[GraphRelation.Field.SOURCE_ID])
    return GraphRecord.create(graph[GraphRecord.Field.ENTITIES], [{**edge, GraphRelation.Field.WEIGHT: len(sources[key[:GraphRelation.TRIPLE_SIZE]])} for key, edge in edges.items()])


def validate_graph_checkpoint(graph):
    """Require the normalized stored response; quotation checks still use real chunks."""
    if (not isinstance(graph, dict) or graph.keys() != GraphRecord.FIELDS
            or not isinstance(graph[GraphRecord.Field.ENTITIES], list) or not isinstance(graph[GraphRecord.Field.RELATIONS], list)):
        raise GraphValidationError(GraphValidationText.STORED_LISTS)
    identities = set()
    for entity in graph[GraphRecord.Field.ENTITIES]:
        if (not isinstance(entity, dict) or entity.keys() != GraphEntity.FIELDS
                or not isinstance(entity[GraphEntity.Field.NAME], str) or not entity[GraphEntity.Field.NAME].strip()
                or entity[GraphEntity.Field.NAME] != entity[GraphEntity.Field.NAME].strip()):
            raise GraphValidationError(GraphValidationText.STORED_ENTITY)
        expected = GraphEntity.identifier(entity[GraphEntity.Field.NAME])
        if entity[GraphEntity.Field.ID] != expected or expected in identities:
            raise GraphValidationError(GraphValidationText.STORED_IDENTITY)
        identities.add(expected)
    for edge in graph[GraphRecord.Field.RELATIONS]:
        if (not isinstance(edge, dict)
                or edge.keys() != GraphRelation.RESPONSE_FIELDS):
            raise GraphValidationError(GraphValidationText.STORED_RELATION)
        for key in GraphRelation.TEXT_FIELDS:
            if not isinstance(edge[key], str) or not edge[key].strip():
                raise GraphValidationError(GraphValidationText.STORED_TEXT)
        if edge[GraphRelation.Field.SOURCE] not in identities or edge[GraphRelation.Field.TARGET] not in identities:
            raise GraphValidationError(GraphValidationText.STORED_UNKNOWN_ENTITY)
        if edge[GraphRelation.Field.TYPE] != edge[GraphRelation.Field.TYPE].strip() or not isinstance(edge[GraphRelation.Field.METADATA], dict):
            raise GraphValidationError(GraphValidationText.STORED_CANONICAL)
        try:
            json.dumps(edge[GraphRelation.Field.METADATA], ensure_ascii=GraphEncoding.ASCII_ONLY, allow_nan=GraphEncoding.ALLOW_NONFINITE)
        except (TypeError, ValueError) as error:
            raise GraphValidationError(GraphValidationText.STORED_METADATA) from error
    return graph


def validate_extraction_checkpoint(value, signature):
    """Validate the actual request chunks and response without live settings."""
    from llm.services.infrastructure.storage import revision_token
    if (not isinstance(value, dict) or value.keys() != ExtractionCheckpoint.FIELDS
            or type(value[ExtractionCheckpoint.Field.CHUNK_SIZE]) is not int or value[ExtractionCheckpoint.Field.CHUNK_SIZE] < ExtractionCheckpoint.MINIMUM_CHUNK_SIZE):
        raise GraphValidationError(GraphValidationText.CHECKPOINT_SOURCE)
    if (not isinstance(value[ExtractionCheckpoint.Field.CHUNKS], list) or not value[ExtractionCheckpoint.Field.CHUNKS]
            or not isinstance(signature, str) or re.fullmatch(ExtractionCheckpoint.SIGNATURE_PATTERN, signature) is GraphEncoding.ABSENT):
        raise GraphValidationError(GraphValidationText.CHECKPOINT_REQUEST)
    for chunk in value[ExtractionCheckpoint.Field.CHUNKS]:
        RAGChunk.validate(chunk)
    graph = validate_graph_checkpoint(value[GraphRecord.Field.GRAPH])
    canonical = validate_graph(graph, value[ExtractionCheckpoint.Field.CHUNKS])
    if revision_token(canonical) != revision_token(graph):
        raise GraphValidationError(GraphValidationText.CHECKPOINT_CANONICAL)
    return graph


def validate_stored_graph(graph: dict, chunks: list, document_id: str):
    """Require the complete enriched Writer result; never synthesize stored evidence."""
    normalized = validate_graph(graph, chunks)
    if graph.keys() != GraphRecord.FIELDS or graph[GraphRecord.Field.ENTITIES] != normalized[GraphRecord.Field.ENTITIES]:
        raise GraphValidationError(GraphValidationText.GRAPH_ENTITIES)
    sources, seen = {}, set()
    for edge, canonical in zip(graph[GraphRecord.Field.RELATIONS], normalized[GraphRecord.Field.RELATIONS]):
        if (edge.keys() != canonical.keys() | GraphRelation.ENRICHMENT_FIELDS
                or any(edge[key] != value for key, value in canonical.items())
                or edge[GraphRelation.Field.DOCUMENT_ID] != document_id
                or not isinstance(edge[GraphRelation.Field.EXTRACTED_AT], str)
                or datetime.fromisoformat(edge[GraphRelation.Field.EXTRACTED_AT]).utcoffset() is GraphEncoding.ABSENT
                or type(edge[GraphRelation.Field.WEIGHT]) is not int or edge[GraphRelation.Field.WEIGHT] < GraphEncoding.MINIMUM_SUPPORT):
            raise GraphValidationError(GraphValidationText.GRAPH_EVIDENCE)
        triple = GraphRelation.triple(edge)
        key = (*triple, edge[GraphRelation.Field.SOURCE_ID], edge[GraphRelation.Field.EVIDENCE])
        if key in seen:
            raise GraphValidationError(GraphValidationText.DUPLICATE_EVIDENCE)
        seen.add(key)
        sources.setdefault(triple, set()).add(edge[GraphRelation.Field.SOURCE_ID])
    for edge in graph[GraphRecord.Field.RELATIONS]:
        if edge[GraphRelation.Field.WEIGHT] != len(sources[edge[GraphRelation.Field.SOURCE], edge[GraphRelation.Field.TYPE], edge[GraphRelation.Field.TARGET]]):
            raise GraphValidationError(GraphValidationText.GRAPH_SUPPORT)
    return graph


def group_evidence(graph):
    """Build a disposable source lookup once, rather than scanning all edges per batch."""
    by_source = {}
    for edge in graph[GraphRecord.Field.RELATIONS]:
        by_source.setdefault(edge[GraphRelation.Field.SOURCE_ID], []).append(edge)
    return {entity[GraphEntity.Field.ID]: entity for entity in graph[GraphRecord.Field.ENTITIES]}, by_source


def select_graph(grouped, entity_ids, chunks, *, relation_types=()):
    """Reconstruct one extraction batch from stored graph data, then validate its evidence."""
    entities, by_source = grouped
    if set(entity_ids) - entities.keys():
        raise GraphValidationError(GraphValidationText.MANIFEST_ENTITY)
    return validate_graph(GraphRecord.create([entities[key] for key in entity_ids], [edge for chunk in chunks for edge in by_source.get(RAGChunk(chunk).identifier, ())]),
        chunks, relation_types=relation_types)
