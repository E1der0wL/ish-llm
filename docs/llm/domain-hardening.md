# 승인, Graph·Agent, Memory·장기 문맥 보강

## 승인과 UI

요청은 Run 체크포인트, 답변·취소·갱신 연결은 Run의 `state/interactions`가 소유한다.
`InteractionView`는 이 기록과 후속 Run 상태를 합친 조회 결과이며 별도 실행 도메인이 아니다.

```python
views = await run.ainteraction_views()
for view in views:
    print(view.status, view.can_respond, view.can_resume, view.execution_status)
    # request.title/options/priority/risk/input_schema로 UI를 구성한다.

pending = await run.ainteractions(pending_only=True)
await run.arespond(pending[0].respond("approve"))
plan = await session.run.resume_plan(run.id, engine=run.engine)
if plan.can_resume:
    resumed = await session.run.resume(run.id, engine=run.engine)
    result = await resumed.wait()
```

- 상태는 pending/answered/denied/expired/cancelled/submitted/unavailable이다.
- `can_resume`는 응답 충족 여부다. 실제 설정 변경·엔진 등록·불확실 실행·기존 재개 여부까지
  검사할 때는 `resume_plan`을 사용한다. 미리보기는 요청을 큐에 넣거나 응답을 저장하지 않는다.
- `execution_status`는 후속 요청의 queued 또는 후속 Run 상태다. 승인이 실행 성공을 뜻하지 않는다.
- `acancel_interaction(request)`는 해당 승인을 무효화한다. 이미 실행에 제출된 요청은 취소할 수 없다.
- 만료/취소된 요청은 `arenew_interaction(request, expires_at=...)`로 갱신한다. 새 ID/버전을
  사용하며 실행 대상·인자는 바꾸지 않는다. 옛 응답은 새 승인을 대신할 수 없다.
- 응답/취소/갱신 저장 후 `INTERACTION_CHANGED` 알림을 보낸다. 시간이 지나 만료되는 것은
  조회 시 계산한다. 재접속·알림 유실 시 `ainteraction_views()`로 현재 상태를 다시 읽는다.
- 불확실한 실행은 `execution.retry_uncertain` 요청으로 조회된다. 승인하면 엔진의 retry_nodes에
  연결된다. 직접 retry_nodes를 지정한 명시적 재개도 응답 영수증을 남긴다.
- 응답 저장은 항상 실행과 분리한다. 자동 승인도 새 Run을 시작하지 않는다.

프로젝트 자동 승인 기본값은 꺼짐이다. 호스트와 프로젝트가 모두 허용한 범위에서만 사용한다.

```python
# 백엔드 구성: authorize가 category/risk를 명시한 ToolApprovalRequired를 발생시킨다.
services = ServiceConfig(tool_policy=ToolPolicy(
    authorize=authorize, auto_approve_categories=("file.read",)))

await project.aconfigure_policies({"approval": {
    "enabled": True,
    "rules": [{"id": "read-only", "category": "file.read", "max_risk": "low"}],
}})
```

카테고리는 정확히 일치해야 한다. unknown 위험도와 불확실 효과 재시도는 자동 승인하지 않는다.
호스트의 Tool 허용 목록/실행 계약/거절을 덮어쓰지 않는다. 응답에는 actor=policy와 policy_id가
남는다. 정책 응답 저장 실패는 PAUSED를 유지하고 운영 로그를 남긴다.
이 정책 스키마는 `backend.project_schema()`에도 포함된다.

공통 choice/input 데이터는 사용자 정의 엔진에서도 사용할 수 있다. 기본 중첩 Loop Agent의
Tool 승인은 bool, Graph 확인은 approved/state 계약을 사용한다. 임의 사용자 정의 엔진의
재개 값까지 자동 변환해 주는 것은 아니며 엔진이 자신의 검증 계약을 제공해야 한다.

## Graph와 Agent

`GraphEngine(cleanup_timeout=5.0)`은 취소 후 정리 대기 시간을 제한한다. 제한을 넘은 코루틴은
RunManager의 공통 PendingWork가 추적한다. 완료 전 같은 Session의 다음 요청과 소유권 양도를 막고
`(await session.run.astatus()).unfinished_work`에서 남은 작업 수를 제공한다. 취소 후 노드 이벤트와
추가 Tool 실행은 거부한다. 백엔드를 닫아도 Session 및 기존 workspace 소유권과 메모리 대화는 실제 정리 완료까지
유지한 뒤 해제한다. 런타임 객체는 도메인 JSON에 저장하지 않는다.

이 기능은 코루틴을 강제로 죽이지 않는다. 동기 CPU 무한 루프, 취소를 무시하는 직접 외부 I/O,
이미 시작된 외부 효과는 호스트의 프로세스 실행기/격리 정책으로 다뤄야 한다. 정리되지 않는
작업이 있으면 Session이 계속 막히는 것이 의도된 동작이다. 다른 Session은 계속 실행할 수 있다.

