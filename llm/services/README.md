> 설정은 [명시적 설정 계약](../CONFIGURATION.md)을 따른다. 미설정 정책을 생성하지 않으며, SDK 옵션은 생략한다.

# 서비스 코드 배치

```text
services/
├── api.py                    # LargeLanguageModel이 제공하는 탐색형 Facade
├── configuration.py          # 서비스와 저장소 구성
├── query.py                  # 목록 조회 조건
├── results.py                # Project/Session 범위의 Run 결과 조회
├── lifecycle/
│   ├── projects.py           # ProjectRepository / ProjectManager
│   ├── sessions.py              # SessionRepository / SessionManager / SessionRuntime
│   ├── steps.py              # StepRepository / StepManager / StepEventRecorder
│   ├── components.py         # ComponentData
│   └── access.py             # Project 소유권과 접근 검사
├── runtime/
│   ├── runs.py               # RunRepository / RunManager / RunEventPublisher
│   ├── events.py             # 확장 이벤트 처리와 관찰자 구독
│   └── tools.py              # ToolExecutor
├── history/
│   ├── conversation.py       # 파일/메모리 대화 저장과 프로젝트별 선택
│   └── context.py            # Run 문맥과 복제할 대화 구성
└── infrastructure/
    ├── storage.py            # 원자적 저장, 파일 삭제와 비동기 I/O
    ├── locking.py            # 프로세스 소유권과 잠금
    └── logging.py            # 도메인 로그
```

폴더는 코드를 찾기 위한 구분이다. Repository/Manager를 분리하거나 실행 계층을
추가하지 않는다. SessionRuntime의 정의는 Session 서비스에 있고 실행과 수명은 RunManager가
소유한다. Conversation은 Session에 속하며 Engine은 계속 이벤트를 통해 서비스에 전달한다.

새 코드는 책임별 경로를 사용한다.

```python
from llm.services.lifecycle.projects import ProjectManager, ProjectRepository
from llm.services.runtime.runs import RunManager
from llm.services.history.conversation import ConversationStore
from llm.services.infrastructure.storage import StorageIO
```

이전 평면 import 경로와 `_compat/`는 제거했다. import, monkeypatch, 클래스 참조에
위 책임별 경로를 사용한다. 하위 패키지는 관련 서비스를 일괄 import하지 않아
Component/Engine과의 순환 의존을 피한다.

RunManager의 저장소는 `repository=`와 `.repository`로 주입/조회한다.
ComponentData 정의 생성은 `create/acreate`로 통일한다. Registry의 `register`는
런타임 등록 기능이며 별도의 API다. 비동기 I/O API는 계속 `a` 접두사를 사용한다.

서비스 클래스는 다음 순서를 따른다.

1. 클래스 설명, 필드와 초기화 코드
2. `_`로 시작하는 내부 구현 메서드
3. 공개 속성·메서드와 `__call__` 같은 호출 프로토콜
4. 공개 동기 메서드를 바탕으로 구성하는 비동기 API

getter/setter와 `async_method`처럼 클래스 정의 시 참조하는 항목의 순서는 유지한다.
Enum 값과 dataclass 필드 순서도 바꾸지 않는다.


공개 Session/Run 조회와 재개·복구·보관 계획은 core/views.py와 core/plans.py의 데이터 클래스를
반환한다. 진단·출처·진행 표시는 core/contracts.py의 계약을 사용한다. 실제 상태 전이/저장과
버전 검증은 서비스 책임이다. [공통 데이터 API](../../docs/llm/data-contracts.md)를 참고한다.

## Run 상태 전이

`core.models.validate_run_transition`은 상태 변경의 허용 여부만 검증한다.
시각 생성, 저장, Step/Conversation 변경은 계속 기존 서비스가 담당한다.

| 경로 | 이전 상태 | 다음 상태 |
| --- | --- | --- |
| 시작 (`start`) | PENDING | RUNNING |
| 종료 (`finish`) | RUNNING | COMPLETED, FAILED, INTERRUPTED, PAUSED |
| 복구 (`recovery`) | PENDING, RUNNING | INTERRUPTED |

종료한 Run은 다시 열거나 종료하지 않는다. 명시적 재개는 기존 체크포인트를 참조하는
새 Run을 생성한다. 대기 요청의 취소는 Message의 CANCELLED이며 새 Run을 만들지 않는다.
RunStatus.CANCELLED 저장 enum은 유지하지만 새 실행 전이는 추가하지 않는다.

RunManager의 내부 불변 `_RunOutcome`은 종료 상태·오류·오류 코드를 함께 전달한다.
COMPLETED/PAUSED에 실패 정보를 섞는 조합은 거부한다. `_finish`는 전이 규칙과
Session의 current_run_id를 먼저 확인하므로 중복/늦은 종료가 다음 Run의 소유권을
해제하거나 기존 종료 기록을 덮어쓰지 않는다. 이 검사는 Session 소유권을 가진
기존 직렬 I/O 경로 안에서 수행하며 새 잠금이나 범용 상태 저장기를 만들지 않는다.

실행 순서는 `_worker → _begin → _consume_limited → _finish`를 유지한다.
시작/종료 트랜잭션, Step 정리 → Assistant 상태 → Run 저장 → Session 저장 순서와
저장 성공 후 Run 알림·관측 전달도 유지한다. 저장 실패 시 완료 알림을 보내지 않는다.
복구는 같은 순수 전이 검증만 공유하며 기존 복구 I/O를 그대로 사용한다.
