"""문서 검색의 근거 문단 또는 정확한 엔티티 이름에서 시작하는 유한 방향 그래프 탐색."""

from .graph_indexing import connection, rows, GraphQueries
from .graph_records import GraphEntity, GraphRelation, GraphRecord, GraphSearchResult
from .search import RelationRanking


class GraphTraversal:
    ABSENT = None
    INCLUDE_SOURCE = True
    TARGET_ONLY = False
    FIRST_HOP = 1
    NEXT_FRONTIER = 2
    INCLUSIVE_BOUND = 1
    INVALID_RANKING = "Unknown graph relation ranking"


def relation_order(strategy, *, source_column=GraphTraversal.INCLUDE_SOURCE):
    """미설정은 ID 기반 안정 순서다. 지지 개수 우선 정렬은 명시적으로만 선택한다."""
    if strategy is not GraphTraversal.ABSENT and strategy not in tuple(RelationRanking):
        raise ValueError(GraphTraversal.INVALID_RANKING)
    prefix = GraphQueries.ORDER_SUPPORT if strategy == RelationRanking.SUPPORT_COUNT else GraphQueries.EMPTY_ORDER
    return GraphQueries.ORDER_PREFIX + prefix + GraphQueries.ORDER_SOURCE + (GraphQueries.ORDER_SOURCE_ID if source_column else GraphQueries.EMPTY_ORDER) + GraphQueries.ORDER_TAIL


def related_graph(path, source_ids: list, *, max_hops: int, limit: int, options=GraphTraversal.ABSENT, ranking=GraphTraversal.ABSENT) -> dict:
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
                entities[source] = GraphEntity.create(source, source_name)
                entities[target] = GraphEntity.create(target, target_name)
                edges.append(GraphSearchResult.edge(source_name, target_name, kind, document_id, source_id, evidence, hop, weight, metadata, extracted_at))
                frontier.extend((source, target))
                if len(edges) == limit:
                    break
            return frontier

        columns = GraphQueries.COLUMNS
        order = relation_order(ranking)
        frontier = []
        # 문서 검색 순서를 유지하므로 낮은 순위 문단이 관계 예산을 먼저 쓰지 않는다.
        for source_id in dict.fromkeys(source_ids):
            found = rows(conn, GraphQueries.SELECT_SOURCE
                         + columns + order + GraphQueries.limit(limit - len(edges)), {GraphRelation.Field.SOURCE_ID: source_id})
            frontier.extend(collect(found, GraphTraversal.FIRST_HOP))
            if len(edges) == limit:
                break
        visited = set()
        for hop in range(GraphTraversal.NEXT_FRONTIER, max_hops + GraphTraversal.INCLUSIVE_BOUND):
            following = []
            for identifier in dict.fromkeys(frontier):
                if identifier in visited or len(edges) == limit:
                    continue
                visited.add(identifier)
                # 이미 본 간선 수만큼 더 읽고 collect에서 중복을 제거한다.
                found = rows(conn, GraphQueries.SELECT_OUTGOING
                             + columns + order + GraphQueries.limit(limit + len(seen)), {GraphEntity.Field.ID: identifier})
                following.extend(collect(found, hop))
            frontier = following
            if not frontier or len(edges) == limit:
                break
    return GraphRecord.create(list(entities.values()), edges)


def graph_search(path, seed: str, *, max_hops: int, limit: int, options=GraphTraversal.ABSENT, ranking=GraphTraversal.ABSENT) -> dict:
    edges, entities, visited = [], {}, set()
    with connection(path, options=options) as conn:
        frontier = rows(conn, GraphQueries.SELECT_NAME, {GraphEntity.Field.NAME: seed})
        for identifier, name in frontier:
            entities[identifier] = GraphEntity.create(identifier, name)
        for hop in range(GraphTraversal.FIRST_HOP, max_hops + GraphTraversal.INCLUSIVE_BOUND):
            following = []
            for identifier, name in frontier:
                if identifier in visited:
                    continue
                visited.add(identifier)
                # LIMIT는 검증된 정수로만 삽입한다. 사용자 문자열은 바인딩한다.
                found = rows(conn, GraphQueries.SELECT_TARGET_COLUMNS
                    + relation_order(ranking, source_column=GraphTraversal.TARGET_ONLY)
                    + GraphQueries.limit(limit - len(edges)), {GraphEntity.Field.ID: identifier})
                for kind, target, target_name, document_id, source_id, evidence, weight, metadata, extracted_at in found:
                    entities[target] = GraphEntity.create(target, target_name)
                    edges.append(GraphSearchResult.edge(name, target_name, kind, document_id, source_id, evidence, hop, weight, metadata, extracted_at))
                    following.append((target, target_name))
                if len(edges) >= limit:
                    return GraphSearchResult.seeded(seed, list(entities.values()), edges)
            frontier = following
    return GraphSearchResult.seeded(seed, list(entities.values()), edges)
