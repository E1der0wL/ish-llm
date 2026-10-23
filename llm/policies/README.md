# 재사용 정책 알고리즘

Engine과 Component 등 여러 계층에서 쓸 수 있는 순수 처리 알고리즘을 둡니다.
Project 설정 저장·상속, Run/Step 수명, 승인, 재시도 실행을 중앙에서 관리하는 계층은 아닙니다.

| 파일 | 역할 |
|---|---|
| `completion.py` | CompletionPolicy. 전체 요청을 계수하고 과거 턴 단위로 입력을 선택합니다. |
| `errors.py` | 계층에 독립적인 ExecutionLimitError와 안정된 오류 코드입니다. |
| `retry.py` | RetryPolicy의 횟수·간격·기한 계산, RetryFailure/RetryDecision과 수정 인자 검증입니다. 실행 wrapper는 없습니다. |
| `usage.py` | 검증된 영수증 Projection의 Run/Project 한도와 동일 기간을 계산합니다. 실제 계수·예약 저장은 runtime이 담당합니다. |
| `__init__.py` | 위 공통 계약의 공개 import입니다. |

```python
from llm.policies import CompletionPolicy
from llm.core.implementation_parameters import ImplementationParameters

# 구현체가 자신의 policy.completion을 해석하고 호스트 계산기와 연결합니다.
parameters = ImplementationParameters.read(configuration)
policy = CompletionPolicy.from_config(parameters.policy.get(CompletionPolicy.NAME), counters)
if policy is not None:
    request = policy.prepare(request)
```

정책이 없으면 선택기를 만들지 않습니다. 알고리즘은 주입된 계산기를 사용하고 원본 요청을
변경하지 않습니다. 시스템 지시와 현재 요청의 Tool 호출·결과를 보존하며 그 부분만으로도
예산을 넘으면 `context_budget_exceeded`를 발생시킵니다. SDK의 출력 토큰 제한을 설정하지 않습니다.

공용으로 옮길 기준은 실제 여러 계층에서 재사용할 수 있고 저장·실행 수명에 독립적인가입니다.
RunPolicy 선언은 core/policies.py에, 정책 집행과 ToolRuntime는 해당 실행 서비스에 둡니다. Component 전용 처리 정책은 그
Component에 둡니다. 설정 소유권과 상속 순서는 [공통 설정 계약](../CONFIGURATION.md)을 따릅니다.

입력 선택 필드와 제약은 CompletionPolicy가, 공개 Schema 문법과 등록 자원 표시는
core/schema.py가 소유합니다. 생략된 설정·명시적 null·0을 원본에 합치거나 복원하지
않습니다. 계산기에 전달할 요청은 분리 복사하되 주입 client의 identity는 보존합니다.

재시도 선택기와 적용 단위는 [재시도 계약](../../docs/llm/retry.md)을 참고합니다. 정책이 없으면
추가 시도를 하지 않으며 오류/효과 판단·승인·영속 기록은 각 실행 소유자에 남습니다.
