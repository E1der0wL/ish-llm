# 체크포인트 수명 계약

체크포인트는 실행을 복원하기 위한 소유자별 자료다. 새 영속 도메인이나 중앙 실행 권한 저장소가 아니다.
공통 계약은 `llm/core/checkpoints.py`의 `ResumeAbandonment`, `require_checkpoint_plan`과
`llm/core/plans.py`의 `CheckpointPlan`으로 표현한다. 파일 I/O나 Engine/Component payload 해석은 core에 두지 않는다.

## 소유권과 정리 단위

| 자료 | 의미·검증 소유자 | 저장·정리 소유자 | 수동 정리 단위 |
| --- | --- | --- | --- |
| Loop 체크포인트 | LoopEngine | RunRepository / CheckpointRepository | 선택한 Run의 체크포인트 전체 |
| Graph 체크포인트 | GraphEngine, 선택한 handler | 같은 Run 저장소 | 중첩 자료를 포함한 Run 전체 |
| Agent/중첩 Engine | 자식 Engine, EngineCheckpointScope의 부모 연결 | 부모 Run 저장소 | 독립 삭제하지 않음 |
| RAG 임베딩·추출·준비 결과 | RAG ingestion/extraction | RAGJobs / ComponentData | 선택한 Job 전체 |

Tool operation 원장, 승인 응답, Run/Step 기록, 저장 트랜잭션 저널, VectorCache는
일괄 삭제할 체크포인트가 아니다. 각 자료의 기존 소유권·수명 계약을 유지한다.

## 공통 규칙

1. 참조는 소유자 ID를 포함한다. ID나 조회 결과를 받았다는 것만으로 실행·삭제 권한을 부여하지 않는다.
2. 저장과 수정은 기존 workspace 잠금·StorageIO·트랜잭션 경계를 사용한다. Engine은 파일을 직접 쓰지 않는다.
3. 재개 시 입력·설정·구현 binding과 참조를 각 소유자가 검증한다. 다른 작업의 결과를 재사용하지 않는다.
4. 재개는 명시적이다. Run 재개는 새 Run을 만들고, RAG retry는 기존 Job의 검증된 결과를 재사용한다.
5. 취소, 재개 포기, 물리적 삭제는 별개다. 취소만으로 완료된 조각을 지우지 않는다.
6. 재개 포기는 되돌리지 않는다. 이후 작업은 새 요청/Job으로 시작하며 이미 발생한 효과를 되돌리지 않는다.
7. 삭제는 계획 조회 후 명시적으로 적용한다. 소유자가 잠금 안에서 계획을 다시 계산하고 revision을 검증한다.
8. 살아 있는 실행, 미처리 재개 예약, 필요한 후속 참조는 삭제를 막는다. 실행 중 worker를 삭제 API가 중지하지 않는다.
9. 파일 삭제와 소유자의 삭제 표식은 기존 트랜잭션에서 확정한다. 예외·프로세스 종료 때 복구해 부분 삭제를 노출하지 않는다.
10. 기간·용량 같은 보관 값은 명시 설정만 적용한다. 공통 계약에 숨은 보관 기본값을 넣지 않는다.

## 공통 조회 결과

`CheckpointPlan`은 다음 필드를 가진 JSON 직렬화 가능한 관찰 계약이다.

| 필드 | 의미 |
| --- | --- |
| `source` | Run 또는 RAG Job의 `ResourceRef` |
| `checkpoints` | 소유자 안의 체크포인트 이름/키. 개별 삭제 대상 선택자가 아님 |
| `bytes` | 정리 범위의 현재 파일 바이트 합계. Run 승인 증거 보존 때문에 실제 확보 용량과 다를 수 있음 |
| `abandoned` | 소유자가 저장한 재개 포기 여부 |
| `abandon_blockers` | 재개 포기 차단 사유 목록 (`Diagnostic`) |
| `delete_blockers` | 삭제 차단 사유 목록 (`Diagnostic`) |
| `revision` | 계획 적용 시 필요한 불투명한 변경 식별자 |

Python의 `can_abandon`, `can_delete` 속성은 각 blocker 목록에서 계산한다. 별도 영속 상태가 아니다.
`to_dict()` / `from_dict()`로 전달할 수 있다. 계획에는 문서·벡터·프롬프트 원문을 넣지 않는다.
`checkpoint_conflict`는 재조회가 필요하다는 뜻이며 자동 재승인하지 않는다.

계획 조회와 적용은 대상 파일의 내용 해시와 현재 참조를 확인한다. 큰 Job이나 긴 Session에서는
검사 비용이 커질 수 있고 그동안 같은 workspace의 저장 작업은 직렬화된다. 비동기 API는
파일 I/O를 이벤트 루프 밖에서 수행하지만 이 잠금을 생략하지 않는다.

Run의 계획 계산에서는 Session의 Run 목록과 체크포인트 해석을 작업 안에서 재사용한다.
승인 요청·응답·UI 상태도 같은 잠금 작업 안에서는 동일한 파일을 반복해서 읽지 않는다.
이 읽기 범위는 API 호출이 끝나면 폐기한다. 다음 승인·재개·포기·삭제 요청은 원본을 다시
검증하며, 파일 내용 해시·계보·승인 binding·삭제 CAS는 그대로 유지한다. 계획의 내용 해시는
해석된 JSON과 별도로 원본 파일 전체를 읽어 확인한다. 전역 승인 캐시나 저장 형식 변환은 없다.

