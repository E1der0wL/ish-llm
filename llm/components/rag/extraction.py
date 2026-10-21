"""모델 응답을 출처가 있는 엔티티·트리플로 변환한다. 저장은 Component가 담당한다."""

import hashlib
import json
import re
from copy import copy, deepcopy
from datetime import datetime, timezone

from llm.components.rag._client import ModelClient
from .prompts import EXTRACTION_CONTRACT, ExtractionJSONMode
from llm.errors import CodedError
from .splitting import validate_chunk

CHECKPOINT_FIELDS = frozenset(("chunks", "chunk_size", "graph"))


class GraphValidationError(CodedError, ValueError):
    """구문/의미 계약 실패. 공급자 retry와 분리된 repair/best_effort 대상이다."""
    code = "graph_validation_failed"


def validate_graph(graph: dict, chunks: list, *, relation_types=()) -> dict:
    """관계의 양 끝과 정확한 원문 인용을 검증하고 이름 기반 ID로 정규화한다."""
    if not isinstance(graph, dict) or not isinstance(graph.get("entities"), list) or not isinstance(graph.get("relations"), list):
        raise GraphValidationError("Extraction requires entities and relations lists")
    source = {c["id"]: c["text"] for c in chunks}
    canonical_types = {kind.casefold(): kind for kind in relation_types}
    identities, entities = {}, {}
    for index, entity in enumerate(graph["entities"]):
        if not isinstance(entity, dict):
            raise GraphValidationError(f"entities[{index}] must be an object")
        identifier, name = entity.get("id"), entity.get("name")
        if not isinstance(identifier, str) or not identifier or identifier in identities or not isinstance(name, str) or not name.strip():
            raise GraphValidationError("Entities require unique IDs and nonempty names")
        name = name.strip()
        key = hashlib.sha256(name.casefold().encode("utf-8")).hexdigest()
        identities[identifier] = key
        entities[key] = {"id": key, "name": name}
    relations = []
    for index, edge in enumerate(graph["relations"]):
        if not isinstance(edge, dict):
            raise GraphValidationError(f"relations[{index}] must be an object")
        if any(not isinstance(edge.get(key), str) or edge[key] not in identities for key in ("source", "target")):
            raise GraphValidationError(f"Relation references unknown entity at relations[{index}]; source/target must be entities[].id")
        evidence, kind = edge.get("evidence"), edge.get("type")
        source_id = edge.get("source_id")
        if (not isinstance(kind, str) or not kind.strip() or not isinstance(source_id, str)
                or source_id not in source or not isinstance(evidence, str) or not evidence.strip()
                or evidence not in source[source_id]):
            raise GraphValidationError(f"Relation requires a type and an exact source quotation at relations[{index}]; "
                             "source_id must be a supplied chunk ID and evidence a literal substring of its text")
        metadata = edge.get("metadata", {})
        if not isinstance(metadata, dict):
            raise GraphValidationError(f"relations[{index}].metadata must be a JSON object")
        try:
            metadata = json.loads(json.dumps(metadata, ensure_ascii=False, allow_nan=False))
        except (TypeError, ValueError) as error:
            raise GraphValidationError(f"relations[{index}].metadata must contain finite JSON values") from error
        relations.append({"source": identities[edge["source"]], "target": identities[edge["target"]],
            "type": canonical_types.get(kind.strip().casefold(), kind.strip()),
            "source_id": source_id, "evidence": evidence, "metadata": metadata})
    return {"entities": list(entities.values()), "relations": relations}


def enrich_graph(graph: dict, document_id: str) -> dict:
    """검증한 관계에 신뢰한 출처/시각을 부여한다. 반복 근거 수는 확률이 아니다."""
    stamp = datetime.now(timezone.utc).isoformat()
    edges, sources = {}, {}
    for edge in graph["relations"]:
        triple = (edge["source"], edge["type"], edge["target"])
        key = (*triple, edge["source_id"], edge["evidence"])
        # 같은 출력의 중복은 한 근거로 취급한다. 서로 다른 청크만 가중치를 높인다.
        edges.setdefault(key, {**edge, "document_id": document_id, "extracted_at": stamp})
        sources.setdefault(triple, set()).add(edge["source_id"])
    return {"entities": graph["entities"], "relations": [
        {**edge, "weight": len(sources[key[:3]])} for key, edge in edges.items()]}


