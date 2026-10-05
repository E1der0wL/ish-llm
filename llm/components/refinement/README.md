# Refinement — 실행 근거에서 리소스 개선안 만들기

Refinement는 Project의 **제안과 적용 이력**을 보관합니다. 모델을 학습시키거나 완료된
Run을 다시 실행하지 않습니다. 분석은 일반 Loop/Agent/Workflow Run에서 수행하며
모델 호출과 Tool 사용은 기존 Run/Step에 남습니다. 자동 background 모델 호출은 없습니다.

```text
Run/Step 원본 조회 → 모델 분석 → proposal → structural validation → evaluation → approval → apply
```

대상은 같은 Project에서 선택한 skills/prompts/agents/memory의 **저장된 레코드 한 개**입니다.
Engine 생성자 system prompt, Runtime 코드, Tool/승인 정책은 대상이 아닙니다.
Agent의 일반 update에서는 purpose/description만 변경할 수 있습니다. Skill resource 설명은
실행 권한을 부여하지 않습니다. 각 patch는 최상위 키 교체이며 일반 JSON Patch 문법이 아닙니다.

## UI / Python API

```python
# Project 생성/선택 시 refinement와 수정할 대상 컴포넌트를 명시합니다.
refine = await project.components.aget("refinement")
target = {"component": "skills", "identifier": "review-guide"}
current = await refine.atarget_snapshot(target)
proposal_id = await refine.acreate({
    "target": target,
    "operation": "update",
    "expected_version": current["version"],
    "reason": "검증 오류를 확인하기 전에 완료로 처리했다.",
    "evidence": [{"session_id": session.id, "run_id": prior_run.id}],
    "patch": {"instructions": "검증 결과를 확인하고 실패한 검사는 먼저 수정한다."},
})
# 여기까지 target은 바뀌지 않습니다.
view = await refine.asnapshot(proposal_id)
await refine.avalidate(proposal_id)

# 사용자가 UI에서 승인했을 때만 호출합니다. approve는 실행을 시작하지 않습니다.
approved = await refine.aapprove(proposal_id, expected_version=view["version"])
applied = await refine.aapply(proposal_id, expected_version=approved["version"])

# 적용 후 수정된 대상은 덮어쓰지 않습니다.
reverted = await refine.arollback(proposal_id, expected_version=applied["version"])
```

동기 이름도 제공합니다. list/load/snapshot, reject, fail을 사용할 수 있습니다.
`fail(..., reason=...)`은 검증자가 적용 불가능한 제안을 명시적으로 종료하는 API입니다.
기존 제안의 patch/target/승인 상태를 save/update로 바꾸지 않습니다. 새 제안을 생성하세요.
개별 delete도 차단하여 before snapshot과 승인 이력이 사라지지 않게 합니다.
Project/Component 전체 영구 삭제는 기존 수명 관리 API의 명시적 작업입니다.

## 모델 Tool과 승인

선택 시 `refinement_target`, `refinement_evidence`, `refinement_read`를 제공합니다.
`policy.proposal_operations=["update"]` 등을 명시하면 그 연산만 받는 `refinement_propose`를
추가합니다. update/create/fork/bind_skills를 선택할 수 있으며 제품 프리셋 이름은 없습니다.
evidence Tool은 현재 Session의 Run과 선택한 Step의
원본 사본을 반환합니다. Step ID는 기존 실행 조회 API/UI에서 얻어 전달할 수 있습니다.
proposal 생성 Tool의 source에는 분석 Run/Step ID가 자동으로 기록됩니다. 사용 모델은
그 Run의 Completion 기록에서 확인합니다. 별도 trajectory 저장소는 만들지 않습니다.

예를 들어 호출 애플리케이션은 일반 요청으로 분석을 시작할 수 있습니다. 프롬프트와
실행 엔진 선택은 호출자가 정하며 라이브러리가 자동 생성하지 않습니다.

```python
handle = await session.run.submit(
    f"Run {prior_run.id}의 실패 근거를 refinement_evidence로 읽고, "
    "skills/review-guide를 refinement_target으로 조회하세요. "
    "검증 순서 개선이 필요하면 refinement_propose로 제안만 저장하세요.",
    engine="loop",
)
analysis_run = await handle.wait()
```

실제 적용 Tool이 필요하면 다음을 명시합니다.

```python
await refine.aconfigure({"policy": {"proposal_operations": ["update"], "apply_tools": True}})
```

이 경우 `refinement_apply`, `refinement_rollback`을 추가합니다. 둘 다
`ToolContract.approval_required=True`이며 호스트 ToolPolicy.authorize가 필요합니다.
ASK이면 적용 전에 Run이 멈춥니다. 기존 InteractionResponse/명시적 resume를 거쳐
ToolExecutor가 승인한 뒤에만 proposal approval와 apply를 수행합니다.
모델에 독립적인 approve Tool을 노출하지 않으며 권한·receipt·재시도를 구현하지 않습니다.
직접 Python approve/apply는 신뢰하는 UI/호스트 API입니다. 사용자 확인 없이 호출하지 마세요.

## 저장·버전·실패

- `project/refinement/records/<id>.json`: immutable 제안, evidence 참조, before,
  target expected_version, status/history, applied_target(식별자/적용 후 버전).
- 상태: proposed → approved → applied → rolled_back. proposed/approved에서 rejected/failed 가능.
- 일반 target은 기존 JSON 내용 해시 expected_version을 사용합니다. Agent도 같은 정의 지문을
  검사하고 공개 revise를 사용합니다. Memory는 공개 revision API를 그대로 사용합니다.