Graph 재개는 기본적으로 전체 ProjectConfig와 정의·정책·Tool 계약을 검증한다. 개발자가
`config_keys=("my_runtime",)`를 지정하면 추가 설정 중 실행에 쓰는 키만 결합해 UI 표시 설정
변경 때문에 재개가 막히는 일을 줄일 수 있다. parameters/policies는
항상 검증한다. 사용자 정의 처리기가 소비하는 설정 키와 코드 revision을 빠뜨리지 않아야 한다.
중첩 Graph도 자기 설정과 바인딩을 유지한다. LangGraph 스케줄링과 소유 Run의 Step 저장은 유지한다.

## Memory와 장기 문맥

```python
memory = project.components.memory
await memory.aconfigure({"cache_records": 256, "processing": {
    "summarize": True,
    "completion": {"model": "provider/model"},
    "max_summary_calls": 4,
    "model_input_chars": 24000,
    "recall_every": 3,
    "recall_query_chars": 2000,
}})
```

- 단일 과거 턴이 보조 모델 입력보다 크면 여러 조각으로 처리하고 진행 위치를 Session 요약에
  저장한다. 전체 턴이 처리되기 전에는 원문을 문맥에서 제거하지 않는다. 원문 대화 파일도 유지한다.
- 부분 요약은 원문 해시·설정 해시와 결합한다. 재시작 후 같은 입력이면 이어서 처리하며,
  설정이나 원문이 달라지면 해당 부분 커서를 재사용하지 않는다.
- 큰 Tool 교환도 조각으로 요약한다. assistant Tool 호출/결과 쌍 전체를 처리한 뒤에만
  요약으로 대체한다. 실행 중 교환 요약 캐시는 세션 소유이며 재시작 시 원본에서 다시 만든다.
- max_summary_calls는 과거 요약/활성 교환 요약 각 단계의 호출 상한이다. 기본 4, 최소 1이다.
  요약 설정은 기본 비활성이고 추가 모델 호출의 사용량은 기존 Step/Run 관찰 경로에 기록된다.
- recall_every=0은 세션 최초 1회, 양수 N은 prepare 호출마다 N회 간격으로 검색한다.
  현재 요청과 최근 Tool 결과를 검색어로 쓰며 recall_query_chars로 길이를 제한한다.
- 현재 기억 레코드만 제한된 LRU에 보관하고 파일 변경 시 갱신한다. 검색은 여전히 레코드 수에
  비례한다. 대규모 인덱스 검색을 구현한 것은 아니다.
- `MemoryComponent(search_fn=rank)`로 의미 검색 순위를 주입할 수 있다. 동기 함수
  rank(query, visible_records)는 {memory_id: finite_nonnegative_score}를 반환한다.
  현재 Session에서 보이고 상태/만료 조건을 만족하는 기록만 전달하며 다른 ID 반환은 거부한다.
  기본 검색은 키워드 검색이고 특정 임베딩 모델을 강제하지 않는다.

기억 통합은 모델의 후보 작성과 사람의 확정을 분리한다.

```python
review = await memory.areview(session_id=session.id)
proposal = review["proposals"][0]
receipt = await memory.aconsolidate(
    proposal["id"], expected_revision=proposal["revision"], session_id=session.id)
# 중단된 병합을 운영 UI에서 확인하고 복구
pending = await memory.apending_consolidations()
receipts = await memory.arecover_consolidations()
```

후보 metadata.replaces의 {id, revision}들을 모두 검증한 후 후보를 confirmed로 바꾸고
대체 기록을 soft delete한다. 프로젝트/Session 범위를 넘는 통합은 거부한다. 원문과 이력은 남는다.
`memory/consolidations/pending/<id>.json`에 계획을 먼저 저장하고 각 변경을 원자적으로 쓴다.
중간 실패 시 일반 기억 CRUD/검색을 막고 명시적 복구만 허용한다. 복구는 각 레코드가 변경 전이나
예정된 변경 후인지 검증하며 외부 변경이 있으면 중단한다. 완료 영수증은 receipts에 남긴다.
이 기능은 모델의 통합 정확성을 자동 보장하지 않는다. 후보 검토는 실제 내용을 확인해야 한다.

## 검증과 한계

테스트는 Python 3.12.14 Linux에서 가짜 제공자와 실제 로컬 저장소를 사용한다. 승인 응답 및
명시적 재개, 만료 갱신, 저장 오류, 취소 지연/종료 소유권, 불확실 노드 재시도, 기억 병합의
부분 저장 복구·버전 충돌, 검색 격리/캐시, 큰 대화·Tool 교환의 원문 보존을 검증한다.
실제 모델의 요약 정확도·기억 재현율, 5 Session × 3시간 실행, 2GB 문서 부하와 배포 환경의
프로세스 격리는 별도 실측이 필요하다. 외부 시스템의 exactly-once 실행도 보장하지 않는다.

최종 전체 검사: Linux Python 3.12.14, **722개 통과**, 165.378초.
로그: `tests/llm/reports/history/domain-hardening-complete-suite-python312.txt`, 소스 해시 검토: `tests/llm/reports/history/domain-hardening-review.json`.

조회·계획 API의 데이터 클래스 전환은 [data-contracts.md](data-contracts.md)를 참고한다.
