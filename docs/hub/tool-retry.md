# Tool 인자 수정 재시도

Hub의 `ToolRetrySelector`는 `ToolRuntime.decide_retry`에 연결된다. Tool 이름에
관계없이 실패 인자, Tool 설명·JSON schema, `RetryFailure.changeable`, 현재 요청과
작업 디렉터리, 선택적인 근거 자료를 이용한다. 적격성·추가 시도 횟수·인자 검증·
재분류·권한·승인·실제 실행·체크포인트 저장은 llm이 소유한다.

## 설정과 범위

### 실행 전 인자 검증

Hub의 `ToolInputRegistry`는 백엔드의 `ToolRegistry.prepare`를 그대로 호출한다.
검증에 실패한 경우에만 공개 schema와 인자 제약으로 실패 지점을 확인하여
Tool 이름, 필드, 누락·타입·범위 오류를 예외에 붙인다. 예를 들어
`Tool 'file_read', offset: expected type integer`로 Step 진단에서 원인을 구분한다.
진단 때문에 핸들러를 호출하거나 이미 완료한 Tool을 다시 실행하지 않는다.
오류에는 인자 값 전체를 복사하지 않는다. 실제 실행 중 발생한 오류는 기존
Step/Tool 실패 계약을 따르며, 임의의 `ValueError`를 재시도 가능으로 바꾸지 않는다.

`file_read`는 원래 핸들러에서만 검사하던 문자/줄/마지막 줄 모드의 배타성을
Hub의 모델용 schema에도 선언한다. 정책이 고정 인자를 주입하는 경우 모델에도
그 인자를 명시하도록 요구하여, 기본 문자 페이지 제한과 줄 단위 인자를 함께
선택하는 실수를 줄인다. 명시적인 기존 제약·읽기 권한·선택된 Tool은 유지한다.

RAG는 백엔드가 리랭커 부재를 `rerank: false`로 선언한 경우에만 해당 인자를
필수로 표시한다. 리랭커가 있는 검색은 기존대로 동작하고, 저장된 리랭크 설정을
자동으로 끄지 않는다. 리랭커가 켜진 환경의 간헐적 오류가 이 문제 때문이라는
뜻은 아니다. 정확한 원인은 실패한 Step의 Tool 이름과 상세 진단으로 확인해야 한다.

검증: `tests.hub.test_tool_input_diagnostics`는 여러 정상 호출 뒤의 잘못된 인자,
Tool 선택/계약 보존, 문자·줄 읽기, 리랭커 유무를 검사한다.

### 재시도 정책

프로젝트 설정의 `config.policies.tool_retry.max_retries`는 최초 호출을 제외한
추가 시도 수이며, 동일 인자 반복과 인자 수정이 공유한다. `delay_seconds`는 시도
사이의 대기다. Hub의 새 프로젝트 기본값은 각각 `2`, `0`이다. 기존 프로젝트와
명시한 `0`/`null`은 변경하지 않는다. 기존 프로젝트는 설정 화면에서 활성화한다.

`recoverable=True`, `effect="none"`, ToolContract의 `retry_fields`가 있는 실패만
인자 수정 대상이다. 현재 내장 Tool에서는 `file_read`의 파일 없음이 해당한다.
다른 Tool도 이 계약을 선언하면 공통 선택기를 사용한다. 일반 예외, 불확실한
효과, 권한/경로 범위 오류를 Hub가 복구 가능 오류로 다시 분류하지 않는다.
안전한 일시 오류의 `retryable=True` 동작은 기존 동일 인자 재시도를 유지한다.

## 재시도 표시

모델 호출과 Tool 재시도는 `RetryMonitor` → `RetrySummary` → `retry_text`를 공통으로
사용한다. 어시스턴트 헤더에는 대상(모델 호출/Tool 이름), 최초 호출을 포함한 시도
횟수와 최대 시도 수를 표시한다. Tool 설정의 `max_retries=2`는 UI에서 최대 3회다.
인자 수정안을 만드는 동안에는 `수정안 생성 중`을 표시하며 소요 시간을 추정하지
않는다. 대기 시간은 확인된 재시도 이벤트와 해당 정책을 기준으로 로컬 계산한다.
Tool의 카운트다운은 이벤트 관찰 시점 기준이므로 저장·재승인 시간이 추가될 수 있다.
실제 실행 관측 전에는 다음 시도가 시작됐다고 표시하지 않는다.

