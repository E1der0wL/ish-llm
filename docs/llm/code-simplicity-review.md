# 코드 단순성 검토 — 2026-09-28

초기 검토 이후 메인 도메인의 아래 리팩토링을 적용했다. 컴포넌트 내부 최적화는 이번 범위에 포함하지 않았다.
함수 개수보다는 한 함수 안에서 구분하기 어려운 책임, 반복 계산과 전체 자료 탐색을 기준으로 봤다.

| 우선순위 | 위치 | 문제 | 다음 개선 방향 |
| --- | --- | --- | --- |
| 완료 | services/runtime/runs.py, RunManager._consume | 출력 계약은 RunOutputState, 영속 이벤트/델타 저장은 전용 private 메서드로 구분 | _consume은 소비·저장 대기·실행 상태 반영·알림 순서를 유지한다. 저장 경계를 늘리거나 별도 Manager를 추가하지 않음 |
| 완료 | engines/graph/engine.py, Graph runtime의 _node | 승인·재개·체크포인트·Step 수명과 노드 계산/입출력 연결을 구분 | 같은 파일의 _execute_node가 계산을 담당하며 기존 공통 수명 래퍼가 실패·취소를 처리. 하위 Run을 만들지 않음 |
| 완료 | engines/graph/engine.py, _walk/_prepare/_binding | 준비 단계의 정의 스냅샷을 검증과 바인딩에 공유 | 호출별 중첩 문맥은 유지하며 다음 실행/재개에서는 다시 검사. 등록 Engine 인스턴스에 실행 캐시를 만들지 않음 |
| 완료 | runtime/output.py, history/conversation.py, infrastructure/journal.py | 출력과 대화의 매 델타 전체 문자열 복사를 버퍼 누적으로 변경. 출력 저널은 순차 투영 | 공개 데이터 클래스·순서 검증·rollback·fsync는 유지. 확정/조회 때만 문자열 생성 |
| 완료 | services/query.py | 역순 최신 페이지에서 전체 참조 목록을 복사하던 비용 제거 | iterable 확장 지원은 유지. 파일의 stat/무결성 확인을 생략하지 않음 |
| 2 | components/rag/data.py, _current 및 rag/component.py, configured | 단순 조회에도 설정 병합·사본·클라이언트 바인딩을 반복할 수 있음 | 원본 자료 조회와 모델이 필요한 작업의 설정 바인딩을 구분. 설정 버전 충돌 검사는 유지 |
| 2 | components/rag/component.py, snapshot 및 rag/search.py, search | 검색 시 전체 문서 자료를 읽고 전체 chunk 사전을 다시 구성함. BM25 캐시가 있어도 이 비용은 남음 | 불변 generation별 메타데이터/검색 자료 재사용과 문서 지연 읽기. 2GB 규모 효과는 별도 측정 필요 |

Facade → Manager → Repository → 저장 함수의 얇은 위임은 그 자체로 문제가 아니다.
각 단계가 공개 API 수명, 도메인 검증, 저장 형식, 원자적 쓰기를 담당하므로 합치면 책임 경계가
오히려 흐려질 수 있다. 위 개선도 Project → Session → Run → Step을 유지해야 한다.

설정 병합에서 JSON용 병합과 공급자 실행 객체용 병합이 분리된 것도 의도된 것이다.
호출 함수나 SDK 객체의 동일성을 지켜야 하므로 두 함수를 단순히 합치지 않는다.

재현 가능한 검사는 `examples/llm/domain_performance.py`와 사용 안내에서 제공한다.
시간·할당량 미세 측정과 실제 파일 저장 Graph 통합 검사를 구분한다. 최신 실행 수치는
handoff.md를 참고한다. `EngineOutput.apply` 자체는 UI가 활용할 불변 API로 유지하며,
매 토큰마다 전체 불변 결과를 요청하는 외부 사용자의 복사 비용까지 없애지는 않는다.

메인 도메인의 단순성은 명확한 저장/실행 경계와 일관된 데이터 계약 기준으로 높음으로
평가할 수 있다. 성능은 개선한 스트리밍·복원·메모리 페이지 경로에서 검증한다. 파일 카탈로그
전체 확인, 트랜잭션 fsync와 workspace 잠금은 남아 있으므로 무제한 기록 규모의 처리량이나
사내 디스크에서의 3시간 운영까지 이 평가로 확정하지 않는다.