- proposal 버전과 target 버전을 모두 검사합니다. stale 변경은 거부하고 자동 재병합하지 않습니다.
- apply/rollback은 기존 workspace 잠금 및 multi-file transaction 안에서 대상 공개 API와
  proposal 기록을 함께 저장합니다. 저장 실패 시 applied로 표시하지 않습니다.
- 실패 시 예외를 호출자에게 전달하고 기존 상태를 보존합니다. UI가 필요한 경우 fail로
  종료할 수 있습니다. 불확실한 저장 실패를 자동 재시도하거나 효과를 다시 실행하지 않습니다.
- clone에는 원본 Project의 proposal/승인을 복사하지 않습니다. 새 Project에서 새 근거로 제안하세요.
- evidence/분석 Run은 history_references로 보관 참조를 제공합니다. 원본 Run/Step이 source of truth입니다.

Memory 적용은 **새 candidate**를 만들며 before의 확정 기억은 바꾸지 않습니다.
`metadata.replaces=[{id, revision}]`와 refinement_proposal을 저장하므로 기존 review/consolidate로
검토·통합합니다. rollback은 아직 바뀌지 않은 candidate만 soft-delete합니다.
이미 통합/수정된 candidate를 과거 상태로 강제 복원하지 않습니다.

## 파일

| 파일 | 역할 |
| --- | --- |
| __init__.py | 공개 Component/Data import |
| component.py | 설정/레코드 schema, capability, clone/보관 참조 |
| data.py | 근거 검증, immutable proposal, 승인, 대상 CAS와 원자 적용/되돌리기 |
| tools.py | 일반 Run에서 사용하는 읽기·제안·승인 대상 Tool 연결 |

분석 품질은 모델·근거·지침에 달려 있습니다. JSON 검증은 개선의 의미적 정확성을 보장하지 않습니다.
첫 버전은 명시적 refinement만 제공하며 자동 on-finish 실행, 복수 대상 원자 변경, 외부 효과 재실행은 하지 않습니다.

## Skill 생성·분기와 별도 바인딩

`operation="create"`는 새 Skill의 전체 정의를 patch로 받고 `expected_version=None`을
요구합니다. 대상 ID가 이미 있으면 거부합니다. `fork`는 아래처럼 부모 ID/버전을 추가합니다.
부모 정의에 patch를 적용하고 코드가 lineage를 생성합니다. 모델은 lineage를 직접 쓰지 않습니다.

```python
parent = await project.components.skills.asnapshot("review-guide")
proposal_id = await refine.acreate({
    "operation": "fork", "target": {"component": "skills", "identifier": "review-python"},
    "expected_version": None,
    "parent": {"identifier": "review-guide", "version": parent["version"]},
    "patch": {"instructions": "Python 작업에서는 기존 테스트와 변경 동작을 함께 검사하세요."},
    "reason": "실행 근거에서 별도 Python 지침이 필요함을 확인했다.",
    "evidence": [{"session_id": session.id, "run_id": prior_run.id}],
})
```

적용 전 부모 변경, 대상 ID 충돌, 잘못된 정의는 실패합니다. 부모의 후속 변경은 자식에
전파하지 않습니다. 새 Skill은 Agent에 자동 연결되지 않습니다. 별도 `bind_skills` 제안은
Agent target/expected_version과 `patch={"skills": ["review-python"]}`를 받습니다.
이는 resources.skills만 교체하며 Tool/Engine/Policy/Prompt/RAG/MCP는 그대로 유지합니다.
제안 때 선택 Skill들의 버전을 고정하고 적용 때 다시 확인합니다. 빈 목록은 연결 해제입니다.
생성된 Skill의 rollback은 현재 Agent 참조가 없어야 하며, 그 외 rollback도 적용 후 CAS를 지킵니다.
NO-OP은 분석 결과이며 저장을 변경하는 proposal operation이 아닙니다.

## 평가와 승인

`validate`는 구조와 참조/CAS만 검증합니다. 의미적 개선 여부는 앱이 선택한 테스트,
validator, Tool 또는 LLM judge가 판단합니다. Backend는 evaluator를 자동 선택/호출하지 않습니다.

```python
view = await refine.asnapshot(proposal_id)
validation = await refine.avalidate(proposal_id)
evaluated = await refine.arecord_evaluation(proposal_id, {
    "evaluator": "application/test-suite-v1",
    "baseline_version": validation["baseline_version"],  # create: None, fork: 부모 버전
    "candidate_version": validation["candidate_version"],
    "results": {"passed": True},   # 호출자가 실행한 검사 결과
    "regressions": [], "improvements": ["누락된 검증 추가"],
    "evidence": [{"session_id": session.id, "run_id": evaluation_run.id}],
}, expected_version=view["version"])
```

평가는 immutable proposal에 append되며 대상은 바뀌지 않습니다. fork의 baseline은 부모 정의이며 부모 버전은
proposal.parent.version에 보존됩니다. evidence Run/Step 참조는 보관 보호에 포함됩니다.
`policy.require_evaluation=True`이면 평가 기록 없이 승인·적용할 수 없습니다. 점수 임계값,
자동 합격/승인, 평가 결과에 따른 자동 UPDATE/CREATE/FORK는 없습니다. 평가 결과의 신뢰는
호출한 host/evaluator의 책임입니다. 모델에 평가/approve 위조 Tool을 제공하지 않습니다.
Tool apply는 기존 approval_required/ToolPolicy/Interaction 경계를 사용하고, trusted Python
approve/apply는 UI·host API로 유지합니다. 구조 검사 통과가 승인이나 평가 통과를 의미하지 않습니다.
