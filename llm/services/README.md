# Services — 공개 API와 도메인 수명 관리

서비스는 UI/Engine과 영속 데이터 사이를 연결합니다. 일반 사용자는 [LargeLanguageModel](../README.md)의 핸들 API를 사용하고, 저장소·처리기를 주입하는 개발자는 이 폴더를 살펴보면 됩니다.

```text
LargeLanguageModel → api.py의 핸들
  ├ lifecycle: 생성·설정·복제·삭제
  ├ runtime: 요청·실행·승인·이벤트
  ├ history: 대화·문맥·보관·복구
  └ infrastructure: 저장·잠금·트랜잭션·로그
```

## 파일과 하위 폴더

| 파일/폴더 | 역할 |
| --- | --- |
| [__init__.py](__init__.py) | 서비스 패키지 설명입니다. |
| [api.py](api.py) | Project/Session/Request/Run/Step/Component를 탐색하는 공개 Facade입니다. |
| [configuration.py](configuration.py) | ServiceConfig로 저장소·factory·호스트 자원을 한 번 구성합니다. |
| [schema.py](schema.py) | 등록된 Engine/Component를 반영한 UI 설정 schema와 유효 설정을 계산합니다. |
| [query.py](query.py) | Query의 상태·위치·개수 등 목록 필터 계약입니다. |
| [results.py](results.py) | Project/Session에서 Run 소유 결과를 모아 조회합니다. 별도 결과 파일을 만들지 않습니다. |
| [lifecycle/](lifecycle/README.md) | Project/Session/Step 및 ComponentData의 수명 관리입니다. |
| [runtime/](runtime/README.md) | RunManager, ToolExecutor, 체크포인트·승인·이벤트·사용량 처리입니다. |
| [history/](history/README.md) | ConversationStore, 문맥 정책, 복구·보관입니다. |
| [infrastructure/](infrastructure/README.md) | 공통 I/O, 잠금, 트랜잭션, 프로세스, 로그·관찰입니다. |

## 공개 API를 사용할 때

비동기 UI는 `acreate/aload/asave/alist` 같은 I/O API를 사용합니다. `submit/steer/wait/interrupt/shutdown`은 원래 비동기입니다. Facade는 백엔드에서 구성한 같은 저장소·ConversationStore factory·StorageIO를 실행과 조회에 사용합니다.

반환되는 핸들은 실행 서비스를 연결하는 객체이며 JSON으로 저장하는 도메인 모델이 아닙니다. `aget_data()`는 독립된 도메인 스냅샷을 반환하고, 그 객체를 변경해도 저장되지 않습니다. 명시적 저장 API를 사용하세요.

설정은 [ProjectConfig 계약](../CONFIGURATION.md)을 따릅니다. ServiceConfig는 구현체와 호스트 공유 자원을 주입하는 곳이며 Project의 정책 값을 대신 생성하지 않습니다.

## Run 상태 전이

| 작업 | 이전 상태 | 다음 상태 |
| --- | --- | --- |
| 시작 | PENDING | RUNNING |
| 정상 종료·실패·중단·대기 | RUNNING | COMPLETED / FAILED / INTERRUPTED / PAUSED |
| 재시작 복구 | PENDING / RUNNING | INTERRUPTED |

`core.models.validate_run_transition`은 허용 전이만 판단합니다. 시각·저장·Step/Conversation 변경은 서비스가 수행합니다. 종료된 Run을 다시 열지 않으며 명시적 재개는 새로운 Run을 만듭니다. 대기 요청 취소는 Message의 CANCELLED로 처리하고 새 Run을 만들지 않습니다.

`steer(run_id, text, targets=...)`는 같은 RUNNING Run에 입력을 추가하며 Run 상태 전이를 만들지 않습니다. 단독 Loop는 대상 생략이 가능하고 Graph는 `run.ainstruction_targets()`의 소비자 ID를 지정합니다. 대상별 반영 및 전체 pending/applied/unapplied/partially_applied 상태는 `run.ainstructions()`로 조회합니다. [접수·반영·재개 경계](../../docs/llm/steering.md)를 따릅니다.

미시작 Agent에는 `run.ainstruction_routes()`의 경로를 선택해 `session.run.reserve_instruction(run_id, text, targets=routes)`로 예약합니다. 경로별 다음 실행 한 번에만 결합하며 미사용 예약을 명시적 재개에 이전하지 않습니다. 적용한 예약은 기존 문맥만 복원합니다.

RunManager는 종료 상태와 오류의 조합, Session의 current_run_id를 확인합니다. 늦은 종료가 다음 Run의 소유권을 해제하거나 이미 저장된 종료를 덮어쓰지 못하게 합니다. Step 정리 → Assistant 상태 → Run → Session 기록은 기존 트랜잭션 경계를 따르고 저장 성공 후 수명 알림을 전달합니다.

## 확장할 때

- Engine은 이벤트만 발생시키고, StepEventRecorder/StepManager가 Step을 저장합니다.
- 사용자 이벤트 처리기는 기존 EventContext API를 사용합니다. 정책·출력·체크포인트 등 소유자가 정해진 메타데이터를 덮어쓰지 않습니다.
- UI 구독은 관찰용입니다. 저장소 원본과 누락 복구 경로를 유지하세요.
- Repository 교체 시 Facade 조회와 runtime이 같은 인스턴스를 사용하도록 ServiceConfig에서 구성합니다.
- 새 코드는 책임별 하위 패키지에 둡니다. 단순 폴더 분류를 새 실행 계층으로 만들지 않습니다.

[이벤트·구독 예제](runtime/README.md), [서비스 확장 계약](../../docs/llm/service-extensions.md), [공통 데이터 API](../../docs/llm/data-contracts.md)에 세부 계약이 있습니다.
