# 공통 데이터 계약

UI와 서비스 사이의 안정적인 필드는 데이터 클래스로 제공한다. 이 객체들은 실행 도메인이나
저장소가 아니다. Project → Session → Run → Step 및 컴포넌트의 소유권을 유지한다.
프로젝트 설정, Component 정의, LiteLLM 인자, Workflow 상태와 도메인별 세부 내용은 열린
JSON dict를 유지한다. 모든 dict를 고정 스키마로 바꾸지 않는다.

## 공개 타입과 반환 API

`llm.llm`에서 직접 import하거나 `plugin.get("llm")`에서 가져온다.

| 클래스 | 사용 위치 | 핵심 속성 |
| --- | --- | --- |
| Diagnostic | 실행 한도/요청/Tool 예외의 diagnostic, ExecutionResult.diagnostic, Step.diagnostic, 계획의 진단 | code, message, severity, source, details |
| ResourceRef | 진단·계획·진행 정보의 source | kind, id, project_id, session_id, run_id, step_id, component, version |
| SessionRuntimeView | session.run.status/astatus | session_id, status, active_run_id, engine, started_at, queued_count, queued_request_ids, unfinished_work |
| RunView | run.view/aview | run, outputs, cursor, events |
| ResumePlan | session.run.resume_plan | source, engine, can_resume, interactions, reused, retry_nodes, blockers |
| TurnDeletionPlan | session.delete_turn_plan | source, message_ids, blockers, revision |
| RecoveryPlan | project.recovery/arecovery(apply=False) | source, issues, repair_sessions, version |
| RecoveryResult | project.recovery/arecovery(apply=True) | applied, journal, remaining |
| RetentionPlan | project.retention/aretention | source, policy, candidates, protected, bytes_before/after, tokens_before/after, version, estimated |
| OperationProgress | EngineEvent.progress, Step.progress, rag.job_progress/ajob_progress | source, phase, completed, total, unit, message |

각 클래스에 `to_dict()`/`from_dict()`가 있다. JSON 필드 타입과 알 수 없는 최상위 키를
검사하며 중첩 값은 분리된 사본으로 전달한다. `details`와 정책·후보 레코드 내부는 자유로운
JSON 확장 영역이다. dataclass의 속성 할당은 막지만 내부 dict/list는 읽는 쪽에서 편집할 수
있다. 그 편집은 저장소나 실행 중 상태에 적용되지 않는다.

```python
from llm.llm import RunView, Diagnostic

status = await session.run.astatus()
print(status.queued_count, status.unfinished_work)

view = await run.aview()
payload = view.to_dict()              # json.dumps(payload)로 UI에 전송
restored = RunView.from_dict(payload) # Run/Path/Enum과 출력 타입도 복원
for output in restored.outputs:
    print(output.text)

result = await run.aresult()
if result.diagnostic is not None:
    print(result.diagnostic.code, result.diagnostic.message)

try:
    await session.run.submit("request", engine="missing")
except Exception as error:
    issue = getattr(error, "diagnostic", Diagnostic.from_exception(error))
    print(issue.to_dict())
```

진단은 설명과 UI 분류를 위한 것이다. severity는 info/warning/error이고 code는 확장 가능한
문자열이다. ToolExecutionError의 effect/retryable 정보는 details에 표시하지만 이 값만으로
Tool을 재실행하지 않는다. 기존 ToolRuntime·효과 원장·명시적 승인 검사가 계속 실행을 결정한다.
ResourceRef를 역직렬화한 것만으로 대상 조회·수정 권한을 얻지 않는다. 대상 API가 소유권을 검사한다.

## 검토와 실행 분리

`TurnDeletionPlan`은 `session.adelete_turn_plan(request_id)`가 반환한다.
`source`는 턴의 사용자 메시지 참조, `message_ids`는 삭제 대상, `blockers`는 이를 참조하는
미완료 체인의 마지막 Run 진단, `revision`은 조회 원본의 변경 식별자다.
각 blocker의 `source.run_id`와 `details`의 `status`, `engine`, `message_ids`를 사용자에게 보여준다.
계획은 실행 권한이 아니며 삭제 서비스가 실제 참조와 revision을 재검증한다.

