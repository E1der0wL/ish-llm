"""문서 검색의 근거 문단 또는 정확한 엔티티 이름에서 시작하는 유한 방향 그래프 탐색."""

from .graph_indexing import connection, rows
import json


def related_graph(path, source_ids: list, *, max_hops: int, limit: int, options=None) -> dict:
    """검색된 문단에 근거가 있는 관계부터 시작한다. 전체 반환 개수와 깊이를 제한한다."""
    entities, edges, seen = {}, [], set()
    with connection(path, options=options) as conn:
        def collect(found, hop):
            frontier = []
            for source, source_name, kind, target, target_name, document_id, source_id, evidence, weight, metadata, extracted_at in found:
                key = (source, target, kind, document_id, source_id, evidence)
                if key in seen:
                    continue
                seen.add(key)
                entities[source] = {"id": source, "name": source_name}
                entities[target] = {"id": target, "name": target_name}
                edges.append({"source": source_name, "target": target_name, "type": kind,
                              "document_id": document_id, "source_id": source_id,
                              "evidence": evidence, "hop": hop, "weight": weight,
                              "metadata": json.loads(metadata), "extracted_at": extracted_at})
                frontier.extend((source, target))
                if len(edges) == limit:
                    break
            return frontier

        columns = " RETURN a.id,a.name,r.kind,b.id,b.name,r.document_id,r.source_id,r.evidence,r.weight,r.metadata,r.extracted_at"
        order = " ORDER BY r.weight DESC,r.document_id,r.source_id,a.id,b.id,r.kind,r.evidence"
        frontier = []
        # 문서 검색 순서를 유지하므로 낮은 순위 문단이 관계 예산을 먼저 쓰지 않는다.
        for source_id in dict.fromkeys(source_ids):
            found = rows(conn, "MATCH (a:Entity)-[r:Link]->(b:Entity) WHERE r.source_id=$source_id"
                         + columns + order + f" LIMIT {limit - len(edges)}", {"source_id": source_id})
            frontier.extend(collect(found, 1))
            if len(edges) == limit:
                break
        visited = set()
        for hop in range(2, max_hops + 1):
            following = []
            for identifier in dict.fromkeys(frontier):
                if identifier in visited or len(edges) == limit:
                    continue
                visited.add(identifier)
                # 이미 본 간선 수만큼 더 읽고 collect에서 중복을 제거한다.
                found = rows(conn, "MATCH (a:Entity)-[r:Link]->(b:Entity) WHERE a.id=$id"
                             + columns + order + f" LIMIT {limit + len(seen)}", {"id": identifier})
                following.extend(collect(found, hop))
            frontier = following
            if not frontier or len(edges) == limit:
                break
    return {"entities": list(entities.values()), "relations": edges}


def graph_search(path, seed: str, *, max_hops: int, limit: int, options=None) -> dict:
    edges, entities, visited = [], {}, set()
    with connection(path, options=options) as conn:
        frontier = rows(conn, "MATCH (e:Entity) WHERE e.name=$name RETURN e.id,e.name", {"name": seed})
        for identifier, name in frontier:
            entities[identifier] = {"id": identifier, "name": name}
        for hop in range(1, max_hops + 1):
            following = []
            for identifier, name in frontier:
                if identifier in visited:
                    continue
                visited.add(identifier)
                # LIMIT는 검증된 정수로만 삽입한다. 사용자 문자열은 바인딩한다.
                found = rows(conn, "MATCH (a:Entity)-[r:Link]->(b:Entity) WHERE a.id=$id "
                    "RETURN r.kind,b.id,b.name,r.document_id,r.source_id,r.evidence,r.weight,r.metadata,r.extracted_at "
                    "ORDER BY r.weight DESC,r.document_id,r.source_id,b.id,r.kind,r.evidence "
                    f"LIMIT {limit - len(edges)}", {"id": identifier})
                for kind, target, target_name, document_id, source_id, evidence, weight, metadata, extracted_at in found:
                    entities[target] = {"id": target, "name": target_name}
                    edges.append({"source": name, "target": target_name, "type": kind,
                                  "document_id": document_id, "source_id": source_id,
                                  "evidence": evidence, "hop": hop, "weight": weight,
                                  "metadata": json.loads(metadata), "extracted_at": extracted_at})
                    following.append((target, target_name))
                if len(edges) >= limit:
                    return {"seed": seed, "entities": list(entities.values()), "relations": edges}
            frontier = following
    return {"seed": seed, "entities": list(entities.values()), "relations": edges}