Tool 관측은 Loop/Graph의 동일한 Engine 이벤트 경로를 사용하고, 동일 인자 재시도의
실행 시작은 Tool execution 관측으로 확인한다. 동시에 재시도하는 작업은 별도로
관리하고 가장 가까운 예정 시도를 헤더에 표시한다. 완료·실패·승인 대기·중단 또는
이벤트 스트림 종료 시 해당 Tool 표시를 정리한다. 렌더링 틱마다 저장소를 읽지 않는다.

## 기본 선택기

선택한 Engine의 completion 설정을 사용하며, 모델 설정이 없는 Graph 등에서는
프로젝트/세션의 Loop 설정을 사용한다. Agent 전용 모델 설정을 별도로 추정하지
않는다. 인자 수정은 추가 모델 호출 1회이며, 이전 Tool이나 Run 전체를 재실행하지
않는다. 이 보조 호출에는 Loop의 provider 재시도 정책을 상속하지 않는다.

추가 모델 호출은 현재 Run의 내부 LLM Step/Completion으로 전달되어 저장·사용량
제한·취소를 공유한다. 모델에 Tool 실행 권한은 제공하지 않는다. 반환된 JSON의
수정 키, 전체 인자 schema와 제약을 검사하며, 잘못된 결과·변경 없는 결과·모델
오류는 원래 Tool 실패를 유지한다. 사용량 제한과 취소는 그대로 전달한다.

`file_read`의 근거 제공자는 해당 요청에 캡처된 ish 작업 루트에서 제한된 파일명
후보를 모은다. 파일 본문·환경변수를 보내지 않으며, symlink·숨김 디렉터리는
탐색하지 않는다. 범위는 최대 4,096항목/6단계/약 0.15초이며 최대 12개 후보를
제공한다. `workspace_id`가 있는 Tool에는 셸 루트 후보를 섞지 않는다. 테스트
작업 공간 등 다른 소유권의 경로는 별도의 근거 제공자를 연결할 수 있다.

## 애플리케이션 확장

`HubConfig.tool_retry_evidence`는 Tool 이름과 근거 제공 함수의 매핑이다.
`tool_retry_chooser`를 지정하면 기본 모델 선택기를 교체한다. 두 함수 모두
`ToolRetryRequest`를 받으며, 동기 함수는 작업 스레드에서, async 함수는 백엔드
이벤트 루프에서 실행한다. 함수는 신뢰한 애플리케이션 코드다. 실행 권한을
넓히거나 Tool 부작용을 직접 실행하는 용도로 사용하지 않는다.

```python
from hub.backend.runtime import HubConfig
from llm.policies.retry import RetryDecision

async def format_evidence(request):
    return {"supported_formats": ["json", "csv"]}

async def choose_retry(request):
    if (request.failure.operation == "export_report"
            and "format" in request.failure.changeable
            and request.failure.arguments.get("format") == "JSON"):
        return RetryDecision({"format": "json"})
    return None

config = HubConfig(
    tool_retry_evidence={"export_report": format_evidence},
    tool_retry_chooser=choose_retry,
    project_config={"policies": {"tool_retry": {"max_retries": 2, "delay_seconds": 0}}},
)
```

`None`이면 중단한다. 반환값은 선언된 최상위 인자만 교체하는 `RetryDecision`이다.
변경 인자는 백엔드에서 다시 검증·승인되며, 이전 인자의 승인을 재사용하지 않는다.

## 수명과 호환성

함수와 Run별 문맥은 저장하지 않는다. 설치/재시작 시 Hub가 다시 연결한다.
`recovery_events`는 크기 1의 큐와 저장 완료 응답으로 내부 Completion과 기존
Engine 이벤트를 중계한다. 저장/사용량 승인 전에 다음 모델 호출이나 Tool을
진행하지 않으며, 재시도가 비활성화된 Run은 기존 이벤트 경로를 그대로 사용한다.

선택기 및 ToolRuntime revision 변경은 승인 binding에 영향을 준다. 변경 전의
대기 중 승인/체크포인트를 자동 변환하지 않는다. 최신 계약은 새 요청에서 사용한다.
