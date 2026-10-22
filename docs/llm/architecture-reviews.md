# 개별 정적 후보의 분류와 무효화

`check_architecture`의 AST 후보는 확정 위반과 다르다. 실제 단일 선언, 검증된 boolean 조건,
산술 판정, 현재 형식의 정확한 타입 검사, 필수 upstream 콜백은 소유자와 계약을 확인해
개별 분류할 수 있다. 미해결 위반이나 실행·데이터 안전성 실패를 분류로 제외하지 않는다.

`tests/llm/fixtures/architecture_debt.json`은 미해결 목록이다. 승인 목록이 아니며,
기본 검사 통과가 P1 완료를 뜻하지 않는다. 검토한 발생은 별도
`tests/llm/fixtures/architecture_reviews.json`에 다음 근거를 함께 기록한다.

- 파일·심볼·정규화한 AST·발생 순서를 포함하는 정확한 finding ID, rule, owner, 개별 사유
- 의미를 정의한 현행 계약 문서
- 후보 파일 전체와 선언·검증기·필요한 소비자·시험·Fixture·계약 파일의 SHA-256
- 실제 존재하는 구체적 시험 ID

원본 findings는 보고서에 그대로 남는다. `reviewed`에는 유효한 개별 근거를, `outstanding`과
`required_outstanding`에는 미해결 후보를 구분해 표시한다. 필수 rule의 미해결 후보가 있으면
엄격 게이트는 계속 실패한다. 미해결 목록에 분류된 ID를 중복 보관해도 stale로 실패한다.

다음 경우 기본 검사도 실패한다: 발생 삭제/이동/변경, 중복 review, owner/rule 불일치,
후보·의존성·계약·시험·Fixture의 hash 변경, 누락된 시험 ID, 빈 사유/근거, 외부 파일 참조.
새 발생은 기존 파일/심볼에 추가되어도 승계하지 않는다. 파일·디렉터리·rule 전체 허용이나
자동 baseline/hash 갱신 기능은 없다. 변경으로 무효화된 개별 근거만 다시 검토한다.

현재 분류 대상은 S2–S5다. S1 구 API와 S6 Provider 경계는 이 목록으로
승인할 수 없다. 숫자·문자열을 다른 위치로 숨기거나 실제 owner 중복을 예외로 만들지 않는다.

시험 ID는 **검증 범위를 가리키는 근거**이며 시험 통과 증거가 아니다. 이 검사기는 시험을
실행하거나 과거 결과를 재사용하지 않는다. 현재 소스의 집중/통합/안전성 시험, R3 Mutation,
상위 단계 전체 LLM/Hub와 CI 필수 검증은 [검증 정책](verification-policy.md)을 그대로 따른다.
분류 파일이 바뀌면 이 검사기의 성공·실패 사례도 검증한다.

```bash
# 현재 CI 필수 규칙: P1의 S1–S5와 A의 S6를 모두 유지한다.
python -m tests.llm.check_architecture --require S1 --require S2 --require S3 --require S4 --require S5 --require S6 --output artifacts/architecture.json
# P1 종료: S1–S5의 모든 후보를 해결하거나 개별 근거로 분류해야 한다.
python -m tests.llm.check_architecture --require S1 --require S2 --require S3 --require S4 --require S5
# A 종료: S6도 실제로 해결한 뒤 전체 엄격 검사
python -m tests.llm.check_architecture --strict
```

기본 분류 파일은 검사 root 아래에서 찾는다. 없는 임시 fixture root에는 승인 항목이 없다.
명시한 `--reviews` 파일이 없으면 실패한다. CI는 저장소의 동일 분류 파일과 검사기를 사용하며
부분 테스트로 전체 필수 Job을 대체하지 않는다.


## A-c 호출 경계 전환 재검토

공통 모델 전환에서 변경된 source/consumer/test 근거를 다시 검토한다. 기존 후보의 AST identity와
owner가 같은 발생만 유지하고, Hub model_events의 SDK retry=0 발생은 Adapter로 옮겨 사라졌으므로
해당 개별 검토를 삭제한다. SDK retry 필드 목록은 providers.retry의 단일 선언을 실제 Adapter도
소비한다. S6 18건은 공통 모델 소비자로 전환하여 제거하며 S6 예외를 추가하지 않는다.

S2 조건은 명시 presence/boolean·실패 후 재시도·공급자 용량/누적 사용량의 기존 판단을 유지한다.
S3의 검증 경계·선택형 생성 preset·UI 관찰 의미는 유지하며 common message/Tool 정의를 실제
Adapter에서 변환한다. 특히 Tool 정의 전체가 binding에 반영되고 승인/효과/원장 owner는 그대로다.
S4는 정확한 현재 버전/필수 필드 검사와 CAS 의미를 유지한다. S5 표준 logging callback은 그대로다.
공통 호출 변경으로 테스트 fixture와 readonly 관찰 투영이 달라진 근거도 다시 고정한다. 개별
검토는 통과 증거가 아니며, 현행 참조 시험과 전체 회귀·R3·엄격 검사는 A 게이트에서 별도로 실행한다.
