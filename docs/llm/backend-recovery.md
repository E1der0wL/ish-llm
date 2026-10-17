# 오류·복구 공개 API와 애플리케이션 연결

백엔드는 근거·실행 상태·허용 범위를 제공한다. 애플리케이션은 복구를 선택하고 요약 모델,
프롬프트, 표시 문구를 정한다. 새 실패 저장소, 일반 실행 Manager, 제품별 자동 재개 정책은 없다.
Hub 코드를 import하지 않고 아래 API를 사용할 수 있다.

## 공개 import

```python
from llm.llm import (
    Diagnostic, ErrorClassification, classify_diagnostic, CheckpointError,
    ResumePlan, JobResumePlan, CompletionRecovery, CompletionRepair, LoopEngine,
)
```

`Diagnostic.classification`은 `classify_diagnostic(diagnostic)`과 같다. category/reason,
confidence(`explicit`, `inferred`, `unknown`), evidence를 반환한다. 모든 공개 관찰 객체는
JSON 왕복이 가능하고 사본 수정이 저장 상태를 바꾸지 않는다. CompletionRecovery/Repair는
현재 호출에만 쓰는 callback 값이며 별도 영속 객체가 아니다.

Provider만 HTTP/SDK 메시지를 해석한다. 400 문맥 초과 근거와 400 인자 오류를 구분하며,
상태와 메시지는 동일 원인에서 가져온다. 인증·제한·5xx·synthetic 500 코드와 명시적 바깥
계약 오류를 보존한다. 원인 체인은 retry 이력이 아니다. 미분류 코드는 unknown으로 남는다.
info/warning 진단도 그대로 사용한다. 상세 원문·traceback 수집은 기존 ProviderDiagnostics의
명시적 설정을 따른다. Tool/Component 진단에 그 옵션을 자동 적용하지 않는다.

## 오류 조회

```python
async def inspect_failure(run, rag=None, job_id=None):
    result = await run.aresult()
    issue = result.diagnostic
    if issue is not None:
        print(issue.code, issue.classification.to_dict(), issue.source)
    for step in await run.steps.alist():
        if step.diagnostic is not None:
            print(step.id, step.diagnostic.to_dict())
    if rag is not None and job_id is not None:
        print(await rag.ajob_diagnostic(job_id))  # Diagnostic | None
```

Run.error/error_code, Step의 기존 metadata, Job의 기존 record가 원본이다. 진단 조회는 실행하거나
모델을 호출하지 않는다. 수집되지 않은 원문을 복원하지 않는다. 신뢰되지 않은 `.code`는
Run/Job의 실행 코드를 바꾸지 않고 `details.observed_code`로만 남는다.

예외가 `Diagnostic`을 저장된 속성 또는 계산형 property로 제공하면 공통 변환기가 동일하게
보존한다. Step와 Run은 같은 변환 경계를 사용하며 source만 각 관찰 위치를 나타낼 수 있다.
계산형 진단을 추가하는 새 구현체 때문에 공통 변환기에 구현체별 분기를 추가할 필요는 없다.

## 문맥 복구 callback

```python
def make_recovery(summarize_text):
    # summarize_text는 Application이 모델/프롬프트/정책을 명시하여 구성한 async 함수다.
    async def recover(failure: CompletionRecovery):
        candidates = [i for i in failure.editable
                      if failure.request["messages"][i]["role"] == "tool"]
        if not candidates:
            return None
        index = candidates[-1]  # 이 선택은 예제 Application 정책이다.
        summary = await summarize_text(failure.request["messages"][index]["content"])
        return CompletionRepair({index: summary})
    return recover

# engines={"loop": LoopEngine(recover_completion=make_recovery(application_summarizer))}
# 별도로 Project parameters.engines.loop.policy.provider.max_attempts를 명시한다.
```

콜백은 모델 입력 전체의 사본을 받지만 반환할 수 있는 수정은 editable 본문뿐이다. 잘못된
인덱스, 빈 본문, 본문이 줄지 않는 제안은 거부한다. 문자 수 감소는 토큰 수 감소의 증명이
아니므로 명시된 CompletionPolicy로 다시 검사하며, Provider가 재거부하면 남은 기존 예산
안에서만 처리한다. 이 문서의 선택 전략이나 수치는 백엔드 기본값이 아니다.

현재 요청 전체, system/developer, 현재 실행의 추가 지시는 보호한다. 요청 안의 자료 범위를
추측하여 자르지 않는다. 자료가 없거나 보호 내용 자체가 너무 크면 기존 실패를 반환한다.
현재 Tool 결과 본문을 줄여도 호출 ID/인자/역할/대응 관계와 영속 영수증은 보존한다.
축약이 의미를 완벽히 보존한다는 보장은 없으므로 원문 Step/대화는 계속 조회할 수 있다.

## Run 계획과 실행

```python
async def continue_run(session, run_id, engine):
    plan = await session.run.resume_plan(run_id, engine=engine)
    if plan.can_resume:
        return await session.run.resume(run_id, engine=engine, expected_revision=plan.revision)
    if plan.can_restart:
        return await session.run.restart(run_id, engine=engine, expected_revision=plan.revision)
    return plan  # UI가 blockers/interactions/restart_blockers를 표시한다.
```