## Run API

Run의 실행 상태는 변경하지 않는다. Session runtime이 연결되어 있으면 먼저 정상 종료해야 한다.
queued/streaming 요청이 남아 있으면 처리하거나 명시적으로 취소한 다음 계획을 조회한다.

```python
await session.run.shutdown()
run = await session.run.aload(run_id)
plan = await run.acheckpoint_plan()

# 애플리케이션에서 대상·차단 사유를 보여주고 재개 포기 의사를 확인한 경우
if plan.can_abandon:
    await run.aabandon_resume(expected_revision=plan.revision)

# 포기와 삭제는 독립 동작이다. 삭제 의사를 확인하고 새 계획을 사용한다.
plan = await run.acheckpoint_plan()
if plan.can_delete:
    await run.adelete_checkpoints(expected_revision=plan.revision)
```

동기 API는 `checkpoint_plan`, `abandon_resume`, `delete_checkpoints`다.
완료·취소 Run은 참조 보호를 통과하면 포기 없이 삭제할 수 있다.
paused/failed/interrupted Run은 먼저 명시적으로 재개를 포기해야 한다.
살아 있는 후속 재개 체인은 원본 정리를 막는다. 자식이 원본으로 fallback하는 경우에는
자식 체크포인트 정리를 먼저 완료해야 원본을 정리할 수 있다.
원본 파일이 이유 없이 없거나 승인/계보가 손상되면 삭제 허가로 추측하지 않는다.

`resume_abandonment`는 기존 `conversation_deleted` 사유와 독립 API의 `explicit` 사유를 표현한다.
대화 삭제 경로는 기존 `request_id`를 유지하고, 독립 포기는 `request_id=null`이다.
독립 포기는 대화를 삭제하지 않는다. Activity와 승인 UI 변경 알림은 저장 후에 발생한다.

삭제 시 `Run.state/checkpoints/` 전체를 제거하고 서비스 소유 `metadata.checkpoint_deletion`에
삭제 시각·계획 revision·이름과 조회용 승인 요청을 남긴다. 승인 요청은 기존 응답 영수증과 연결되는
감사 증거이며 실행 원본이 아니다. 원본 파일 삭제와 함께 확정되므로 활성 승인 원본을 이중 관리하지 않는다.
Run/Step/대화/승인 응답/Tool operation 원장은 유지한다.
삭제 표식이 있는 Run은 조회 fallback이나 명시적 resume으로 체크포인트를 되살릴 수 없다.
재개 포기 후 승인 응답·갱신·취소도 기존과 같이 거부한다.

Run 보관 정책은 기존 완료 Run 정리 범위를 유지한다. 이 API는 실패 Run 전체 삭제를 의미하지 않는다.

## RAG Job API

```python
rag = project.components.rag
plan = await rag.acheckpoint_plan(job_id)
if plan.can_abandon:
    await rag.aabandon_job(job_id, expected_revision=plan.revision)

plan = await rag.acheckpoint_plan(job_id)
if plan.can_delete:
    await rag.adelete_job(job_id, expected_revision=plan.revision)
```

동기 API는 `checkpoint_plan`, `abandon_job`, `delete_job`이다.
worker OS lease가 살아 있으면 포기·삭제를 모두 거부한다. 취소 요청 후에도 실제 worker가 끝나기 전에는 삭제할 수 없다.
프로세스 종료 후 남은 `running` 문자열은 실행 권한이 아니므로, lease가 해제된 작업은 명시적으로 포기할 수 있다.
queued/failed 작업도 먼저 포기해야 삭제할 수 있다. completed/cancelled 작업은 lease가 없으면 바로 삭제할 수 있다.

포기는 기존 Job 상태와 완료 조각을 보존하고 `resume_abandonment`를 추가한다.
`arun_job(..., retry=True)`도 포기한 Job을 거부하며 `arun_jobs()`는 포기한 대기 Job을 실행하지 않는다.
삭제는 `jobs/<id>/` 안의 입력·준비 자료·체크포인트·Job 기록을 제거한다.
공개된 문서·활성 generation·VectorCache·모델 사용량 영수증은 삭제하지 않는다.

성공한 등록은 기존처럼 `prepared.json`과 `batches/`를 정리한다. 실패·취소에서는 조각을 보존한다.
`policy.retention.job_max_age_seconds`와 명시적 Project maintenance는 완료·취소·명시적으로 포기한
Job을 정리할 수 있다. 포기한 Job의 보관 기간은 포기 시각부터 계산한다.
이 설정이 없거나 null이면 기간 기준 정리는 하지 않는다. 수동 `delete_job`에는 보관 기간 설정이 필요 없다.

## 변경하지 않는 계약

- Project → Session → Run → Step 및 Component 데이터 소유권
- 승인 요청/응답 binding, explicit resume, 불확실한 Tool 효과의 자동 재실행 금지
- RAG generation 원자적 공개, 조각 순서·벡터 무결성·fingerprint 검증
- 저장/Workflow/graph schema version과 기존 저장 자료 (자동 migration 없음)
- Hub UX 및 제품 보관 정책 (이 변경은 backend API만 제공)

회귀 테스트는 `tests/llm/test_checkpoint_lifecycle.py`와 기존 승인·재개·중첩 Engine·RAG 테스트에 있다.