```python
plan = await session.adelete_turn_plan(request_id)
# 영향받는 Run 전부를 보여주고 재개 포기 확인을 받은 뒤:
confirmed_run_ids = [issue.source.run_id for issue in plan.blockers]
await session.run.shutdown()
await session.adelete_turn(
    request_id, abandon_runs=confirmed_run_ids, expected_revision=plan.revision,
)
```

포기와 논리 삭제는 원자적으로 적용된다. 취소/확인 실패 시 이 API를 호출하지 않는다.
`abandon_runs` 생략은 기존 참조 보호를 유지한다. 재개 포기는 이미 발생한 효과를 되돌리지 않는다.

```python
plan = await session.run.resume_plan(run.id, engine=run.engine)
for issue in plan.blockers:
    print(issue.code, issue.message, issue.source)
# 사용자가 필요한 응답을 저장한 뒤 재조회한다. 미리보기 자체는 실행하지 않는다.
if plan.can_resume:
    request = await session.run.resume(run.id, engine=run.engine)

await session.run.shutdown()
recovery = await project.arecovery()
print(recovery.issues, recovery.repair_sessions)
# 사용자 검토 후 호출
result = await project.arecovery(apply=True, expected_version=recovery.version)
print(result.remaining.issues)

retention = await project.aretention()
print(retention.candidates, retention.protected)
# 영구 삭제 대상 검토 후에만 호출
await project.aretention(apply=True, expected_version=retention.version)
```

Resume/Recovery/Retention은 별도 클래스를 유지한다. 공통 `execute()`나 범용 Manager를
추가하지 않는다. 복구·보관 적용 시 원본 버전을 다시 검사하고, 재개는 Engine/승인/체크포인트
정합성을 다시 검사한다. 사용자가 계획 객체를 편집해도 실행 대상이나 권한이 바뀌지 않는다.
RecoveryPlan.issues의 복구 가능 여부는 `issue.details["repairable"]`에 표시된다.
Retention 후보·보호 레코드는 Session/Run 단위별 정보가 달라 열린 dict로 유지한다.

## 진행 표시

```python
for step in await run.steps.alist():
    if step.progress is not None:
        print(step.progress.phase)
    if step.diagnostic is not None:
        print(step.diagnostic.message)

job = await project.components.rag.aenqueue_document(title="Manual", content=markdown)
progress = await project.components.rag.ajob_progress(job["id"])
print(progress.source, progress.phase, progress.total)
```

기본 Step 이벤트는 phase를 제공하고 개발자는 EngineEvent.progress에 OperationProgress를
넣어 실제 단위/처리량을 전달할 수 있다. 서비스가 Step 참조를 검사하고 metadata에 JSON으로
저장한다. Step 종료 시 저장된 진행 phase도 종료 상태로 갱신하므로 중단 후 running이 남지
않는다. 기본 RAG 작업 진행은 저장된 phase를 투영한다. 전체량을 계산하지 않으므로 total은
None이며 추측한 퍼센트를 제공하지 않는다. RAG 작업 때문에 Session/Run을 새로 만들지 않는다.
Run 성공 여부는 RunStatus로, RAG 작업 완료 여부는 job.status로 판단한다. phase만 보고
성공이나 재실행 가능 여부를 추론하지 않는다.

## API 변경

기존 `status["queued_count"]`, `view["cursor"]`, `plan["version"]` 대신
`status.queued_count`, `view.cursor`, `plan.version`을 사용한다. 복구 적용 결과는
`result.remaining.issues`로 조회한다. 사전이 필요한 UI 경계에서만 `.to_dict()`를 호출한다.
기존 dict 접근 별칭은 만들지 않는다. Component maintenance, 설정 스키마·설정 스냅샷,
RAG 검색 결과와 Component 레코드는 계속 dict 계약을 사용한다.

Project/Session/Run/Step 저장 버전과 저장 경로는 그대로다. Step metadata에 관찰 필드를 추가하며
별도 조회 결과 파일은 만들지 않는다. 기존 기록에 새 관찰값이 없으면 progress는 None이고
오류 문자열이 있으면 진단을 조회 시 구성한다. 복구 감사 저널에는 plan.to_dict()를 저장한다.

검증: Linux Python 3.12.14, 전체 733개 테스트 통과(156.838초), 새 계약 테스트 11개.
로그: `tests/llm/reports/history/contracts-verified-suite-python312.txt`, 검토: `tests/llm/reports/history/contracts-review.json`.