이 예제의 자동 선택은 Application에만 속한다. 백엔드는 조회만으로 실행하지 않는다.
승인 응답을 저장한 뒤에는 계획을 새로 조회한다. 수정한 계획 객체가 권한이 되지는 않는다.
동시 요청이나 상태 변경은 `CheckpointError.code`로 거부되며 최신 계획을 다시 확인한다.
실제 resume도 기존 checkpoint digest·승인 binding·효과 불명확 검사를 다시 수행한다.
새 시작은 오직 서비스가 실행 dispatch 전에 중단됐다는 영속 근거가 있을 때만 허용한다.
이미 dispatch했거나 표시가 없는 기록은 Step이 없어도 새 시작을 허용하지 않는다.

## RAG Job 계획과 실행

```python
async def continue_job(rag, job_id):
    plan = await rag.aresume_plan(job_id)
    if plan.can_resume:
        return await rag.arun_job(job_id, retry=True, expected_revision=plan.revision)
    return plan
```

JobResumePlan은 Run/Engine 필드를 만들지 않는다. `active`는 실제 worker lease,
`abandoned`는 저장된 포기 증거를 반영한다. `checkpoints`는 저장된 후보이며 사용 직전에
signature와 vector/graph 무결성을 검증한다. completed/cancelled/abandoned Job은 재개하지 않는다.
worker가 사라진 running Job은 자동 실행하지 않고 명시적 retry만 허용한다. 설정 불일치와
원문 소실은 각각 incompatible/missing으로 차단한다. Job 전체 삭제 후 조회는 FileNotFoundError다.

`job_diagnostic/ajob_diagnostic`은 현재 시도의 진단이다. 명시적 재시도가 검증을 통과해
실제로 시작되면 이전 실패의 진단·단계·시각은 같은 Job의 `failures` 목록으로 옮기고,
현재 `error`, `diagnostic_code`, `diagnostic`, `failed_phase`, `ended_at`은 제거한다.
입장/CAS 검증이 실패하면 이 변경도 하지 않는다. 재시도 중에는 현재 진단이 없고,
성공하면 계속 `None`이며 진행 메시지에 과거 오류를 표시하지 않는다. 다시 실패하거나
취소되면 현재 시도의 새 진단을 기록한다. 이전 실패는 `await rag.ajob(job_id)`의
`failures`로 조회할 수 있으며, 이 관찰 이력은 자동 재시도나 승인 근거가 아니다.

기존 `acheckpoint_plan`, `aabandon_job`, `adelete_job` 및 Run의 동등한 API를 계속 사용한다.
포기와 삭제는 별도 연산이며 삭제가 실패해도 포기를 취소하지 않는다. corpus/완료 문서,
공유 VectorCache, 대화·효과 영수증은 Job/체크포인트 정리 대상이 아니다.

## Hub 연결 대응표

| 현재 Application 코드 | 사용할 백엔드 계약 | Application에 남는 역할 |
| --- | --- | --- |
| ErrorInfo/Provider 오류 자체 분류 | result/step/job의 Diagnostic + classification | 번역·표시, Hub 자체 오류 변환 |
| RecoveryFacts.backend_admitted | 실제 서비스의 계획·실행 검사 | 허용 후보 선택; 플래그로 권한 부여 금지 |
| 문맥 축약 후 전체 Run retry | LoopEngine.recover_completion | 요약 범위·모델·프롬프트 선택 |
| _unstarted_without_checkpoint | ResumePlan.can_restart/restart_blockers | 사용자에게 새 시도 선택 표시 |
| Job status로 retry 가능 추론 | rag.aresume_plan + arun_job(expected_revision=...) | 실행/포기/삭제 UX |
| checkpoint_conflict 문자열 파싱 | CheckpointError.code/blockers/diagnostic | 충돌 알림과 최신 계획 재조회 |

기존 submit/resume/rag.arun_job 호출 형식은 유지한다. 새로운 expected_revision은 resume과
Job retry에서 선택적이며, 새 restart에서는 필수다. Hub의 기존 자체 복구 전략을 이 API로
전환하는 작업은 별도다. 이번 백엔드 구현이 기존 Hub에 새 callback을 자동 주입하지 않는다.

## 제한과 검증 경계

- SDK/provider별 400 문구가 알려지지 않으면 unknown/request로 남는다. 텍스트로 추정한
  context 분류는 inferred이며, 실행 가능성은 별도 검증한다.
- 복구 callback 실패/기한 소진은 원래 실패와 거절 관찰을 유지한다. 취소는 원래 취소 경로를 탄다.
- 임의의 현재 본문 내부 span 교체나 system prompt 축약은 제공하지 않는다.
- 보조 요약이 필요하면 기존 요약 API와 Provider 관찰/사용량 경계를 사용한다. 임의 외부 SDK
  호출을 callback에서 직접 수행한 사용량까지 백엔드가 추측하여 집계하지 않는다.
- 새 Run dispatch 사실이나 Job 설정 지문이 없는 저장값을 채우는 migration은 없다.
- API 테스트는 가짜 Provider와 실제 로컬 저장소로 실행한다. 사내 모델의 chat template,
  endpoint별 오류 문구와 실제 요약 품질은 별도 실환경 검증이 필요하다.
