# 서비스 코드 배치

```text
services/
├── api.py                    # LargeLanguageModel이 제공하는 탐색형 Facade
├── configuration.py          # 서비스와 저장소 구성
├── query.py                  # 목록 조회 조건
├── results.py                # Project/Task 범위의 Run 결과 조회
├── lifecycle/
│   ├── projects.py           # ProjectRepository / ProjectManager
│   ├── tasks.py              # TaskRepository / TaskManager / TaskRuntime
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
추가하지 않는다. TaskRuntime의 정의는 Task 서비스에 있고 실행과 수명은 RunManager가
소유한다. Conversation은 Task에 속하며 Engine은 계속 이벤트를 통해 서비스에 전달한다.

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


공개 Task/Run 조회와 재개·복구·보관 계획은 core/views.py와 core/plans.py의 데이터 클래스를
반환한다. 진단·출처·진행 표시는 core/contracts.py의 계약을 사용한다. 실제 상태 전이/저장과
버전 검증은 서비스 책임이다. [공통 데이터 API](../../docs/data-contracts.md)를 참고한다.