def validate_graph_checkpoint(graph):
    """Require the normalized stored response; quotation checks still use real chunks."""
    if (not isinstance(graph, dict) or graph.keys() != {"entities", "relations"}
            or not isinstance(graph["entities"], list) or not isinstance(graph["relations"], list)):
        raise GraphValidationError("Stored extraction checkpoint requires entities and relations")
    identities = set()
    for entity in graph["entities"]:
        if (not isinstance(entity, dict) or entity.keys() != {"id", "name"}
                or not isinstance(entity["name"], str) or not entity["name"].strip()
                or entity["name"] != entity["name"].strip()):
            raise GraphValidationError("Stored extraction entity is incomplete or noncanonical")
        expected = hashlib.sha256(entity["name"].casefold().encode("utf-8")).hexdigest()
        if entity["id"] != expected or expected in identities:
            raise GraphValidationError("Stored extraction entity identity changed or is duplicated")
        identities.add(expected)
    for edge in graph["relations"]:
        if (not isinstance(edge, dict)
                or edge.keys() != {"source", "target", "type", "source_id", "evidence", "metadata"}):
            raise GraphValidationError("Stored extraction relation requires all evidence fields")
        for key in ("source", "target", "type", "source_id", "evidence"):
            if not isinstance(edge[key], str) or not edge[key].strip():
                raise GraphValidationError("Stored extraction evidence must be non-empty text")
        if edge["source"] not in identities or edge["target"] not in identities:
            raise GraphValidationError("Stored extraction relation references an unknown entity")
        if edge["type"] != edge["type"].strip() or not isinstance(edge["metadata"], dict):
            raise GraphValidationError("Stored extraction relation is not canonical")
        try:
            json.dumps(edge["metadata"], ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise GraphValidationError("Stored extraction metadata requires finite JSON") from error
    return graph


def validate_extraction_checkpoint(value, signature):
    """Validate the actual request chunks and response without live settings."""
    from llm.services.infrastructure.storage import revision_token
    if (not isinstance(value, dict) or value.keys() != CHECKPOINT_FIELDS
            or type(value["chunk_size"]) is not int or value["chunk_size"] < 1):
        raise GraphValidationError("Incomplete extraction checkpoint source")
    if (not isinstance(value["chunks"], list) or not value["chunks"]
            or not isinstance(signature, str) or re.fullmatch(r"[a-f0-9]{64}", signature) is None):
        raise GraphValidationError("Invalid stored extraction request chunks or signature")
    for chunk in value["chunks"]:
        validate_chunk(chunk)
    graph = validate_graph_checkpoint(value["graph"])
    canonical = validate_graph(graph, value["chunks"])
    if revision_token(canonical) != revision_token(graph):
        raise GraphValidationError("Stored extraction response is not canonical")
    return graph


def validate_stored_graph(graph: dict, chunks: list, document_id: str):
    """Require the complete enriched Writer result; never synthesize stored evidence."""
    normalized = validate_graph(graph, chunks)
    if graph.keys() != {"entities", "relations"} or graph["entities"] != normalized["entities"]:
        raise GraphValidationError("Stored graph entities are not canonical")
    sources, seen = {}, set()
    for edge, canonical in zip(graph["relations"], normalized["relations"]):
        if (edge.keys() != canonical.keys() | {"document_id", "extracted_at", "weight"}
                or any(edge[key] != value for key, value in canonical.items())
                or edge["document_id"] != document_id
                or not isinstance(edge["extracted_at"], str)
                or datetime.fromisoformat(edge["extracted_at"]).utcoffset() is None
                or type(edge["weight"]) is not int or edge["weight"] < 1):
            raise GraphValidationError("Incomplete or invalid stored graph evidence")
        triple = edge["source"], edge["type"], edge["target"]
        key = (*triple, edge["source_id"], edge["evidence"])
        if key in seen:
            raise GraphValidationError("Duplicate stored graph evidence")
        seen.add(key)
        sources.setdefault(triple, set()).add(edge["source_id"])
    for edge in graph["relations"]:
        if edge["weight"] != len(sources[edge["source"], edge["type"], edge["target"]]):
            raise GraphValidationError("Stored graph weight differs from its source evidence")
    return graph


def group_evidence(graph):
    """Build a disposable source lookup once, rather than scanning all edges per batch."""
    by_source = {}
    for edge in graph["relations"]:
        by_source.setdefault(edge["source_id"], []).append(edge)
    return {entity["id"]: entity for entity in graph["entities"]}, by_source


def select_graph(grouped, entity_ids, chunks, *, relation_types=()):
    """Reconstruct one extraction batch from stored graph data, then validate its evidence."""
    entities, by_source = grouped
    if set(entity_ids) - entities.keys():
        raise GraphValidationError("Extraction manifest references unknown entities")
    return validate_graph({"entities": [entities[key] for key in entity_ids],
        "relations": [edge for chunk in chunks for edge in by_source.get(chunk["id"], ())]},
        chunks, relation_types=relation_types)


class TripleExtractor(ModelClient):
    """LiteLLM 비동기 completion 또는 주입한 함수로 트리플을 추출한다."""

    def __init__(self, *, completion_fn=None, **params):
        super().__init__("acompletion", completion_fn, params)
        self.extraction = {}
        self.prompt = {"messages": []}

    def with_extraction(self, options: dict, prompt: dict):
        """호출별 정책/프롬프트 사본. 등록 클라이언트나 다른 Project는 변경하지 않는다."""
        from jsonschema import Draft202012Validator
        from .prompts import extraction_schema
        Draft202012Validator(extraction_schema()).validate(options)
        worker = copy(self)
        worker.extraction, worker.prompt = deepcopy(options), deepcopy(prompt)
        return worker

    async def extract(self, chunks: list) -> dict:
        """검증 오류만 제한 횟수 수정한다. 연결 오류·취소·사용량 제한은 그대로 전달한다."""
        messages = [{"role": "system", "content": EXTRACTION_CONTRACT + "\nPreferred relation types: "
                     + json.dumps(self.extraction.get("relation_types", []), ensure_ascii=False)},
                    *deepcopy(self.prompt["messages"]),
                    {"role": "user", "content": json.dumps(chunks, ensure_ascii=False)}]
        from llm.providers.requests import error_code
        from llm.providers.runtime import diagnostic
        mode = self.extraction.get("json_mode")
        wall_timeout = self.provider_options.get("wall_timeout")
        from llm.policies.retry import RetryPolicy
        repair = RetryPolicy(self.extraction.get("repair_attempts", 0) + 1,
                             deadline=RetryPolicy.end(wall_timeout))
        deadline = repair.deadline
        for attempt in range(repair.max_attempts):
            try:
                response = await self._invoke(stream=False, _provider_deadline=deadline,
                    **({"response_format": {"type": "json_object"}} if mode in (ExtractionJSONMode.STRICT.value, ExtractionJSONMode.AUTO.value) else {"_without_response_format": True} if mode == ExtractionJSONMode.OFF.value else {}),
                    messages=messages)
            except Exception as error:
                if mode != ExtractionJSONMode.AUTO.value or error_code(error) not in ("provider_empty_response", "provider_invalid_response"):
                    raise
                mode = ExtractionJSONMode.OFF.value
                diagnostic("provider_json_mode_fallback", operation="acompletion")
                response = await self._invoke(stream=False, _provider_deadline=deadline,
                    _without_response_format=True, messages=messages)
            content = None
            try:
                choices = response["choices"] if isinstance(response, dict) else response.choices
                message = choices[0]["message"] if isinstance(choices[0], dict) else choices[0].message
                content = message["content"] if isinstance(message, dict) else message.content
                graph = json.loads(content)
                validate_graph(graph, chunks)
                return graph
            except (ValueError, TypeError, KeyError, IndexError, AttributeError) as error:
                if not repair.allows(attempt + 1):
                    raise GraphValidationError(f"RAG extraction invalid after {attempt} repair attempts: {error}") from error
                # 이전 응답을 assistant 지침으로 승격하지 않는다. 고정 크기의 최신 오류만 전달한다.
                messages[-1] = {"role": "user", "content": json.dumps({
                    "instruction": "Correct the previous JSON to satisfy the extraction contract. Return the full corrected JSON.",
                    "validation_error": str(error), "previous_json": content, "chunks": chunks,
                }, ensure_ascii=False)}
