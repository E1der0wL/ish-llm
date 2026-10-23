# 오류 관측과 분류의 소유

현행 [공유 계약](shared-contracts.md)에 따라 `llm/errors.py`의 `CodedError`가 명시적으로
분류된 오류인지와 유효한 code인지를 판정한다. 일반 SDK 예외의 code 속성만으로 실행
분류 권한을 부여하지 않는다. `stable_error_code`는 기존대로 바깥쪽의 분류된 오류를
우선하고 명시적 cause, 숨기지 않은 context 순서로 탐색한다.

`ErrorObservation`은 선택적 속성의 관측 실패 처리와 관측 체인 상한을 선언한다.
관측 속성이 일반 Exception을 발생시키면 정보 없음으로 처리하고 원래 실패를 보존한다.
취소 등 BaseException은 전파한다. 기존 32개 체인 상한과 순환 차단은 유지한다.
이 상한은 오류 관측의 작업량 제한이며 모델/Tool 재시도·실행 예산과 다르다.

Provider 분류는 공통 Owner가 탐색한 뒤 임의의 code getter를 다시 읽거나 신뢰 판정을
복제하지 않는다. 기존 HTTP 상태·timeout·연결 등의 Provider 분류 규칙은 그대로다.
Tool worker는 같은 분기에서 얻은 코드 값을 응답에도 재사용한다. 분기 판단과 전송 사이에
getter를 다시 실행해 다른 코드를 전송하지 않는다. Worker의 ToolExecutionError 효과·
재시도·복구 가능성, 미분류 실패의 불확실한 효과 처리와 취소/프로세스 정리는 유지한다.

이 변화는 관측 결과를 근거로 승인·예산·Tool 실행을 허가하거나 자동 재시도를 추가하지 않는다.
실패 재현, 실제 Linux worker, 호출/효과 횟수 및 원본 오류 보존 검증은
[전수 소유권 검증 기록](whole-ownership-verification.md)에 남긴다.
